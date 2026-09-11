"""SSE 流式解析的缓冲区上限保护与事件聚合测试。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

import seedream_mcp.utils.io.io_sse as sse_parser_module
from seedream_mcp.utils.core.errors import SeedreamAPIError
from seedream_mcp.utils.io.io_sse import (
    is_sse_response,
    parse_sse_response,
    parse_sse_segment,
)

from _client_fakes import _FakeLog, _FakeSSEResponse

if TYPE_CHECKING:
    from loguru import Logger


class _CapturingLog(_FakeLog):
    """捕获 debug 与 warning 调用参数的日志替身，供进度与事件丢弃日志断言使用。"""

    def __init__(self) -> None:
        self.debug_calls: list[tuple[object, ...]] = []
        self.warning_calls: list[tuple[object, ...]] = []

    def debug(self, *a: Any, **k: Any) -> None:
        self.debug_calls.append(a)

    def warning(self, *a: Any, **k: Any) -> None:
        self.warning_calls.append(a)


def _sse_response(chunks: list[bytes]) -> httpx.Response:
    """构造伪流式响应，以 httpx.Response 类型供解析器调用。"""
    return cast(httpx.Response, _FakeSSEResponse(chunks))


def _log() -> Logger:
    """构造吞日志替身，以 Logger 类型供解析器调用。"""
    return cast("Logger", _FakeLog())


async def test_parse_sse_response_collects_events_and_completed() -> None:
    """完整流解析出 data 事件、completed 状态与 usage，chunk_size 原样传抵读取入口。"""
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    response = _FakeSSEResponse(chunks)
    result = await parse_sse_response(
        cast(httpx.Response, response),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert response.observed_chunk_size == 64
    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/1.png"
    assert result["status"] == "completed"
    assert result["usage"]["generated_images"] == 1


async def test_parse_sse_response_preserves_complete_events_before_truncation() -> None:
    """缓冲区超限截断不完整尾部时，已就绪的完整事件不得丢失。"""
    complete = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    oversized_tail = b"y" * 2000  # 不完整尾部，超过 event_truncate_threshold。
    result = await parse_sse_response(
        _sse_response([complete + oversized_tail]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=512,
        event_truncate_threshold=512,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/1.png"
    # 截断导致数据丢失时 status 标记为 partial，通知调用方结果不完整。
    assert result["status"] == "partial"


async def test_parse_sse_response_raises_on_request_level_error() -> None:
    """零产出时请求级错误事件抛 SeedreamAPIError，携带上游 message。"""
    chunks = [b'data: {"error":{"message":"bad request","code":"x"}}\n\n']
    with pytest.raises(SeedreamAPIError, match="bad request"):
        await parse_sse_response(
            _sse_response(chunks),
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
            event_truncate_threshold=4096,
            total_bytes_limit=64 * 1024,
            log=_log(),
        )


async def test_parse_sse_request_level_error_keeps_collected_items() -> None:
    """已有产出后到达的请求级错误保留部分结果，与非流式部分成功口径一致。"""
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        b'data: {"error":{"message":"late failure","code":"x"}}\n\n',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    urls = [item["url"] for item in result["data"] if "url" in item]
    assert urls == ["http://x/1.png"]
    assert any("error" in item for item in result["data"])
    assert result["status"] == "partial"


async def test_parse_sse_request_level_error_code_narrowed_to_string() -> None:
    """请求级错误事件的 code 仅非空字符串被保留，数字与空串均置 None。

    与 errors.handle_api_error 的收窄口径一致。
    """
    for code_json, expected in [
        (b'"code":40012', None),
        (b'"code":""', None),
        (b'"code":"InvalidParameter"', "InvalidParameter"),
    ]:
        chunks = [b'data: {"error":{"message":"bad request",' + code_json + b"}}\n\n"]
        with pytest.raises(SeedreamAPIError, match="bad request") as exc_info:
            await parse_sse_response(
                _sse_response(chunks),
                model_id="m",
                chunk_size=64,
                buffer_max_size=4096,
                event_truncate_threshold=4096,
                total_bytes_limit=64 * 1024,
                log=_log(),
            )
        assert exc_info.value.error_code == expected


async def test_parse_sse_request_level_error_message_truncated_to_8kb() -> None:
    """请求级错误事件的超长 message 拼入异常前经 8KB 截断，与 handle_api_error 同口径。"""
    long_message = "x" * 20000
    chunks = [b'data: {"error":{"message":"' + long_message.encode() + b'","code":"e"}}\n\n']
    with pytest.raises(SeedreamAPIError) as exc_info:
        await parse_sse_response(
            _sse_response(chunks),
            model_id="m",
            chunk_size=64,
            buffer_max_size=64 * 1024,
            event_truncate_threshold=64 * 1024,
            total_bytes_limit=256 * 1024,
            log=_log(),
        )
    message = exc_info.value.message
    assert message.startswith("<truncated:20000 chars>")
    # 截断后总长为标注前缀 + 8KB 片段 + 省略号，远小于原始 20000 字符。
    assert len(message) < 8 * 1024 + 100


async def test_parse_sse_response_logs_unknown_event_type_with_segment_size() -> None:
    """未识别 type 的事件被丢弃并记录 debug 日志，携带事件 type 与段字节规模。"""
    log = _CapturingLog()
    unknown = b'data: {"type":"image_generation.unknown_future","payload":"x"}\n\n'
    chunks = [unknown, b'data: {"type":"image_generation.completed","usage":{}}\n\n']
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=cast("Logger", log),
    )
    assert result["data"] == []
    assert result["status"] == "completed"
    unknown_logs = [
        call for call in log.debug_calls if "image_generation.unknown_future" in str(call)
    ]
    assert unknown_logs, "未知 type 事件须记录 debug 日志"
    # 日志参数含事件段字节规模；段长不含事件分隔符 \n\n。
    assert str(len(unknown) - 2) in str(unknown_logs[0])


async def test_parse_sse_response_progress_log_by_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """进度日志按字节增量阈值记录，不依赖 processed_bytes 恰好命中整 MB 取模。"""
    monkeypatch.setattr(sse_parser_module, "_SSE_PROGRESS_LOG_INTERVAL_BYTES", 100)
    log = _CapturingLog()
    # 无事件分隔符的不完整流仅累计字节，驱动进度分支。
    chunk = b"z" * 60
    await parse_sse_response(
        _sse_response([chunk, chunk, chunk, chunk]),
        model_id="m",
        chunk_size=16,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=cast("Logger", log),
    )
    progress_logs = [call for call in log.debug_calls if "已处理" in str(call)]
    # 240 字节按 100 字节间隔至少记录两次；取模判定下 240 % 1MB 永不为 0。
    assert len(progress_logs) >= 2


def test_parse_sse_segment_joins_multiline_data_into_single_json() -> None:
    """SSE 单事件跨多行 data: 时以 \\n 拼接为完整 JSON，客户端拆行发送仍可还原。"""
    segment = (
        b'data: {"type": "image_generation.completed",\n' b'data: "usage": {"generated_images": 2}}'
    )
    result = parse_sse_segment(segment, log=None)
    assert result is not None
    assert result["type"] == "image_generation.completed"
    assert result["usage"]["generated_images"] == 2


def test_parse_sse_segment_strips_single_leading_space_only() -> None:
    """data: 字段仅剥离首个前导空格：data:x 与 data: x 语义一致，多余空白属负载。

    SSE 规范仅移除单个前导 U+0020，剩余空白由 JSON 解析容忍。
    """
    completed = b'{"type":"image_generation.completed","usage":{}}'
    for segment in (
        b"data:" + completed,
        b"data: " + completed,
        b"data:  " + completed,
        b"data: " + completed + b"  ",
    ):
        result = parse_sse_segment(segment, log=None)
        assert result is not None, f"形态 {segment!r} 应解析成功"
        assert result["type"] == "image_generation.completed"


def test_parse_sse_segment_done_marker_without_space_still_recognized() -> None:
    """data:[DONE] 无空格形态同样识别为流结束哨兵，不进入 JSON 解析。"""
    assert parse_sse_segment(b"data: [DONE]", log=None) is None
    assert parse_sse_segment(b"data:[DONE]", log=None) is None


def test_parse_sse_segment_returns_none_for_done_marker() -> None:
    """[DONE] 标记不返回事件对象。"""
    assert parse_sse_segment(b"data: [DONE]", log=None) is None


def test_parse_sse_segment_skips_non_data_lines() -> None:
    """event: / id: 等非 data 行被忽略，仅合并 data 行。"""
    segment = b"event: image_generation.completed\n" b'data: {"type":"image_generation.completed"}'
    result = parse_sse_segment(segment, log=None)
    assert result is not None
    assert result["type"] == "image_generation.completed"


def test_parse_sse_segment_deeply_nested_payload_dropped_without_raising() -> None:
    """约 100KB 纯嵌套括号负载在 json.loads 内抛 RecursionError，按解析失败丢弃。

    RecursionError 是 RuntimeError 子类而非 ValueError，未显式列入 except 元组时
    会作为未分类异常逃出解析器，使流式请求整体失败且重试同样命中。
    """
    segment = b"data: " + b"[" * 50000 + b"]" * 50000
    assert parse_sse_segment(segment, log=None) is None


async def test_parse_sse_response_classifies_partial_failed_event() -> None:
    """partial_failed 事件归入 data 项并标记 status=partial。"""
    chunks = [
        b'data: {"type":"image_generation.partial_failed",'
        b'"error":{"code":"content_filter","message":"blocked"}}\n\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert result["success"] is True
    # 含 error 项使 status 从 completed 降级为 partial。
    assert result["status"] == "partial"
    assert len(result["data"]) == 1
    assert result["data"][0]["type"] == "image_generation.partial_failed"
    assert result["data"][0]["error"]["code"] == "content_filter"
    assert result["data"][0]["error"]["message"] == "blocked"


async def test_parse_sse_response_strips_leading_bom() -> None:
    """流首 UTF-8 BOM 紧贴首个 data: 行时被一次性剥离，首事件完整解析不丢失。"""
    chunks = [
        b'\xef\xbb\xbfdata: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/1.png"
    assert result["status"] == "completed"
    assert result["truncated_events"] == 0


async def test_parse_sse_response_normalizes_crlf_line_endings() -> None:
    """CRLF 行尾被归一化为 \\n，事件分隔 \\n\\n 仍正确切分。"""
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\r\n\r\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\r\n\r\n',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/1.png"
    assert result["status"] == "completed"


async def test_parse_sse_response_normalizes_cr_line_endings() -> None:
    """纯 CR 行尾被归一化为 \\n，事件仍可正确切分。"""
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/2.png"}\r\r',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\r\r',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/2.png"
    assert result["status"] == "completed"


async def test_parse_sse_response_crlf_multiline_event_survives_arbitrary_split() -> None:
    """CRLF 多行 data 事件在任意分块点下完整解析，不产生假事件分隔符。

    逐字节分块穷尽全部切分点；块尾孤立 \r 与次块首 \n 拼出假分隔符曾拆丢多行
    事件。4 字节分块另覆盖常规分片形态。
    """
    line1 = b'data: {"type": "image_generation.partial_succeeded",'
    line2 = b'data:  "url":"http://x/1.png"}'
    completed = b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}'
    stream = line1 + b"\r\n" + line2 + b"\r\n\r\n" + completed + b"\r\n\r\n"
    for size in (1, 4):
        chunks = [stream[i : i + size] for i in range(0, len(stream), size)]
        result = await parse_sse_response(
            _sse_response(chunks),
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
            event_truncate_threshold=4096,
            total_bytes_limit=64 * 1024,
            log=_log(),
        )
        assert len(result["data"]) == 1, f"分块大小 {size} 下事件不得丢失"
        assert result["data"][0]["url"] == "http://x/1.png"
        assert result["status"] == "completed"
        assert result["truncated_events"] == 0


async def test_parse_sse_response_reassembles_event_across_chunks() -> None:
    """单个事件跨多 chunk 到达时，缓冲区累积后仍能完整解析。"""
    full_event = (
        b'data: {"type": "image_generation.completed",' b'"usage":{"generated_images":2}}\n\n'
    )
    # 拆成 4 字节一块，模拟分片到达。
    chunks = [full_event[i : i + 4] for i in range(0, len(full_event), 4)]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert result["status"] == "completed"
    assert result["usage"]["generated_images"] == 2


async def test_parse_sse_response_offloads_large_segment_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超 64KB 的大事件把切片与 json.loads 卸载到工作线程，小事件保持同步。

    卸载任务为 _slice_parse_segment(buffer, start, end, log)，段体积即 end - start。
    """
    offload_sizes: list[int] = []
    real_to_thread = asyncio.to_thread

    async def spy(func: Any, *args: Any, **kwargs: Any) -> Any:
        if func is sse_parser_module._slice_parse_segment and len(args) == 4:
            offload_sizes.append(int(args[2]) - int(args[1]))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)

    big_event = json.dumps({"type": "image_generation.partial_succeeded", "b64_json": "A" * 70000})
    chunks = [
        ("data: " + big_event + "\n\n").encode(),
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=256 * 1024,
        event_truncate_threshold=256 * 1024,
        total_bytes_limit=256 * 1024,
        log=_log(),
    )
    assert len(result["data"]) == 1
    assert len(offload_sizes) == 1
    assert offload_sizes[0] > 64 * 1024


