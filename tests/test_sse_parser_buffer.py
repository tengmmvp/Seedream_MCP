"""SSE 流式解析的缓冲区上限保护与事件聚合测试。"""

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

import seedream_mcp.utils.io.io_sse as sse_parser_module
from seedream_mcp.utils.core.errors import SeedreamAPIError
from seedream_mcp.utils.io.io_sse import (
    is_sse_response,
    parse_sse_response,
    parse_sse_segment,
)

from _client_fakes import _FakeLog, _FakeSSEResponse


class _CapturingLog(_FakeLog):
    """捕获 debug 调用参数的日志替身，供进度与未知事件日志断言使用。"""

    def __init__(self) -> None:
        self.debug_calls: list[tuple[object, ...]] = []

    def debug(self, *a: Any, **k: Any) -> None:
        self.debug_calls.append(a)


async def test_parse_sse_response_collects_events_and_completed() -> None:
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    result = await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/1.png"
    assert result["status"] == "completed"
    assert result["usage"]["generated_images"] == 1


async def test_parse_sse_response_preserves_complete_events_before_truncation() -> None:
    """缓冲区超限截断不完整尾部时，已就绪的完整事件不得丢失。"""
    complete = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    oversized_tail = b"y" * 2000  # 不完整尾部，超过 event_truncate_threshold
    result = await parse_sse_response(
        _FakeSSEResponse([complete + oversized_tail]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=512,
        event_truncate_threshold=512,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/1.png"
    # 截断导致数据丢失时 status 标记为 partial，通知调用方结果不完整
    assert result["status"] == "partial"


async def test_parse_sse_response_raises_on_request_level_error() -> None:
    chunks = [b'data: {"error":{"message":"bad request","code":"x"}}\n\n']
    with pytest.raises(SeedreamAPIError, match="bad request"):
        await parse_sse_response(
            _FakeSSEResponse(chunks),
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
            event_truncate_threshold=4096,
            total_bytes_limit=64 * 1024,
            log=_FakeLog(),
        )


async def test_parse_sse_request_level_error_code_narrowed_to_string() -> None:
    """请求级错误事件的 code 仅非空字符串被保留，数字与空串均置 None。

    与 errors.handle_api_error 的错误码收窄口径一致，数字码转字符串属臆测语义。
    """
    for code_json, expected in [
        (b'"code":40012', None),
        (b'"code":""', None),
        (b'"code":"InvalidParameter"', "InvalidParameter"),
    ]:
        chunks = [b'data: {"error":{"message":"bad request",' + code_json + b"}}\n\n"]
        with pytest.raises(SeedreamAPIError, match="bad request") as exc_info:
            await parse_sse_response(
                _FakeSSEResponse(chunks),
                model_id="m",
                chunk_size=64,
                buffer_max_size=4096,
                event_truncate_threshold=4096,
                total_bytes_limit=64 * 1024,
                log=_FakeLog(),
            )
        assert exc_info.value.error_code == expected


async def test_parse_sse_request_level_error_message_truncated_to_8kb() -> None:
    """请求级错误事件的超长 message 拼入异常前经 8KB 截断，与 handle_api_error 同口径。

    使用同一截断辅助函数而非独立实现：截断保留前缀并标注原长度，超大错误体不随
    异常 message 进入日志。
    """
    long_message = "x" * 20000
    chunks = [b'data: {"error":{"message":"' + long_message.encode() + b'","code":"e"}}\n\n']
    with pytest.raises(SeedreamAPIError) as exc_info:
        await parse_sse_response(
            _FakeSSEResponse(chunks),
            model_id="m",
            chunk_size=64,
            buffer_max_size=64 * 1024,
            event_truncate_threshold=64 * 1024,
            total_bytes_limit=256 * 1024,
            log=_FakeLog(),
        )
    message = exc_info.value.message
    assert message.startswith("<truncated:20000 chars>")
    # 截断后总长为标注前缀 + 8KB 片段 + 省略号，远小于原始 20000 字符
    assert len(message) < 8 * 1024 + 100


async def test_parse_sse_response_logs_unknown_event_type_with_segment_size() -> None:
    """未识别 type 的事件被丢弃并记录 debug 日志，携带事件 type 与段字节规模。"""
    log = _CapturingLog()
    unknown = b'data: {"type":"image_generation.unknown_future","payload":"x"}\n\n'
    chunks = [unknown, b'data: {"type":"image_generation.completed","usage":{}}\n\n']
    result = await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=log,
    )
    assert result["data"] == []
    assert result["status"] == "completed"
    unknown_logs = [
        call for call in log.debug_calls if "image_generation.unknown_future" in str(call)
    ]
    assert unknown_logs, "未知 type 事件须记录 debug 日志"
    # 日志参数含事件段字节规模；段长不含事件分隔符 \n\n
    assert str(len(unknown) - 2) in str(unknown_logs[0])


async def test_parse_sse_response_progress_log_by_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """进度日志按字节增量阈值记录，不依赖 processed_bytes 恰好命中整 MB 取模。"""
    monkeypatch.setattr(sse_parser_module, "_SSE_PROGRESS_LOG_INTERVAL_BYTES", 100)
    log = _CapturingLog()
    # 无事件分隔符的不完整流仅累计字节，驱动进度分支
    chunk = b"z" * 60
    await parse_sse_response(
        _FakeSSEResponse([chunk, chunk, chunk, chunk]),
        model_id="m",
        chunk_size=16,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=log,
    )
    progress_logs = [call for call in log.debug_calls if "已处理" in str(call)]
    # 240 字节按 100 字节间隔至少记录两次；取模判定下 240 % 1MB 永不为 0
    assert len(progress_logs) >= 2


def test_parse_sse_segment_joins_multiline_data_into_single_json() -> None:
    """SSE 单事件跨多行 data: 时，parse_sse_segment 用 \\n 拼接为完整 JSON。

    验证逐行提取 data 值并以 \\n 拼接的多行 data 合并行为，
    确保客户端将合法 JSON 拆成两行发送时仍能正确还原对象。
    """
    segment = (
        b'data: {"type": "image_generation.completed",\n' b'data: "usage": {"generated_images": 2}}'
    )
    result = parse_sse_segment(segment, log=None)
    assert result is not None
    assert result["type"] == "image_generation.completed"
    assert result["usage"]["generated_images"] == 2


def test_parse_sse_segment_strips_single_leading_space_only() -> None:
    """data: 字段仅剥离首个前导空格：data:x 与 data: x 语义一致，多余空白属负载。

    SSE 规范规定字段值仅移除单个前导 U+0020；JSON 负载对剩余前后空白天然容忍，
    三种形态均解析为同一事件对象。
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
    """data:[DONE] 无空格形态同样识别为流结束哨兵，不进入 json 解析。"""
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


async def test_parse_sse_response_classifies_partial_failed_event() -> None:
    """partial_failed 事件归入 data 项并标记 status=partial。"""
    chunks = [
        b'data: {"type":"image_generation.partial_failed",'
        b'"error":{"code":"content_filter","message":"blocked"}}\n\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    result = await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert result["success"] is True
    # 含 error 项使 status 从 completed 降级为 partial
    assert result["status"] == "partial"
    assert len(result["data"]) == 1
    assert result["data"][0]["type"] == "image_generation.partial_failed"
    assert result["data"][0]["error"]["code"] == "content_filter"
    assert result["data"][0]["error"]["message"] == "blocked"


async def test_parse_sse_response_normalizes_crlf_line_endings() -> None:
    """CRLF 行尾被归一化为 \\n，事件分隔 \\n\\n 仍正确切分。"""
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\r\n\r\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\r\n\r\n',
    ]
    result = await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
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
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/2.png"
    assert result["status"] == "completed"


async def test_parse_sse_response_crlf_multiline_event_survives_arbitrary_split() -> None:
    """CRLF 多行 data 事件在任意分块点下完整解析，不产生假事件分隔符。

    逐字节分块穷尽全部切分点，含 \r 与 \n 的边界；块尾孤立 \r 被归一为 \n 后与
    次块首 \n 拼接曾把多行 data 事件静默拆丢。4 字节分块另覆盖常规分片形态。
    """
    line1 = b'data: {"type": "image_generation.partial_succeeded",'
    line2 = b'data:  "url":"http://x/1.png"}'
    completed = b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}'
    stream = line1 + b"\r\n" + line2 + b"\r\n\r\n" + completed + b"\r\n\r\n"
    for size in (1, 4):
        chunks = [stream[i : i + size] for i in range(0, len(stream), size)]
        result = await parse_sse_response(
            _FakeSSEResponse(chunks),
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
            event_truncate_threshold=4096,
            total_bytes_limit=64 * 1024,
            log=_FakeLog(),
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
    # 拆成 4 字节一块，模拟分片到达
    chunks = [full_event[i : i + 4] for i in range(0, len(full_event), 4)]
    result = await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert result["status"] == "completed"
    assert result["usage"]["generated_images"] == 2


async def test_parse_sse_response_offloads_large_segment_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单事件体积超过 64KB 阈值时切片与 json.loads 一并卸载到工作线程。

    小事件保持同步。大段卸载任务为 _slice_parse_segment(buffer, start, end, log)，
    段体积即 end - start；completed 小事件不得触发 to_thread。
    """
    offload_sizes: list[int] = []
    real_to_thread = sse_parser_module.asyncio.to_thread

    async def spy(func: object, *args: object, **kwargs: object) -> object:
        if func is sse_parser_module._slice_parse_segment and len(args) == 4:
            offload_sizes.append(int(args[2]) - int(args[1]))  # type: ignore[arg-type]
        return await real_to_thread(func, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sse_parser_module.asyncio, "to_thread", spy)

    big_event = json.dumps({"type": "image_generation.partial_succeeded", "b64_json": "A" * 70000})
    chunks = [
        ("data: " + big_event + "\n\n").encode(),
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    result = await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=256 * 1024,
        event_truncate_threshold=256 * 1024,
        total_bytes_limit=256 * 1024,
        log=_FakeLog(),
    )
    assert len(result["data"]) == 1
    # 仅超 64KB 的大事件经 to_thread 卸载，completed 小事件保持同步解析
    assert len(offload_sizes) == 1
    assert offload_sizes[0] > 64 * 1024


async def test_parse_sse_response_empty_stream_returns_none_status() -> None:
    """空流无任何事件：返回 success=True、空 data、status=None、tools=None。"""
    result = await parse_sse_response(
        _FakeSSEResponse([]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert result["success"] is True
    assert result["data"] == []
    assert result["status"] is None
    assert result["tools"] is None


async def test_parse_sse_response_propagates_tools_from_completed_event() -> None:
    """completed 事件携带的 tools 字段须透传到结果，与官方响应字段对齐。

    响应侧 tools 当前无下游消费者，保留透传仅为字段完整性；联网搜索用量经
    usage.tool_usage 表达。"""
    chunks = [
        b'data: {"type":"image_generation.completed",'
        b'"usage":{"generated_images":1},'
        b'"tools":[{"type":"web_search"}]}\n\n',
    ]
    result = await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert result["tools"] == [{"type": "web_search"}]


async def test_parse_sse_response_counts_truncated_events() -> None:
    """超限丢弃的 SSE 事件以 truncated_events 计数区分图片失败与事件丢弃。

    正常流无丢弃时 truncated_events 为 0；超限丢弃时计数大于 0 且 status 标记 partial。
    截断阈值与前缀回收阈值解耦：超大事件按 event_truncate_threshold 判定丢弃。
    """
    # 正常流：无事件丢弃
    normal_chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
    ]
    normal_result = await parse_sse_response(
        _FakeSSEResponse(normal_chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert normal_result["truncated_events"] == 0

    # 超限流：单个不完整事件超过截断阈值被丢弃
    complete = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    oversized_tail = b"y" * 2000  # 不完整尾部，超过 event_truncate_threshold
    truncated_result = await parse_sse_response(
        _FakeSSEResponse([complete + oversized_tail]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=512,
        event_truncate_threshold=512,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert truncated_result["truncated_events"] >= 1
    assert truncated_result["status"] == "partial"


async def test_parse_sse_response_counts_unparseable_trailing_event() -> None:
    """流末尾不完整事件解析失败时计入 truncated_events，不再静默丢失。

    与超阈值丢事件同口径：计数使 status 标记 partial 通知调用方结果不完整，并记录
    debug 日志携带丢弃字节数；残留段之前的完整事件不受影响。
    """
    log = _CapturingLog()
    chunks = [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        # 不完整 JSON 且无结尾空行：残留段含 data 负载但解析失败
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/2',
    ]
    result = await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=log,
    )
    assert len(result["data"]) == 1
    assert result["truncated_events"] == 1
    assert result["status"] == "partial"
    drop_logs = [call for call in log.debug_calls if "流末尾" in str(call)]
    assert drop_logs, "流末尾丢弃事件须记录 debug 日志"


async def test_parse_sse_response_done_sentinel_tail_not_counted_truncated() -> None:
    """[DONE] 哨兵残留（无结尾空行）与纯空白尾部不计为丢失事件。

    哨兵与空白行经解析同样返回 None，但不构成数据丢失；误计会使 completed 状态
    被降级为 partial，谎报结果不完整。
    """
    sentinel_chunks = [
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
        b"data: [DONE]",
    ]
    sentinel_result = await parse_sse_response(
        _FakeSSEResponse(sentinel_chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert sentinel_result["truncated_events"] == 0
    assert sentinel_result["status"] == "completed"

    whitespace_chunks = [
        b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n',
        b"\n  \n",
    ]
    whitespace_result = await parse_sse_response(
        _FakeSSEResponse(whitespace_chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert whitespace_result["truncated_events"] == 0
    assert whitespace_result["status"] == "completed"


async def test_parse_sse_response_large_event_not_truncated_below_file_size_threshold() -> None:
    """截断阈值对齐 auto_save 文件上限后，单张合法大图事件不被误丢。

    模拟 stream + b64_json 场景：单事件体积介于前缀回收阈值与对齐后的截断阈值之间时，
    须完整解析而非截断丢弃，守护前缀回收阈值与截断阈值解耦不被回归。
    """
    # 事件体积 8KB，大于 buffer_max_size(2KB) 但小于 event_truncate_threshold(32KB)
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
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=2048,
        event_truncate_threshold=32 * 1024,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )
    assert result["truncated_events"] == 0
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/big.png"


async def test_parse_sse_response_terminates_on_total_bytes_limit() -> None:
    """多事件滴流累计超过总量上限时终止解析、停止读取并抛出明确异常。

    单事件阈值拦不住每个事件都合法但数量无限的滴流流，累计接收字节超限即关闭
    响应并抛 SeedreamAPIError，错误消息注明响应流总量超限。
    """
    event = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    # 每块 4 个完整事件（体积均低于单事件阈值），共 8 块持续滴流
    chunk = event * 4
    total_chunks = 8
    consumed = 0

    class _CountingResponse(_FakeSSEResponse):
        async def aiter_bytes(self, chunk_size: int):
            nonlocal consumed
            del chunk_size
            for c in self._chunks:
                consumed += 1
                yield c

    response = _CountingResponse([chunk] * total_chunks)
    with pytest.raises(SeedreamAPIError, match="SSE 响应流总量超限"):
        await parse_sse_response(
            response,
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
            event_truncate_threshold=4096,
            # 上限设为两块体积，第三块接收后累计即超限
            total_bytes_limit=len(chunk) * 2,
            log=_FakeLog(),
        )

    # 超限后停止读取：实际消费的块数远小于上游供给的块数
    assert consumed < total_chunks


async def test_parse_sse_response_terminates_on_item_count_limit() -> None:
    """大量小事件超过条目数硬上限时终止解析并标记 partial，解析产物不无界累积。

    总量上限约束的是线上字节；每个事件解析为 dict 后体积放大，字节未超限时条目数
    仍可无限增长。512KB 限额按 64 字节最小事件下界派生 8192 条上限，供给 12000 个
    53 字节的事件使累计字节 636KB 但条目数先行触顶，字节上限不触发。
    """
    event = b'data: {"type":"image_generation.partial_succeeded"}\n\n'
    assert len(event) == 53
    events_per_chunk = 100
    total_chunks = 120
    consumed = 0

    class _CountingResponse(_FakeSSEResponse):
        async def aiter_bytes(self, chunk_size: int):
            nonlocal consumed
            del chunk_size
            for c in self._chunks:
                consumed += 1
                yield c

    response = _CountingResponse([event * events_per_chunk] * total_chunks)
    result = await parse_sse_response(
        response,
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        # 512KB 派生条目上限 8192；全部 12000 事件的字节总量 636KB 会触发字节上限，
        # 条目数在约 82 块处先行触顶，两道上限的触发先后由此区分
        total_bytes_limit=512 * 1024,
        log=_FakeLog(),
    )

    # 条目数封顶：已解析条目不超过上限加单块内事件数，远小于供给总数
    assert len(result["data"]) <= 8192 + events_per_chunk
    # 终止解析后停止读取：实际消费块数远小于供给
    assert consumed < total_chunks
    # 与单事件截断同口径计数并标记 partial，通知调用方结果不完整
    assert result["truncated_events"] >= 1
    assert result["status"] == "partial"


async def test_parse_sse_response_item_limit_floor_keeps_legal_batches() -> None:
    """极小总量限额下条目上限取绝对下限兜底，合法小批次不被误截。

    2048 字节限额按字节下界推导仅得 32 条上限，小于组图单请求 15 张的合法规模
    余量；绝对下限 64 兜底后 33 个事件批次完整解析，不产生截断计数。
    """
    event = b'data: {"type":"image_generation.partial_succeeded"}\n\n'
    event_count = 33
    # 33 × 53 = 1749 字节，未超 2048 字节总量限额，仅条目数维度可能触发
    assert event_count * len(event) < 2048
    result = await parse_sse_response(
        _FakeSSEResponse([event * event_count]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=2048,
        log=_FakeLog(),
    )
    assert len(result["data"]) == event_count
    assert result["truncated_events"] == 0


async def test_parse_sse_response_terminates_on_deadline() -> None:
    """逐块检查截止时间：预算耗尽后关闭响应、停止读取并抛 asyncio.TimeoutError。"""
    event = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    consumed = 0
    closed = 0

    class _DeadlineResponse(_FakeSSEResponse):
        async def aiter_bytes(self, chunk_size: int):
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
            _DeadlineResponse([event] * 200),
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
            event_truncate_threshold=4096,
            total_bytes_limit=64 * 1024,
            log=_FakeLog(),
            # 截止时间已过，首个块即触发终止
            deadline=time.monotonic() - 1.0,
        )

    # 超限后关闭响应并停止读取
    assert closed == 1
    assert consumed < 200


def _resp_with_content_type(content_type: str) -> SimpleNamespace:
    """构造仅含 content-type 头的伪响应对象。"""
    return SimpleNamespace(headers={"content-type": content_type})


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
    assert is_sse_response(_resp_with_content_type("application/json")) is False


def test_is_sse_response_rejects_missing_content_type() -> None:
    """缺省 content-type 头时返回 False，交由调用方走非流式解析。"""
    assert is_sse_response(SimpleNamespace(headers={})) is False
