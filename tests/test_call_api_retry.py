"""SeedreamClient._call_api 重试与错误分类守护。

覆盖错误恢复路径的核心分支：429 指数退避重试后成功、4xx 客户端错误立即抛出、
超时经重试用尽映射为 SeedreamTimeoutError、非可重试的意外错误不浪费退避。
网络层经 monkeypatch 注入，不触达真实 API。
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


async def test_call_api_retries_on_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
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

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 2
    assert result["success"] is True


async def test_call_api_4xx_not_retried(monkeypatch: pytest.MonkeyPatch, no_sleep: None) -> None:
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

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(SeedreamAPIError):
            await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 1


async def test_call_api_timeout_retried_then_mapped(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
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

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(SeedreamTimeoutError):
            await client._call_api("text_to_image", {"prompt": "p"})

    # max_retries=1 表示首次失败后还可重试 1 次，故共 2 次尝试
    assert calls == 2


async def test_call_api_unexpected_error_not_retried(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
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

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(ValueError):
            await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 1


async def test_call_api_retries_on_5xx_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
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

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 2
    assert result["success"] is True


async def test_call_api_network_error_retries_then_mapped(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
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

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(SeedreamNetworkError):
            await client._call_api("text_to_image", {"prompt": "p"})

    # max_retries=1 表示首次失败后还可重试 1 次，故共 2 次尝试
    assert calls == 2


async def test_call_api_429_uses_retry_after_for_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
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

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 2
    assert result["success"] is True
    # retry_after=2.0 路径：单次退避等于 Retry-After 值；指数路径 attempt 0 应为 2**0=1.0
    assert sleep_durations == [2.0]


async def test_call_api_429_retry_after_above_backoff_cap(monkeypatch: pytest.MonkeyPatch) -> None:
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

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p"})

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
