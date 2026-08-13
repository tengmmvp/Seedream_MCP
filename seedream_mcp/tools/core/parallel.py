"""生成请求的并行执行与 lifespan 共享资源获取。

单次请求直接调用 request_executor；request_count > 1 时按 parallelism 构造信号量限流
并发，逐个请求完成后按完成数上报进度。共享的 SeedreamClient 与 DownloadManager 优先从
lifespan 上下文获取，以复用 HTTP/aiohttp 连接池，无 lifespan 场景由调用方回退新建。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from ...utils.errors import format_error_for_user
from ._helpers import (
    PROGRESS_GENERATION_DONE,
    PROGRESS_GENERATION_START,
    _safe_report_progress,
    _yield_for_cancellation,
)
from .context import GenerationExecutionContext
from .results import aggregate_parallel_generation_results

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from ...client import SeedreamClient


async def _execute_parallel_generation_requests(
    *,
    client: "SeedreamClient",
    context: GenerationExecutionContext,
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[Dict[str, Any]]
    ],
    module_logger: Any,
    ctx: Optional["Context[Any, Any, Any]"] = None,
    progress_start: float = PROGRESS_GENERATION_START,
    progress_span: float = PROGRESS_GENERATION_DONE - PROGRESS_GENERATION_START,
) -> Dict[str, Any]:
    """按 parallelism 信号量限流并发执行多次生成请求，完成后聚合结果。

    每个请求独立捕获异常并记入 request_errors，不中断其余请求。
    """
    semaphore = asyncio.Semaphore(context.parallelism)
    request_results: List[Optional[Dict[str, Any]]] = [None] * context.request_count
    request_errors: Dict[int, Exception] = {}
    completed_requests = 0

    async def _run_single_request(request_index: int) -> None:
        nonlocal completed_requests
        async with semaphore:
            await _yield_for_cancellation()
            try:
                request_results[request_index - 1] = await request_executor(client, context)
            except Exception as exc:
                request_errors[request_index] = exc
                module_logger.warning(
                    "并行请求 {}/{} 失败: {}",
                    request_index,
                    context.request_count,
                    format_error_for_user(exc),
                )
            finally:
                # asyncio 单线程模型下，自增与进度读取之间无 await，不会被其他协程抢占，无需加锁。
                completed_requests += 1
                progress = progress_start + progress_span * (
                    completed_requests / context.request_count
                )
                await _safe_report_progress(
                    ctx,
                    progress=progress,
                    message=f"并行请求进度 {completed_requests}/{context.request_count}",
                )

    await asyncio.gather(
        *[
            _run_single_request(request_index)
            for request_index in range(1, context.request_count + 1)
        ]
    )

    return aggregate_parallel_generation_results(
        request_results=request_results,
        request_errors=request_errors,
    )


def _try_get_shared_client(
    ctx: Optional["Context[Any, Any, Any]"],
) -> "Optional[SeedreamClient]":
    """从 lifespan 上下文获取共享 SeedreamClient，无则返回 None。

    复用共享客户端可共享 HTTP 连接池。无 lifespan 的场景，例如单元测试直接调用
    handler 时，返回 None，由调用方回退新建，保持向后兼容。
    """
    if ctx is None:
        return None

    from ...client import SeedreamClient

    state = ctx.request_context.lifespan_context
    if isinstance(state, dict):
        shared = state.get("client")
        if isinstance(shared, SeedreamClient):
            return shared
    return None


def _try_get_shared_download_manager(
    ctx: Optional["Context[Any, Any, Any]"],
) -> Optional[Any]:
    """从 lifespan 上下文获取共享 DownloadManager，无则返回 None。

    复用共享下载管理器可跨请求复用 aiohttp 连接池，避免每次生成重复 TLS 握手。
    """
    if ctx is None:
        return None

    from ...utils.download_manager import DownloadManager

    state = ctx.request_context.lifespan_context
    if isinstance(state, dict):
        shared = state.get("download_manager")
        if isinstance(shared, DownloadManager):
            return shared
    return None


async def _run_generation_requests(
    *,
    client: "SeedreamClient",
    context: GenerationExecutionContext,
    ctx: Optional["Context[Any, Any, Any]"],
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[Dict[str, Any]]
    ],
    module_logger: Any,
) -> Dict[str, Any]:
    """在给定客户端上执行单次或并行生成请求并返回结果。

    request_count 为 1 时直接调用 request_executor；否则委托
    ``_execute_parallel_generation_requests`` 并行执行。进度按阶段上报。
    """
    if context.request_count == 1:
        await _safe_report_progress(
            ctx, progress=PROGRESS_GENERATION_START, message="开始调用图像生成接口"
        )
        await _yield_for_cancellation()
        result = await request_executor(client, context)
        await _safe_report_progress(ctx, progress=PROGRESS_GENERATION_DONE, message="图像生成完成")
        return result

    await _safe_report_progress(
        ctx,
        progress=PROGRESS_GENERATION_START,
        message=f"开始并行请求，共 {context.request_count} 次",
    )
    result = await _execute_parallel_generation_requests(
        client=client,
        context=context,
        request_executor=request_executor,
        module_logger=module_logger,
        ctx=ctx,
    )
    await _safe_report_progress(ctx, progress=PROGRESS_GENERATION_DONE, message="并行请求执行完成")
    return result
