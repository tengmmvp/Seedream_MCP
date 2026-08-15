"""API 结果 usage 字段异形守卫测试。

非流式 JSON 与 SSE completed 事件两条路径对非 dict 的 usage 收敛为空 dict，
结果结构 usage 恒为 dict；合法 dict 原样保留。网络层经 httpx.MockTransport 与
伪 SSE 响应注入，不触达真实 API。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.io.io_sse import parse_sse_response


class _FakeLog:
    def debug(self, *a: Any, **k: Any) -> None:
        pass

    def warning(self, *a: Any, **k: Any) -> None:
        pass

    def error(self, *a: Any, **k: Any) -> None:
        pass


class _FakeSSEResponse:
    def __init__(self, chunks: List[bytes]) -> None:
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size: int):
        del chunk_size
        for chunk in self._chunks:
            yield chunk


@pytest.mark.parametrize("usage_value", ["text", 123])
@pytest.mark.asyncio
async def test_non_stream_usage_non_dict_converged_to_empty(
    usage_value: Any, no_sleep: None
) -> None:
    """200 JSON 响应 usage 非 dict：请求正常成功，usage 收敛为空 dict。"""
    config = SeedreamConfig(api_key="k", max_retries=1)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"data": [{"url": "https://example.com/x.png"}], "usage": usage_value},
        )

    async with SeedreamClient(config) as client:
        assert client._client is not None
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
        result = await client.text_to_image(prompt="p")

    assert result["success"] is True
    assert result["data"]
    assert result["usage"] == {}


@pytest.mark.asyncio
async def test_non_stream_usage_dict_preserved(no_sleep: None) -> None:
    """合法 dict usage 原样保留，守卫不过度丢弃。"""
    config = SeedreamConfig(api_key="k", max_retries=1)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "data": [{"url": "https://example.com/x.png"}],
                "usage": {"generated_images": 2},
            },
        )

    async with SeedreamClient(config) as client:
        assert client._client is not None
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
        result = await client.text_to_image(prompt="p")

    assert result["usage"] == {"generated_images": 2}


def _sse_chunks(usage_json: str) -> List[bytes]:
    return [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        b'data: {"type":"image_generation.completed","usage":' + usage_json.encode() + b"}\n\n",
    ]


async def _parse_sse(chunks: List[bytes]) -> Dict[str, Any]:
    return await parse_sse_response(
        _FakeSSEResponse(chunks),
        model_id="m",
        chunk_size=64,
        buffer_max_size=4096,
        event_truncate_threshold=4096,
        total_bytes_limit=64 * 1024,
        log=_FakeLog(),
    )


@pytest.mark.parametrize("usage_value", ["text", 123])
async def test_sse_completed_usage_non_dict_converged_to_empty(usage_value: Any) -> None:
    """SSE completed 事件 usage 非 dict：解析正常完成，usage 收敛为空 dict。"""
    result = await _parse_sse(_sse_chunks(json.dumps(usage_value)))

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["usage"] == {}


async def test_sse_completed_usage_dict_preserved() -> None:
    """SSE completed 事件携带合法 dict usage 时原样保留。"""
    result = await _parse_sse(_sse_chunks(json.dumps({"generated_images": 1})))

    assert result["status"] == "completed"
    assert result["usage"] == {"generated_images": 1}
