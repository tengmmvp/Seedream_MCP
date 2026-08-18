"""SeedreamClient._call_api 重试与错误分类守护。

重试循环对标准与流式两条分发路径共用，核心分支以分发目标参数化同时锁定两条
路径：429 指数退避重试后成功、5xx 重试后成功、超时与网络错误重试耗尽后的异常
映射、4xx 与意外错误立即抛出、Retry-After 退避取值与超上限信任服务端值。流式
非 200 分支另以 httpx.MockTransport 驱动真实 _send_stream_request 覆盖：5xx 按
重试次数耗尽且异常携带状态码、429 按服务端 Retry-After 退避、3xx 立即终态不
重试。200 坏体与响应体上限分支仅标准路径在本文件经 MockTransport 覆盖。网络层
经 monkeypatch 或 MockTransport 注入，不触达真实 API。
"""

import asyncio
import random
from typing import Any, Dict, List

import httpx
import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import (
    SeedreamAPIError,
    SeedreamNetworkError,
    SeedreamTimeoutError,
)

from _client_fakes import _install_mock_transport

# 重试循环的两条分发目标：请求体不含 stream 标志经 _send_standard_request 发送，
# 含 stream=True 经 _send_stream_request 发送，其余重试语义完全共用。
_DISPATCH_TARGETS = [
    pytest.param("_send_standard_request", {}, id="standard"),
    pytest.param("_send_stream_request", {"stream": True}, id="stream"),
]


@pytest.mark.parametrize("send_method,extra_body", _DISPATCH_TARGETS)
async def test_call_api_retries_on_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None, send_method: str, extra_body: Dict[str, Any]
) -> None:
    """429 限流按退避重试，首次失败后第二次成功。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            if calls < 2:
                raise SeedreamAPIError("rate limited", status_code=429)
            return {"success": True, "data": [], "usage": {}, "status": "completed"}

        monkeypatch.setattr(client, send_method, fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p", **extra_body})

    assert calls == 2
    assert result["success"] is True


@pytest.mark.parametrize("send_method,extra_body", _DISPATCH_TARGETS)
async def test_call_api_4xx_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    send_method: str,
    extra_body: Dict[str, Any],
) -> None:
    """4xx 客户端错误（非 429）立即抛出，不重试。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            raise SeedreamAPIError("bad request", status_code=400)

        monkeypatch.setattr(client, send_method, fake_send)
        with pytest.raises(SeedreamAPIError):
            await client._call_api("text_to_image", {"prompt": "p", **extra_body})

    assert calls == 1


async def test_call_api_3xx_not_retried(monkeypatch: pytest.MonkeyPatch, no_sleep: None) -> None:
    """3xx（如 302）不可重试，立即终态抛出，不进入退避循环。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            raise SeedreamAPIError("found", status_code=302)

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(SeedreamAPIError):
            await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 1


async def test_call_api_exponential_backoff_no_overflow_on_huge_retries(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """max_retries 超过 1024 的病态配置下退避指数不抛 OverflowError。

    指数退避的计算位于全部 except 块之外，attempt 达到 1024 时
    float(2**1024) 溢出会把重试失败翻成不可解读的溢出包装；指数先做整数
    封顶后，大配置全程按既有的 5xx 可重试语义走完重试并以上游错误收尾。
    """
    config = SeedreamConfig(api_key="k", max_retries=1030)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            raise SeedreamAPIError("server error", status_code=500)

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(SeedreamAPIError) as excinfo:
            await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == config.max_retries + 1
    assert "too large to convert" not in str(excinfo.value)


@pytest.mark.parametrize("send_method,extra_body", _DISPATCH_TARGETS)
async def test_call_api_timeout_retried_then_mapped(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    send_method: str,
    extra_body: Dict[str, Any],
) -> None:
    """httpx 超时按 max_retries 重试用尽后映射为 SeedreamTimeoutError。"""
    config = SeedreamConfig(api_key="k", max_retries=1)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(client, send_method, fake_send)
        with pytest.raises(SeedreamTimeoutError):
            await client._call_api("text_to_image", {"prompt": "p", **extra_body})

    # max_retries=1 表示首次失败后还可重试 1 次，故共 2 次尝试
    assert calls == 2


@pytest.mark.parametrize("send_method,extra_body", _DISPATCH_TARGETS)
async def test_call_api_unexpected_error_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    send_method: str,
    extra_body: Dict[str, Any],
) -> None:
    """非可重试的意外错误立即抛出，不浪费退避等待。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            raise ValueError("unexpected bug")

        monkeypatch.setattr(client, send_method, fake_send)
        with pytest.raises(ValueError):
            await client._call_api("text_to_image", {"prompt": "p", **extra_body})

    assert calls == 1


