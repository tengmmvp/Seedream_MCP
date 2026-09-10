"""SeedreamClient 与 SSE 解析测试共享的替身与注入辅助。

需要伪造 API 响应分块流或注入 MockTransport 的测试经此复用 _FakeLog/
_FakeSSEResponse 与注入逻辑，避免逐行重复定义造成语义漂移。伪 SSE 响应按
aiohttp/httpx 公开接口的最小子集模拟分块字节流。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

import httpx

from seedream_mcp.client import SeedreamClient


class _FakeLog:
    """吞掉全部日志调用的替身，供 SSE 解析路径作 logger 参数使用。"""

    def debug(self, *a: Any, **k: Any) -> None:
        del a, k

    def warning(self, *a: Any, **k: Any) -> None:
        del a, k

    def error(self, *a: Any, **k: Any) -> None:
        del a, k


class _FakeSSEResponse:
    """按预设分块序列产出字节的伪流式响应，记录 aiter_bytes 收到的 chunk_size。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.observed_chunk_size: int | None = None

    async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        self.observed_chunk_size = chunk_size
        for chunk in self._chunks:
            yield chunk


async def _install_mock_transport(client: SeedreamClient, handler: Callable[[Any], Any]) -> None:
    """替换内部 httpx 客户端为 MockTransport 驱动的实例，已有客户端先关闭。

    Args:
        client: 待替换内部 httpx 客户端的 SeedreamClient。
        handler: MockTransport 的请求处理函数。
    """
    if client._client is not None:
        await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
