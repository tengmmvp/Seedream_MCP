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
from seedream_mcp.utils.errors import (
    SeedreamAPIError,
    SeedreamNetworkError,
    SeedreamTimeoutError,
)


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """屏蔽退避 sleep，避免测试因指数退避真实等待。"""

    async def _sleep(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(asyncio, "sleep", _sleep)


async def test_call_api_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 限流按退避重试，首次失败后第二次成功。"""
    _no_sleep(monkeypatch)
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_data: Dict[str, Any],
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_data, request_timeout
            calls += 1
            if calls < 2:
                raise SeedreamAPIError("rate limited", status_code=429)
            return {"success": True, "data": [], "usage": {}, "status": "completed"}

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 2
    assert result["success"] is True


async def test_call_api_4xx_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """4xx 客户端错误（非 429）立即抛出，不重试。"""
    _no_sleep(monkeypatch)
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_data: Dict[str, Any],
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_data, request_timeout
            calls += 1
            raise SeedreamAPIError("bad request", status_code=400)

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(SeedreamAPIError):
            await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 1


async def test_call_api_timeout_retried_then_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx 超时按 max_retries 重试用尽后映射为 SeedreamTimeoutError。"""
    _no_sleep(monkeypatch)
    config = SeedreamConfig(api_key="k", max_retries=1)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_data: Dict[str, Any],
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_data, request_timeout
            calls += 1
            raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(SeedreamTimeoutError):
            await client._call_api("text_to_image", {"prompt": "p"})

    # max_retries=1 表示首次失败后还可重试 1 次，故共 2 次尝试
    assert calls == 2


async def test_call_api_unexpected_error_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """非可重试的意外错误立即抛出，不浪费退避等待。"""
    _no_sleep(monkeypatch)
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_data: Dict[str, Any],
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_data, request_timeout
            calls += 1
            raise ValueError("unexpected bug")

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        with pytest.raises(ValueError):
            await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 1


async def test_call_api_retries_on_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx 服务端错误按退避重试，首次失败后第二次成功。"""
    _no_sleep(monkeypatch)
    config = SeedreamConfig(api_key="k", max_retries=3)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_data: Dict[str, Any],
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_data, request_timeout
            calls += 1
            if calls < 2:
                raise SeedreamAPIError("server error", status_code=500)
            return {"success": True, "data": [], "usage": {}, "status": "completed"}

        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        result = await client._call_api("text_to_image", {"prompt": "p"})

    assert calls == 2
    assert result["success"] is True


async def test_call_api_network_error_retries_then_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx.RequestError（ConnectError）重试用尽后映射为 SeedreamNetworkError。"""
    _no_sleep(monkeypatch)
    config = SeedreamConfig(api_key="k", max_retries=1)
    calls = 0

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_data: Dict[str, Any],
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_data, request_timeout
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
    monkeypatch.setattr(random, "uniform", lambda a, b: 0.0)

    async with SeedreamClient(config) as client:

        async def fake_send(
            *,
            client: httpx.AsyncClient,
            url: str,
            request_data: Dict[str, Any],
            request_timeout: httpx.Timeout,
        ) -> Dict[str, Any]:
            nonlocal calls
            del client, url, request_data, request_timeout
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
