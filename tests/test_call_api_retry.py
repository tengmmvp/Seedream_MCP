"""SeedreamClient._call_api 重试与错误分类守护。

覆盖错误恢复路径的核心分支：429 指数退避重试后成功、4xx 客户端错误立即抛出、
超时经重试用尽映射为 SeedreamTimeoutError、非可重试的意外错误不浪费退避。
网络层经 monkeypatch 注入，不触达真实 API。
"""

import asyncio
from typing import Any, Dict

import httpx
import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.errors import SeedreamAPIError, SeedreamTimeoutError


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
