"""SeedreamClient 与 SSE 解析测试共享的替身与注入辅助。

需要伪造 API 响应分块流、_send_request、捕获 _call_api 请求数据或注入
MockTransport 的测试经此复用 _make_send_request_fake、_capture_call_api、
_FakeLog/_FakeSSEResponse 与注入逻辑，避免逐行重复定义造成语义漂移。
伪 SSE 响应按 httpx 公开接口的最小子集模拟分块字节流。_parse_sse 以标准参数组合
解析伪响应，调用方仅声明偏离默认值的参数。_partial_image_event 生成渐进帧事件，
b64 负载长度由调用方指定。_make_client 复用最小测试配置的客户端构造。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, cast

import httpx

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.io.sse import parse_sse_response
from seedream_mcp.utils.model.capabilities import max_per_request_image_count

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


def _partial_image_event(payload_chars: int) -> bytes:
    """构造 b64 负载为 payload_chars 个字符的渐进帧事件。"""
    return (
        b'data: {"type":"image_generation.partial_image","partial_image_index":0,"b64_json":"'
        + b"A" * payload_chars
        + b'"}\n\n'
    )


async def _install_mock_transport(client: SeedreamClient, handler: Callable[[Any], Any]) -> None:
    """替换内部 httpx 客户端为 MockTransport 驱动的实例，已有客户端先关闭。

    Args:
        client: 待替换内部 httpx 客户端的 SeedreamClient。
        handler: MockTransport 的请求处理函数。
    """
    if client._client is not None:
        await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


_ResponseHandler = Callable[[httpx.Response, float, int], Awaitable[dict[str, Any]]]
_SendOutcome = Callable[[int], dict[str, Any] | BaseException]

# fake_send 缺省与调用方显式注入共用的成功载荷，调用方只读。
_SUCCESS_RESULT: dict[str, Any] = {
    "success": True,
    "data": [],
    "usage": {},
    "status": "completed",
}


def _make_send_request_fake(
    outcome: _SendOutcome | dict[str, Any] | None = None,
    *,
    bodies: list[bytes] | None = None,
    budgets: list[float] | None = None,
    limits: list[int] | None = None,
    callbacks: list[_ResponseHandler] | None = None,
    yield_before_return: bool = False,
) -> tuple[Callable[..., Awaitable[dict[str, Any]]], Callable[[], int]]:
    """构造 _send_request 替身与调用计数读取器，关键字签名与真实方法单点对齐。

    self 带缺省值以兼容类级（自动绑实例）与实例级注入。调用序号从 1 起。
    outcome 为 callable 时按序号取结果，BaseException 即抛出，
    为 dict 时固定返回该对象，缺省返回成功空载荷。
    bodies/budgets/limits/callbacks 传入列表时逐次记录实参。
    实参依次为 request_body/attempt_timeout_seconds/byte_limit/handle_response。
    yield_before_return 在取 outcome 前让出事件循环，模拟同批请求在途重叠。
    """
    outcome = _SUCCESS_RESULT if outcome is None else outcome
    calls = 0

    async def fake_send(
        self: Any = None,
        *,
        client: httpx.AsyncClient,
        url: str,
        request_body: bytes,
        request_timeout: httpx.Timeout,
        attempt_timeout_seconds: float,
        byte_limit: int,
        handle_response: _ResponseHandler,
    ) -> dict[str, Any]:
        nonlocal calls
        del self, client, url, request_timeout
        calls += 1
        if bodies is not None:
            bodies.append(request_body)
        if budgets is not None:
            budgets.append(attempt_timeout_seconds)
        if limits is not None:
            limits.append(byte_limit)
        if callbacks is not None:
            callbacks.append(handle_response)
        if yield_before_return:
            await asyncio.sleep(0)
        value: dict[str, Any] | BaseException
        if callable(outcome):
            value = outcome(calls)
        else:
            value = outcome
        if isinstance(value, BaseException):
            raise value
        return value

    return fake_send, lambda: calls


def _capture_call_api(
    captured: dict[str, Any] | None = None,
    counts: list[int] | None = None,
    outcome: dict[str, Any] | list[Any] | BaseException | None = None,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """构造 _call_api 替身，按传入的收集器记录请求数据与产图数，缺省返回成功空载荷。

    captured 与 counts 传入时分别记录请求数据与生成方法装配的 per_request_image_count 实参。
    outcome 传入 dict/list 时替代默认载荷返回，BaseException 即抛出。
    """

    async def fake_call_api(
        endpoint: str,
        request_data: dict[str, Any],
        *,
        per_request_image_count: int,
    ) -> dict[str, Any]:
        del endpoint
        if captured is not None:
            captured.update(request_data)
        if counts is not None:
            counts.append(per_request_image_count)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is not None:
            return cast(dict[str, Any], outcome)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    return fake_call_api


async def _parse_sse(
    chunks: list[bytes],
    *,
    buffer_max_size: int = 4096,
    event_truncate_threshold: int = 4096,
    retained_bytes_limit: int = 64 * 1024,
    min_items: int | None = None,
    log: Logger | None = None,
) -> dict[str, Any]:
    """以标准参数组合解析伪 SSE 响应，调用方仅声明偏离默认值的参数。"""
    # min_items 缺省按调用时能力表活算，导入期绑定会令
    # monkeypatch 能力表的用例拿到过期下限。
    if min_items is None:
        min_items = max_per_request_image_count()
    return await parse_sse_response(
        cast(httpx.Response, _FakeSSEResponse(chunks)),
        model_id="m",
        buffer_max_size=buffer_max_size,
        event_truncate_threshold=event_truncate_threshold,
        retained_bytes_limit=retained_bytes_limit,
        min_items=min_items,
        log=cast("Logger", _FakeLog()) if log is None else log,
    )
