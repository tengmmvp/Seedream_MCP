"""并行生成批次中途取消传播守护。

批次执行中取消时，取消信号经 gather 传播至已启动请求、在 await 处中断，
_run_generation_requests 整批抛出 CancelledError，无请求能完成。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.context import GenerationExecutionContext
from seedream_mcp.tools.core.parallel import _run_generation_requests
from seedream_mcp.utils.core.logs import get_logger


def _make_context(request_count: int, parallelism: int) -> GenerationExecutionContext:
    return GenerationExecutionContext(
        prompt="p",
        optimize_prompt_options=None,
        size="2K",
        watermark=False,
        response_format="url",
        output_format=None,
        stream=False,
        tools=None,
        layer_decomposition=False,
        background=None,
        max_images=None,
        request_count=request_count,
        parallelism=parallelism,
        enable_auto_save=False,
        save_path=None,
        custom_name=None,
    )


@pytest.mark.asyncio
async def test_parallel_batch_cancellation_propagates_to_inflight_requests() -> None:
    """批次执行中取消：已启动请求在 await 处被中断，整批抛出 CancelledError。"""
    config = SeedreamConfig(api_key="k")
    client = SeedreamClient(config)
    context = _make_context(request_count=3, parallelism=2)

    started = asyncio.Event()
    started_count = 0
    cancelled_count = 0
    completed_count = 0
    release = asyncio.Event()

    async def executor(_client: SeedreamClient, _ctx: GenerationExecutionContext) -> dict[str, Any]:
        nonlocal started_count, cancelled_count, completed_count
        started_count += 1
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled_count += 1
            raise
        completed_count += 1
        return {"success": True, "data": [], "usage": {}, "status": "completed"}

    batch = asyncio.ensure_future(
        _run_generation_requests(
            client=client,
            context=context,
            ctx=None,
            request_executor=executor,
            module_logger=get_logger("test"),
        )
    )
    # 等待至少一个请求进入 executor 并挂起，模拟批次执行中
    await started.wait()
    batch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await batch

    # 取消传播至已启动请求：每个已启动请求都被中断，无一走到完成产出
    assert started_count >= 1
    assert cancelled_count >= 1
    assert cancelled_count == started_count
    assert completed_count == 0
    # 信号量限流下在途请求数不超过 parallelism，取消不突破并发上限
    assert started_count <= context.parallelism
