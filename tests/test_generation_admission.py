"""生成并发准入测试：_call_api 层信号量约束同时在途的生成 API 请求数。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.loop_bound import loop_bound_semaphore


def test_generation_admission_default_limit() -> None:
    """默认配置的准入限值为 3。"""
    config = SeedreamConfig(api_key="k")

    assert config.generate_concurrency == 3


async def test_loop_bound_semaphore_caps_concurrent_entries() -> None:
    """同调用点信号量的并发进入数不超过限值，超出部分排队等待。"""
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with loop_bound_semaphore(2, key="admission-test"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(6)))

    assert peak == 2


async def test_loop_bound_semaphore_rebuilds_on_limit_change() -> None:
    """限值变更后信号量按新限值重建，新请求按新限值进入。"""
    entered = asyncio.Event()

    async def second_entry() -> None:
        async with loop_bound_semaphore(2, key="admission-test-limit"):
            entered.set()

    async with loop_bound_semaphore(1, key="admission-test-limit"):
        task = asyncio.ensure_future(second_entry())
        await asyncio.sleep(0.01)
        assert entered.is_set()
        await task


async def test_generation_admission_caps_inflight_api_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_call_api 层的准入信号量约束同时在途的生成 API 请求数，超限排队。"""
    config = SeedreamConfig(api_key="k", generate_concurrency=2)
    client = SeedreamClient(config)
    active = 0
    peak = 0

    async def fake_send(**_kwargs: Any) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"success": True}

    async def noop() -> None:
        return None

    monkeypatch.setattr(client, "_ensure_client", noop)
    monkeypatch.setattr(client, "_get_http_client", lambda: None)
    monkeypatch.setattr(client, "_send_standard_request", fake_send)

    await asyncio.gather(*(client._call_api("t2i", {"prompt": "p"}) for _ in range(6)))

    assert peak == 2
