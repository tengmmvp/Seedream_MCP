"""SSE 流式解析的缓冲区上限保护与事件聚合测试。"""

import json
from types import SimpleNamespace

import pytest

import seedream_mcp.utils.io.io_sse as sse_parser_module
from seedream_mcp.utils.core.errors import SeedreamAPIError
from seedream_mcp.utils.io.io_sse import (
    is_sse_response,
    parse_sse_response,
    parse_sse_segment,
)


class _FakeLog:
    def debug(self, *a, **k) -> None:
        pass

    def warning(self, *a, **k) -> None:
        pass

    def error(self, *a, **k) -> None:
        pass


class _FakeSSEResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size: int):
        del chunk_size
        for c in self._chunks:
            yield c


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
            log=_FakeLog(),
        )


def test_parse_sse_segment_joins_multiline_data_into_single_json() -> None:
    """SSE 单事件跨多行 data: 时，parse_sse_segment 用 \\n 拼接为完整 JSON。

    验证 split('\\n') + [5:].strip() + '\\n'.join 的多行 data 合并行为，
    确保客户端将合法 JSON 拆成两行发送时仍能正确还原对象。
    """
    segment = (
        b'data: {"type": "image_generation.completed",\n' b'data: "usage": {"generated_images": 2}}'
    )
    result = parse_sse_segment(segment, log=None)
    assert result is not None
    assert result["type"] == "image_generation.completed"
    assert result["usage"]["generated_images"] == 2


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
        log=_FakeLog(),
    )
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/2.png"
    assert result["status"] == "completed"


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
        log=_FakeLog(),
    )
    assert result["status"] == "completed"
    assert result["usage"]["generated_images"] == 2


async def test_parse_sse_response_offloads_large_segment_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单事件体积超过 64KB 阈值时 json.loads 须卸载到工作线程，小事件保持同步解析。"""
    offload_sizes: list[int] = []
    real_to_thread = sse_parser_module.asyncio.to_thread

    async def spy(func: object, *args: object, **kwargs: object) -> object:
        if args and isinstance(args[0], (bytes, bytearray)):
            offload_sizes.append(len(args[0]))
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
        log=_FakeLog(),
    )
    assert result["success"] is True
    assert result["data"] == []
    assert result["status"] is None
    assert result["tools"] is None


async def test_parse_sse_response_propagates_tools_from_completed_event() -> None:
    """completed 事件携带的 tools 字段须透传到结果，供联网搜索等用途下游消费。"""
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
        log=_FakeLog(),
    )
    assert truncated_result["truncated_events"] >= 1
    assert truncated_result["status"] == "partial"


async def test_parse_sse_response_large_event_not_truncated_below_file_size_threshold() -> None:
    """截断阈值对齐 auto_save 文件上限后，单张合法大图事件不被误丢。

    模拟 stream + b64_json 场景：单事件体积介于前缀回收阈值与对齐后的截断阈值之间时，
    须完整解析而非截断丢弃，回归保护 #2 的阈值解耦修复。
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
        log=_FakeLog(),
    )
    assert result["truncated_events"] == 0
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/big.png"


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
