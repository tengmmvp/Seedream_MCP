"""API 结果 usage 字段异形守卫测试。

非流式 JSON 与 SSE completed 两条路径对非 dict 的 usage 收敛为空 dict，合法
dict 原样保留；网络层经 httpx.MockTransport 与伪 SSE 注入，不触达真实 API。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.io.io_sse import parse_sse_response

from _client_fakes import _FakeLog, _FakeSSEResponse, _install_mock_transport


@pytest.mark.parametrize("usage_value", ["text", 123])
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
        await _install_mock_transport(client, _handler)
        result = await client.text_to_image(prompt="p")

    assert result["success"] is True
    assert result["data"]
    assert result["usage"] == {}


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
        await _install_mock_transport(client, _handler)
        result = await client.text_to_image(prompt="p")

    assert result["usage"] == {"generated_images": 2}


def _sse_chunks(usage_json: str) -> list[bytes]:
    """构造 completed 事件携带指定 usage JSON 的两事件 SSE 字节块。"""
    return [
        b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n',
        b'data: {"type":"image_generation.completed","usage":' + usage_json.encode() + b"}\n\n",
    ]


async def _parse_sse(chunks: list[bytes]) -> dict[str, Any]:
    """以固定测试参数解析伪 SSE 响应。"""
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


async def test_stream_top_level_error_failure_keeps_usage_dict(no_sleep: None) -> None:
    """stream 请求级失败路径同样保证 usage 恒为 dict：异形 usage 收敛为空 dict。

    顶层 error 守卫的失败返回不绕过 usage 归一化。
    """
    config = SeedreamConfig(api_key="k", max_retries=1)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"error": {"code": "StreamRejected", "message": "流式请求被拒绝"}, "usage": "bad"},
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)
        result = await client.text_to_image(prompt="p", stream=True)

    assert result["success"] is False
    assert result["usage"] == {}
