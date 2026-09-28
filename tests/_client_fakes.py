"""SeedreamClient 与 SSE 解析测试共享的替身与注入辅助。

需要伪造 API 响应分块流或注入 MockTransport 的测试经此复用 _FakeLog/
_FakeSSEResponse 与注入逻辑，避免逐行重复定义造成语义漂移。伪 SSE 响应按
aiohttp/httpx 公开接口的最小子集模拟分块字节流；_parse_sse 以标准参数组合
解析伪响应，调用方仅声明偏离默认值的参数；_make_client 复用最小测试配置的
客户端构造。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, cast

import httpx

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.io.io_sse import parse_sse_response

if TYPE_CHECKING:
    from loguru import Logger


def _make_client() -> SeedreamClient:
    """以最小测试配置构造 SeedreamClient，prepare 系列测试的共享工厂。"""
    return SeedreamClient(SeedreamConfig(api_key="test_key", max_retries=1))


class _FakeLog:
    """吞掉全部日志调用的替身，供 SSE 解析路径作 logger 参数使用。"""

    def debug(self, *a: Any, **k: Any) -> None:
        del a, k

    def warning(self, *a: Any, **k: Any) -> None:
        del a, k

    def error(self, *a: Any, **k: Any) -> None:
        del a, k


class _FakeSSEResponse:
    """按预设分块序列产出字节的伪流式响应。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
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


async def _parse_sse(
    chunks: list[bytes],
    *,
    buffer_max_size: int = 4096,
    event_truncate_threshold: int = 4096,
    total_bytes_limit: int = 64 * 1024,
    log: Logger | None = None,
) -> dict[str, Any]:
    """以标准参数组合解析伪 SSE 响应，调用方仅声明偏离默认值的参数。"""
    return await parse_sse_response(
        cast(httpx.Response, _FakeSSEResponse(chunks)),
        model_id="m",
        buffer_max_size=buffer_max_size,
        event_truncate_threshold=event_truncate_threshold,
        total_bytes_limit=total_bytes_limit,
        log=cast("Logger", _FakeLog()) if log is None else log,
    )
