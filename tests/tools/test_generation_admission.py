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


def _instrumented_admission_clients(
    monkeypatch: pytest.MonkeyPatch, count: int
) -> tuple[list[SeedreamClient], dict[str, int]]:
    """构造注入峰值计数的 client 列表，_call_api 期间记录在途请求峰值。"""
    stats = {"active": 0, "peak": 0}

    async def fake_send(**_kwargs: Any) -> dict[str, Any]:
        stats["active"] += 1
        stats["peak"] = max(stats["peak"], stats["active"])
        await asyncio.sleep(0.02)
        stats["active"] -= 1
        return {"success": True}

    async def noop() -> None:
        return None

    clients = []
    for _ in range(count):
        client = SeedreamClient(SeedreamConfig(api_key="k", generate_concurrency=2))
        monkeypatch.setattr(client, "_ensure_client", noop)
        monkeypatch.setattr(client, "_get_http_client", lambda: None)
        monkeypatch.setattr(client, "_send_standard_request", fake_send)
        clients.append(client)
    return clients, stats


async def test_generation_admission_caps_inflight_api_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_call_api 层的准入信号量约束同时在途的生成 API 请求数，超限排队。"""
    clients, stats = _instrumented_admission_clients(monkeypatch, 1)

    await asyncio.gather(*(clients[0]._call_api("t2i", {"prompt": "p"}) for _ in range(6)))

    assert stats["peak"] == 2


async def test_generation_admission_shared_across_client_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """准入信号量按固定 key 进程级共享，多实例合并后的在途上限仍为单实例配置值。"""
    clients, stats = _instrumented_admission_clients(monkeypatch, 2)

    await asyncio.gather(
        *(client._call_api("t2i", {"prompt": "p"}) for client in clients for _ in range(3))
    )

    assert stats["peak"] == 2
