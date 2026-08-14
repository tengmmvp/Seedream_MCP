"""SeedreamClient._call_api 流式分支重试守护。

流式与标准分支共用 _call_api 的重试循环，本文件以 request_data.stream=True 触发
_send_stream_request 分发，覆盖 429(带 Retry-After)/5xx/超时/网络错误各重试分支
与末次失败的异常映射。网络层经 monkeypatch 注入 _send_stream_request，不触达真实 API。
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


async def test_stream_retries_on_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """429 限流经流式分支退避重试，首次失败后第二次成功。"""
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

        monkeypatch.setattr(client, "_send_stream_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert calls == 2
    assert result["success"] is True


async def test_stream_retries_on_5xx_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """5xx 服务端错误经流式分支退避重试，首次失败后第二次成功。"""
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

        monkeypatch.setattr(client, "_send_stream_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert calls == 2
    assert result["success"] is True


async def test_stream_timeout_retried_then_mapped_to_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    """流式分支 httpx 超时按 max_retries 重试用尽后映射为 SeedreamTimeoutError。"""
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
            raise httpx.TimeoutException("stream timed out")

        monkeypatch.setattr(client, "_send_stream_request", fake_send)
        with pytest.raises(SeedreamTimeoutError):
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    # max_retries=1 表示首次失败后还可重试 1 次，故共 2 次尝试
    assert calls == 2


async def test_stream_network_error_retried_then_mapped_to_network_error(
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
) -> None:
    """流式分支 httpx.RequestError 重试用尽后映射为 SeedreamNetworkError。"""
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

        monkeypatch.setattr(client, "_send_stream_request", fake_send)
        with pytest.raises(SeedreamNetworkError):
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert calls == 2


async def test_stream_429_uses_retry_after_for_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """带 retry_after 的流式 429 退避基于服务端 Retry-After 值，而非指数 2**attempt。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0
    sleep_durations: List[float] = []

    async def _capture_sleep(*args: object, **kwargs: object) -> None:
        del kwargs
        if args:
            sleep_durations.append(float(args[0]))  # type: ignore[arg-type]

    # 抖动归零使退避值确定等于 Retry-After，便于精确断言
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

        monkeypatch.setattr(client, "_send_stream_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert calls == 2
    assert result["success"] is True
    # retry_after 路径：单次退避等于服务端 Retry-After 值；指数路径 attempt 0 应为 2**0=1.0
    assert sleep_durations == [2.0]


async def test_stream_4xx_not_retried(monkeypatch: pytest.MonkeyPatch, no_sleep: None) -> None:
    """流式分支 4xx 客户端错误（非 429）立即抛出，不重试。"""
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

        monkeypatch.setattr(client, "_send_stream_request", fake_send)
        with pytest.raises(SeedreamAPIError):
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert calls == 1


async def test_stream_unexpected_error_not_retried(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """流式分支非可重试的意外错误立即抛出，不浪费退避等待。"""
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

        monkeypatch.setattr(client, "_send_stream_request", fake_send)
        with pytest.raises(ValueError):
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert calls == 1


async def test_stream_429_retry_after_above_backoff_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """流式分支 retry_after 超过指数退避上限时仍信任服务端值，不被 60 秒上限截断。"""
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

        monkeypatch.setattr(client, "_send_stream_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert calls == 2
    assert result["success"] is True
    # retry_after=120 超过 _MAX_BACKOFF_SECONDS=60，应信任服务端值 120 而非被截断为 60
    assert sleep_durations == [120.0]
