"""生成并发准入测试：请求级信号量约束同时在途的生成 API 请求数。"""

from __future__ import annotations

import asyncio
from typing import Any

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.context import GenerationExecutionContext
from seedream_mcp.tools.core.parallel import _run_generation_requests
from seedream_mcp.utils.core.logs import get_logger
from seedream_mcp.utils.core.loop_bound import loop_bound_semaphore

from _generation_fixtures import make_generation_context


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


async def test_generation_admission_caps_batch_inflight_requests() -> None:
    """批次内并行请求各占一个准入名额，在途 API 请求数不超过配置限值。"""
    config = SeedreamConfig(api_key="k", generate_concurrency=2)
    client = SeedreamClient(config)
    context = make_generation_context(
        prompt="p",
        request_count=6,
        parallelism=6,
        enable_auto_save=False,
    )
    active = 0
    peak = 0

    async def executor(_client: SeedreamClient, _ctx: GenerationExecutionContext) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"success": True, "data": [], "usage": {}, "status": "completed"}

    await _run_generation_requests(
        client=client,
        context=context,
        config=config,
        ctx=None,
        request_executor=executor,
        module_logger=get_logger(),
    )

    assert peak == 2