async def test_parse_sse_response_offloads_large_tail_lost_payload_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超阈值流末尾的丢失负载判定随解析一并卸载工作线程，不在事件循环上扫描。

    丢失判定对大尾部做全量按行扫描，留在事件循环上会与大尾部解析的卸载目的
    相悖；以判定调用所在线程 id 断言其已离开主线程。
    """
    main_thread = threading.get_ident()
    scan_thread_ids: list[int] = []
    real_has_lost = sse_parser_module._has_lost_data_payload

    def _tracking_has_lost(tail: Any) -> bool:
        scan_thread_ids.append(threading.get_ident())
        return real_has_lost(tail)

    monkeypatch.setattr(sse_parser_module, "_has_lost_data_payload", _tracking_has_lost)

    big_tail = b"data: " + b"x" * 70000
    result = await parse_sse_response(
        _sse_response([big_tail]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=256 * 1024,
        event_truncate_threshold=256 * 1024,
        total_bytes_limit=256 * 1024,
        log=_log(),
    )

    assert result["truncated_events"] == 1
    assert scan_thread_ids, "丢失负载判定须被执行"
    assert all(tid != main_thread for tid in scan_thread_ids), "判定不得留在事件循环线程"


async def test_parse_sse_response_offloads_large_tail_slicing_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超阈值流末尾的尾部切片随解析与丢失判定一并卸载工作线程。

    尾部切片是一次整段 memcpy，留在事件循环与大事件卸载目的相悖；以切片调用
    所在线程 id 断言其已离开主线程。
    """
    main_thread = threading.get_ident()
    slice_thread_ids: list[int] = []
    real_slice = sse_parser_module._slice_parse_tail

    def _tracking_slice(buffer: Any, start: int, log: Any) -> Any:
        slice_thread_ids.append(threading.get_ident())
        return real_slice(buffer, start, log)

    monkeypatch.setattr(sse_parser_module, "_slice_parse_tail", _tracking_slice)

    big_tail = b"data: " + b"x" * 70000
    result = await parse_sse_response(
        _sse_response([big_tail]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=256 * 1024,
        event_truncate_threshold=256 * 1024,
        total_bytes_limit=256 * 1024,
        log=_log(),
    )

    assert result["truncated_events"] == 1
    assert slice_thread_ids, "大尾部切片须卸载线程"
    assert all(tid != main_thread for tid in slice_thread_ids), "尾部切片不得留在事件循环线程"


async def test_parse_sse_response_small_tail_stays_synchronous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """小尾部保持同步快路径，不为小切片付线程调度开销。"""
    offloaded: list[object] = []
    real_to_thread = asyncio.to_thread

    async def spy(func: Any, *args: Any, **kwargs: Any) -> Any:
        if func is sse_parser_module._slice_parse_tail:
            offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)

    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        # 小残留段：含 data 负载但解析失败，计入截断。
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/2',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )

    assert result["truncated_events"] == 1
    assert not offloaded, "小尾部不得卸载线程"