@pytest.mark.parametrize("send_method,extra_body", _DISPATCH_TARGETS)
async def test_call_api_retries_on_5xx_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None, send_method: str, extra_body: Dict[str, Any]
) -> None:
    """5xx 服务端错误按退避重试，首次失败后第二次成功。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            if calls < 2:
                raise SeedreamAPIError("server error", status_code=500)
            return {"success": True, "data": [], "usage": {}, "status": "completed"}

        monkeypatch.setattr(client, send_method, fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p", **extra_body})

    assert calls == 2
    assert result["success"] is True


@pytest.mark.parametrize("send_method,extra_body", _DISPATCH_TARGETS)
async def test_call_api_network_error_retries_then_mapped(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    send_method: str,
    extra_body: Dict[str, Any],
) -> None:
    """httpx.RequestError（ConnectError）重试用尽后映射为 SeedreamNetworkError。"""
    config = SeedreamConfig(api_key="k", max_retries=1)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(client, send_method, fake_send)
        with pytest.raises(SeedreamNetworkError):
            await client._call_api("text_to_image", {"prompt": "p", **extra_body})

    # max_retries=1 表示首次失败后还可重试 1 次，故共 2 次尝试
    assert calls == 2


@pytest.mark.parametrize("send_method,extra_body", _DISPATCH_TARGETS)
async def test_call_api_429_uses_retry_after_for_backoff(
    monkeypatch: pytest.MonkeyPatch, send_method: str, extra_body: Dict[str, Any]
) -> None:
    """带 retry_after 的 429 退避基于服务端 Retry-After，而非指数 2**attempt。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0
    sleep_durations: List[float] = []

    async def _capture_sleep(*args: object, **kwargs: object) -> None:
        del kwargs
        if args:
            sleep_durations.append(float(args[0]))  # type: ignore[arg-type]

    # 抖动归零使退避值确定等于 base，便于精确断言 Retry-After 路径
    monkeypatch.setattr(asyncio, "sleep", _capture_sleep)
    monkeypatch.setattr(random, "uniform", lambda *_: 0.0)

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            if calls < 2:
                raise SeedreamAPIError("rate limited", status_code=429, retry_after=2.0)
            return {"success": True, "data": [], "usage": {}, "status": "completed"}

        monkeypatch.setattr(client, send_method, fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p", **extra_body})

    assert calls == 2
    assert result["success"] is True
    # retry_after=2.0 路径：单次退避等于 Retry-After 值；指数路径 attempt 0 应为 2**0=1.0
    assert sleep_durations == [2.0]


@pytest.mark.parametrize("send_method,extra_body", _DISPATCH_TARGETS)
async def test_call_api_429_retry_after_above_backoff_cap(
    monkeypatch: pytest.MonkeyPatch, send_method: str, extra_body: Dict[str, Any]
) -> None:
    """retry_after 超过指数退避上限时仍信任服务端值，不被 60 秒上限截断。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0
    sleep_durations: List[float] = []

    async def _capture_sleep(*args: object, **kwargs: object) -> None:
        del kwargs
        if args:
            sleep_durations.append(float(args[0]))  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "sleep", _capture_sleep)
    monkeypatch.setattr(random, "uniform", lambda *_: 0.0)

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_body: bytes,
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_body, request_timeout
            calls += 1
            if calls < 2:
                raise SeedreamAPIError("rate limited", status_code=429, retry_after=120.0)
            return {"success": True, "data": [], "usage": {}, "status": "completed"}

        monkeypatch.setattr(client, send_method, fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p", **extra_body})

    assert calls == 2
    assert result["success"] is True
    # retry_after=120 超过 _MAX_BACKOFF_SECONDS=60，应信任服务端值 120 而非被截断为 60
    assert sleep_durations == [120.0]


async def test_call_api_no_status_code_not_retried(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """无 HTTP 状态码的错误（如 200 响应体 JSON 解析失败）不可重试，立即抛出。

    生成 API 非幂等，服务端可能已按该请求完成生成与计费，重试会导致重复计费。
    经 httpx.MockTransport 返回 200 + 非 JSON 体，驱动真实的 _send_standard_request
    代码路径：状态码 200 放行，json.loads 抛 ValueError 被包装为 status_code=None 的
    SeedreamAPIError，_call_api 捕获后不重试直接上抛。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    transport = httpx.MockTransport(_handler)

    async with SeedreamClient(config) as client:
        # 用 MockTransport 替换内部 httpx 客户端，驱动真实 _send_standard_request
        assert client._client is not None
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=transport)

        with pytest.raises(SeedreamAPIError, match="JSON 解析失败") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        # status_code 为 None 的错误不可重试
        assert exc_info.value.status_code is None


