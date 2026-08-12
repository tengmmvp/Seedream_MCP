"""SSE 流式解析的缓冲区上限保护与事件聚合测试（A4）。"""

import pytest

from seedream_mcp.utils.errors import SeedreamAPIError
from seedream_mcp.utils.sse_parser import parse_sse_response, parse_sse_segment


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
        _FakeSSEResponse(chunks), model_id="m", chunk_size=64, buffer_max_size=4096, log=_FakeLog()
    )
    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/1.png"
    assert result["status"] == "completed"
    assert result["usage"]["generated_images"] == 1


async def test_parse_sse_response_preserves_complete_events_before_truncation() -> None:
    """缓冲区超限截断不完整尾部时，已就绪的完整事件不得丢失。"""
    complete = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    oversized_tail = b"y" * 2000  # 不完整尾部，超过 buffer_max_size
    result = await parse_sse_response(
        _FakeSSEResponse([complete + oversized_tail]),
        model_id="m",
        chunk_size=64,
        buffer_max_size=512,
        log=_FakeLog(),
    )
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "http://x/1.png"


async def test_parse_sse_response_raises_on_request_level_error() -> None:
    chunks = [b'data: {"error":{"message":"bad request","code":"x"}}\n\n']
    with pytest.raises(SeedreamAPIError, match="bad request"):
        await parse_sse_response(
            _FakeSSEResponse(chunks),
            model_id="m",
            chunk_size=64,
            buffer_max_size=4096,
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
    segment = b"event: image_generation.completed\n" b'data: {"type": "image_generation.completed"}'
    result = parse_sse_segment(segment, log=None)
    assert result is not None
    assert result["type"] == "image_generation.completed"
