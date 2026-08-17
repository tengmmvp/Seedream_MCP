"""SeedreamClient 与 SSE 解析测试共享的替身与注入辅助。

供 test_response_body_limit、test_usage_shape_guards 与 test_sse_parser_buffer
复用，避免多处逐行重复定义 _FakeLog/_FakeSSEResponse 与 MockTransport 注入逻辑
造成语义漂移。伪 SSE 响应按 aiohttp/httpx 公开接口的最小子集模拟分块字节流。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable, List

import httpx

from seedream_mcp.client import SeedreamClient


class _FakeLog:
    """吞掉全部日志调用的替身，SSE 解析路径的 logger 参数使用。"""

    def debug(self, *a: Any, **k: Any) -> None:
        del a, k

    def warning(self, *a: Any, **k: Any) -> None:
        del a, k

    def error(self, *a: Any, **k: Any) -> None:
        del a, k


class _FakeSSEResponse:
    """按预设分块序列产出字节的伪流式响应，忽略 chunk_size。"""

    def __init__(self, chunks: List[bytes]) -> None:
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        for chunk in self._chunks:
            yield chunk


async def _install_mock_transport(client: SeedreamClient, handler: Callable[[Any], Any]) -> None:
    """关闭内部 httpx 客户端并替换为 MockTransport 驱动的实例。"""
    assert client._client is not None
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