async def test_frame_buffer_parse_tail_returns_length_of_sliced_tail() -> None:
    """parse_tail 返回的尾部长度由切出的尾部本体推导，与解析共用同一份字节。"""
    frames = sse_parser_module._SSEFrameBuffer()
    complete = b'data: {"type":"image_generation.completed"}\n\n'
    trailing = b'data: {"type":"image_generation.partial_succeeded"'
    frames.extend(complete + trailing)
    for _start, _end in frames.drain():
        pass

    event, _lost, tail_length = await frames.parse_tail(_log())

    assert event is None
    assert tail_length == len(trailing)


async def test_parse_sse_response_empty_stream_returns_none_status() -> None:
    """空流无任何事件：返回 success=True、空 data、status=None、tools=None。"""
    result = await parse_sse_response(
        _sse_response([]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert result["success"] is True
    assert result["data"] == []
    assert result["status"] is None
    assert result["tools"] is None


async def test_parse_sse_response_propagates_tools_from_completed_event() -> None:
    """completed 事件携带的 tools 字段须透传到结果，与官方响应字段对齐。

    响应侧 tools 当前无下游消费者，保留透传仅为字段完整性。
    """
    chunks = [
        b'data: {"type":"image_generation.completed",'
        b'"usage":{"generated_images":1},'
        b'"tools":[{"type":"web_search"}]}\n\n',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert result["tools"] == [{"type": "web_search"}]


async def test_parse_sse_response_counts_truncated_events() -> None:
    """超限丢弃的 SSE 事件以 truncated_events 计数，正常流为 0，丢弃时标记 partial。"""
    # 正常流：无事件丢弃。
    normal_chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    normal_result = await parse_sse_response(
        _sse_response(normal_chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert normal_result["truncated_events"] == 0

    # 超限流：单个不完整事件超过截断阈值被丢弃。
    complete = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    oversized_tail = b"y" * 2000  # 不完整尾部，超过 event_truncate_threshold。
    truncated_result = await parse_sse_response(
        _sse_response([complete + oversized_tail]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=512,
        event_truncate_threshold=512,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert truncated_result["truncated_events"] == 1
    assert truncated_result["status"] == "partial"


async def test_parse_sse_response_counts_unparseable_trailing_event() -> None:
    """流末尾不完整事件解析失败时计入 truncated_events，不再静默丢失。

    与超阈值丢弃同口径标记 partial 并记录 warning 日志，残留段之前的完整事件不受影响。
    """
    log = _CapturingLog()
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        # 不完整 JSON 且无结尾空行：残留段含 data 负载但解析失败。
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/2',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=cast("Logger", log),
    )
    assert len(result["data"]) == 1
    assert result["truncated_events"] == 1
    assert result["status"] == "partial"
    drop_logs = [call for call in log.warning_calls if "流末尾" in str(call)]
    assert drop_logs, "流末尾丢弃事件须记录 warning 日志"
    # 丢弃字节数取残留段本体长度，非另路推导的缓冲差值。
    assert str(len(chunks[1])) in str(drop_logs[0])


async def test_parse_sse_response_counts_deeply_nested_trailing_event() -> None:
    """深嵌套括号的流末尾残留段按解析失败丢弃并计入截断计数，不作为异常逃出。"""
    deep = b"data: " + b"[" * 50000 + b"]" * 50000
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        deep,
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=256 * 1024,
        event_truncate_threshold=256 * 1024,
        total_bytes_limit=256 * 1024,
        log=_log(),
    )
    assert len(result["data"]) == 1
    assert result["truncated_events"] == 1
    assert result["status"] == "partial"


async def test_parse_sse_response_done_sentinel_tail_not_counted_truncated() -> None:
    """无结尾空行的 [DONE] 哨兵与纯空白尾部不计为丢失事件。

    哨兵与空白行解析返回 None 但不构成数据丢失，误计会把 completed 谎报为 partial。
    """
    sentinel_chunks = [
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
        b"data: [DONE]",
    ]
    sentinel_result = await parse_sse_response(
        _sse_response(sentinel_chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert sentinel_result["truncated_events"] == 0
    assert sentinel_result["status"] == "completed"

    whitespace_chunks = [
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
        b"\n  \n",
    ]
    whitespace_result = await parse_sse_response(
        _sse_response(whitespace_chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert whitespace_result["truncated_events"] == 0
    assert whitespace_result["status"] == "completed"


async def test_parse_sse_response_large_event_not_truncated_below_file_size_threshold() -> None:
    """单事件体积介于前缀回收阈值与截断阈值之间时须完整解析，不被误丢。

    模拟 stream + b64_json 场景，守护截断阈值对齐 auto_save 文件上限后与回收阈值
    解耦不被回归。
    """
    # 事件体积 8KB，大于 buffer_max_size 2KB 但小于 event_truncate_threshold 32KB。
    big_payload = "B" * 8000
    big_event = json.dumps(
        {
            "type": "image_generation.partial_succeeded",
            "url": "http://x/big.png",
            "b64_json": big_payload,
        }
    )
    chunks = [
        ("data: " + big_event + "\n\n").encode(),
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    result = await parse_sse_response(
        _sse_response(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=2048,
        event_truncate_threshold=32 * 1024,
        total_bytes_limit=64 * 1024,
        log=_log(),
    )
    assert result["truncated_events"] == 0
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/big.png"


async def test_parse_sse_response_terminates_on_total_bytes_limit() -> None:
    """多事件滴流累计超过总量上限时终止解析、停止读取并抛 SeedreamAPIError。"""
    event = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    # 每块 4 个完整事件、共 8 块持续滴流，单事件体积均低于单事件阈值。
    chunk = event * 4
    total_chunks = 8
    consumed = 0

    class _CountingResponse(_FakeSSEResponse):
        async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
            nonlocal consumed
            del chunk_size
            for c in self._chunks:
                consumed += 1
                yield c

    response = _CountingResponse([chunk] * total_chunks)
    with pytest.raises(SeedreamAPIError, match="SSE 响应流总量超限"):
        await parse_sse_response(
            cast(httpx.Response, response),
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
            event_truncate_threshold=4096,
            # 上限设为两块体积，第三块接收后累计即超限。
            total_bytes_limit=len(chunk) * 2,
            log=_log(),
        )

    # 超限后停止读取：实际消费块数远小于上游供给。
    assert consumed < total_chunks


async def test_parse_sse_response_terminates_on_item_count_limit() -> None:
    """大量小事件字节未超限仍以条目数硬上限终止解析并标记 partial。

    512KB 限额按单条解析产物内存开销 448 字节派生 1170 条上限；供给 12000 个
    53 字节事件共 636KB 线上字节，条目数在字节上限前先行触顶。
    """
    event = b'data: {"type":"image_generation.partial_succeeded"}\n\n'
    assert len(event) == 53
    events_per_chunk = 100
    total_chunks = 120
    consumed = 0

    class _CountingResponse(_FakeSSEResponse):
        async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
            nonlocal consumed
            del chunk_size
            for c in self._chunks:
                consumed += 1
                yield c

    response = _CountingResponse([event * events_per_chunk] * total_chunks)
    result = await parse_sse_response(
        cast(httpx.Response, response),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        # 条目数在约 12 块处先行触顶，字节上限不触发。
        total_bytes_limit=512 * 1024,
        log=_log(),
    )

    # 条目数封顶：触顶判定以事件为粒度即时生效，已解析条目恰好等于上限。
    assert len(result["data"]) == 512 * 1024 // sse_parser_module._SSE_ITEM_OVERHEAD_BYTES
    # 终止解析后停止读取：实际消费块数远小于供给。
    assert consumed < total_chunks
    # 与单事件截断同口径计数并标记 partial。
    assert result["truncated_events"] == 1
    assert result["status"] == "partial"


async def test_parse_sse_response_item_limit_floor_keeps_legal_batches() -> None:
    """极小总量限额下条目上限取绝对下限兜底，合法小批次不被误截。

    2048 字节限额按单条产物内存开销推导仅得 4 条上限，低于绝对下限 64；兜底后
    33 个事件批次完整解析，不产生截断计数。
    """
    event = b'data: {"type":"image_generation.partial_succeeded"}\n\n'
    event_count = 33
    # 33 × 53 = 1749 字节，未超 2048 字节总量限额，仅条目数维度可能触发。
    assert event_count * len(event) < 2048
    result = await parse_sse_response(
        _sse_response([event * event_count]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=2048,
        log=_log(),
    )
    assert len(result["data"]) == event_count
    assert result["truncated_events"] == 0


async def test_parse_sse_response_terminates_on_deadline() -> None:
    """逐块检查截止时间：预算耗尽后关闭响应、停止读取并抛 asyncio.TimeoutError。"""
    event = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    consumed = 0
    closed = 0

    class _DeadlineResponse(_FakeSSEResponse):
        async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
            nonlocal consumed
            del chunk_size
            for c in self._chunks:
                consumed += 1
                await asyncio.sleep(0.05)
                yield c

        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

    with pytest.raises(asyncio.TimeoutError, match="总时长预算"):
        await parse_sse_response(
            cast(httpx.Response, _DeadlineResponse([event] * 200)),
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
            event_truncate_threshold=4096,
            total_bytes_limit=64 * 1024,
            log=_log(),
            # 截止时间已过，首个块即触发终止。
            deadline=time.monotonic() - 1.0,
        )

    # 预算耗尽后关闭响应并停止读取。
    assert closed == 1
    assert consumed < 200


async def test_parse_sse_response_deadline_keeps_collected_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预算耗尽时已收完整事件是确定性产出，保留为部分结果且不再抛超时。"""

    def _advancing_monotonic() -> float:
        # 首块消费前时间在预算内，次块到达时已超限，超时检查先于其入缓冲。
        return 0.0 if consumed <= 1 else 100.0

    monkeypatch.setattr(time, "monotonic", _advancing_monotonic)
    first = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    second = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/2.png"}\n\n'
    consumed = 0

    class _CountingResponse(_FakeSSEResponse):
        async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
            nonlocal consumed
            del chunk_size
            for c in self._chunks:
                consumed += 1
                yield c

    result = await parse_sse_response(
        cast(httpx.Response, _CountingResponse([first, second])),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_log(),
        deadline=50.0,
    )

    assert result["deadline_exceeded"] is True
    assert result["status"] == "partial"
    # 超时检查先于次块入缓冲，产出为首块的确定性事件
    assert [item["url"] for item in result["data"]] == ["http://x/1.png"]
    assert consumed == 2


def _resp_with_content_type(content_type: str) -> httpx.Response:
    """构造仅含 content-type 头的伪响应对象。"""
    return cast(httpx.Response, SimpleNamespace(headers={"content-type": content_type}))


def test_is_sse_response_matches_plain_media_type() -> None:
    """纯净 text/event-stream 判定为 SSE。"""
    assert is_sse_response(_resp_with_content_type("text/event-stream")) is True


def test_is_sse_response_strips_charset_parameter() -> None:
    """带 charset 参数的 Content-Type 经分号剥离后仍判定为 SSE。"""
    assert is_sse_response(_resp_with_content_type("text/event-stream; charset=utf-8")) is True


def test_is_sse_response_strips_leading_whitespace() -> None:
    """含前导空白的非法 Content-Type 经 strip 后仍判定为 SSE，避免误判降级。"""
    assert is_sse_response(_resp_with_content_type(" text/event-stream")) is True


def test_is_sse_response_rejects_non_sse_media_type() -> None:
    """非 SSE media type 判定为非流式响应。"""
    assert is_sse_response(_resp_with_content_type("application/json")) is False


def test_is_sse_response_rejects_prefixed_lookalike_media_type() -> None:
    """text/event-stream-evil 等前缀仿冒 media type 经严格等值判定拒绝。"""
    assert is_sse_response(_resp_with_content_type("text/event-stream-evil")) is False


def test_is_sse_response_rejects_missing_content_type() -> None:
    """缺省 content-type 头时返回 False，交由调用方走非流式解析。"""
    assert is_sse_response(cast(httpx.Response, SimpleNamespace(headers={}))) is False


async def test_next_stream_chunk_prefers_ready_eof_over_expired_deadline() -> None:
    """已就绪的流结束优先于超时判定，防止已完成的响应被误判超时触发重试。"""

    async def _single_chunk() -> AsyncIterator[bytes]:
        yield b"data"

    iterator = _single_chunk().__aiter__()
    await anext(iterator)

    outcome = await sse_parser_module.next_stream_chunk(iterator, time.monotonic() - 1)
    assert outcome is sse_parser_module.STREAM_ENDED


async def test_next_stream_chunk_reports_deadline_on_pending_wait() -> None:
    """无就绪数据且截止已过时如实报超时。"""

    async def _slow() -> AsyncIterator[bytes]:
        await asyncio.sleep(0.3)
        yield b"late"

    iterator = _slow().__aiter__()
    outcome = await sse_parser_module.next_stream_chunk(iterator, time.monotonic() - 0.1)
    assert outcome is sse_parser_module.STREAM_DEADLINE_HIT