async def test_standard_request_rejects_oversized_content_length(
    no_sleep: None,
) -> None:
    """非流式路径：Content-Length 声明超过总量上限的响应在读取前即拒绝。

    上限为 auto_save_max_file_size × 20（默认 1GB），此处压缩配置为 1024×20 字节
    以便测试；伪造远超实际 body 的 Content-Length 模拟被污染上游的巨型响应声明。
    """
    config = SeedreamConfig(api_key="k", max_retries=3, auto_save_max_file_size=1024)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": str(1024 * 1024)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        assert client._client is not None
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        # 无状态码的超限错误不可重试，避免对非幂等生成 API 重复请求
        assert exc_info.value.status_code is None
        assert "Content-Length" in exc_info.value.message


async def test_standard_request_rejects_chunked_body_over_limit(
    no_sleep: None,
) -> None:
    """非流式路径：chunked 无 Content-Length 的响应在流式累计读取中超限即拒绝。

    content 传异步生成器时 httpx 不携带 Content-Length 头，模拟分块滴流的巨型响应；
    限额读取必须不依赖 Content-Length 声明，在 aiter_bytes 累计中强制执行。
    """
    config = SeedreamConfig(api_key="k", max_retries=3, auto_save_max_file_size=1024)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream():
            # 上限 1024×20=20480 字节，共送出 40KB 确保跨过上限
            for _ in range(40):
                yield b"x" * 1024

        return httpx.Response(200, content=_stream())

    async with SeedreamClient(config) as client:
        assert client._client is not None
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        # 错误消息携带实际读取字节数，且无状态码不可重试
        assert "已读取" in exc_info.value.message
        assert exc_info.value.status_code is None


# ==================== 流式非 200：MockTransport 驱动真实 _send_stream_request ====================


async def test_stream_5xx_retries_with_status_code(no_sleep: None) -> None:
    """流式 5xx 经 _raise_for_stream_response_status 装配状态码，按重试次数耗尽失败。

    流式参数化用例整体替换 _send_stream_request，未触达非 200 的读体限额、状态码
    装配与错误分类；本组用例以 MockTransport 让真实流式发送路径发出请求并收到
    非 200，锁定该分支装配的 status_code 使 5xx 保持可重试语义。
    """
    config = SeedreamConfig(api_key="k", max_retries=2)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx.Response(
            500,
            json={"error": {"code": "InternalServiceError", "message": "upstream boom"}},
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="服务器内部错误") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

        # 错误链路携带状态码与上游错误码，5xx 语义重试至耗尽
        assert exc_info.value.status_code == 500
        assert exc_info.value.error_code == "InternalServiceError"
        assert attempts == config.max_retries + 1


async def test_stream_429_uses_retry_after_for_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """流式 429 带 Retry-After：异常携带 retry_after，退避按服务端值而非指数基数。"""
    config = SeedreamConfig(api_key="k", max_retries=2)
    attempts = 0
    sleep_durations: List[float] = []

    async def _capture_sleep(*args: object, **kwargs: object) -> None:
        del kwargs
        if args:
            sleep_durations.append(float(args[0]))  # type: ignore[arg-type]

    # 抖动归零使退避值确定等于 Retry-After，便于精确断言
    monkeypatch.setattr(asyncio, "sleep", _capture_sleep)
    monkeypatch.setattr(random, "uniform", lambda *_: 0.0)

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx.Response(429, headers={"retry-after": "2"}, json={})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="请求频率超限") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 2.0
        # 3 次尝试间共 2 次退避，均等于服务端 Retry-After 值而非指数 2**attempt
        assert attempts == config.max_retries + 1
        assert sleep_durations == [2.0, 2.0]


async def test_stream_3xx_not_retried(no_sleep: None) -> None:
    """流式 3xx 立即终态抛出，不进入退避重试。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx.Response(302, headers={"location": "https://example.com/elsewhere"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError) as exc_info:
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

        assert exc_info.value.status_code == 302
        assert attempts == 1
