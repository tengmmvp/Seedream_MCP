"""生成请求的并行执行与 lifespan 共享资源获取。

单次请求直接调用 request_executor；request_count > 1 时按 parallelism 信号量限流并发，
按完成数上报进度。共享的 SeedreamClient 与 DownloadManager 优先从 lifespan 上下文获取，
无 lifespan 场景由调用方回退新建。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from ...config import LIFESPAN_KEY_CLIENT, LIFESPAN_KEY_DOWNLOAD_MANAGER
from ...utils.core.errors import format_error_for_user
from ._helpers import (
    PROGRESS_GENERATION_DONE,
    PROGRESS_GENERATION_START,
    _yield_for_cancellation,
    safe_report_progress,
)
from .context import GenerationExecutionContext
from .results import aggregate_parallel_generation_results

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient
    from ...utils.io.io_download import DownloadManager
    from loguru import Logger


# lifespan 共享资源取值的泛型辅助，三处资源探测共用。
_T = TypeVar("_T")


async def _execute_parallel_generation_requests(
    *,
    client: "SeedreamClient",
    context: GenerationExecutionContext,
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[dict[str, Any]]
    ],
    module_logger: Logger,
    ctx: Context[Any, Any] | None = None,
) -> dict[str, Any]:
    """按 parallelism 信号量限流并发执行多次生成请求，完成后聚合结果。

    每个请求独立捕获异常并记入 request_errors，不中断其余请求。
    """
    semaphore = asyncio.Semaphore(context.parallelism)
    request_results: list[dict[str, Any] | None] = [None] * context.request_count
    request_errors: dict[int, Exception] = {}
    completed_requests = 0
    # 进度上报序列化锁：快照与发送之间隔着 await，并发完成可能交错发出回退进度，
    # 违反 progress 严格递增要求；锁内仅发送，发送顺序与完成顺序一致。
    progress_report_lock = asyncio.Lock()

    async def _run_single_request(request_index: int) -> None:
        """在信号量槽内执行单次请求并记录结果或异常，槽外上报进度。"""
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
                # 自增与快照之间无 await，不会被其他协程抢占。
                completed_requests += 1
                progress_snapshot = PROGRESS_GENERATION_START + (
                    PROGRESS_GENERATION_DONE - PROGRESS_GENERATION_START
                ) * (completed_requests / context.request_count)
                message_snapshot = f"并行请求进度 {completed_requests}/{context.request_count}"
        # 上报移出信号量槽，慢客户端背压不拖延槽位释放。
        async with progress_report_lock:
            await safe_report_progress(
                ctx,
                progress=progress_snapshot,
                message=message_snapshot,
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


def get_lifespan_resource(
    ctx: Context[Any, Any] | None,
    key: str,
    resource_type: type[_T],
) -> _T | None:
    """从 lifespan 上下文按键取类型匹配的共享资源，无则返回 None。

    无 ctx 或无 lifespan 上下文时返回 None，由调用方回退新建；取值路径上各属性缺失
    的异常形态均视为「不可得」，捕获后返回 None，确保守卫本身不逃逸异常。
    """
    if ctx is None:
        return None
    try:
        state = ctx.request_context.lifespan_context
    except (AttributeError, ValueError, LookupError):
        return None
    if isinstance(state, dict):
        resource = state.get(key)
        if isinstance(resource, resource_type):
            return resource
    return None


def _try_get_shared_client(
    ctx: Context[Any, Any] | None,
) -> SeedreamClient | None:
    """从 lifespan 上下文获取共享 SeedreamClient，复用 HTTP 连接池，无则返回 None。"""
    from ...client import SeedreamClient

    return get_lifespan_resource(ctx, LIFESPAN_KEY_CLIENT, SeedreamClient)


def _try_get_shared_download_manager(
    ctx: Context[Any, Any] | None,
) -> DownloadManager | None:
    """从 lifespan 上下文获取共享 DownloadManager，跨请求复用 aiohttp 连接池，
    无则返回 None。"""
    from ...utils.io.io_download import DownloadManager

    return get_lifespan_resource(ctx, LIFESPAN_KEY_DOWNLOAD_MANAGER, DownloadManager)


async def _run_generation_requests(
    *,
    client: "SeedreamClient",
    context: GenerationExecutionContext,
    ctx: Context[Any, Any] | None,
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[dict[str, Any]]
    ],
    module_logger: Logger,
) -> dict[str, Any]:
    """在给定客户端上执行单次或并行生成请求并返回结果。

    request_count 为 1 时直接调用 request_executor，否则并行执行。批次执行期间绑定
    共享请求计划，client 侧对同批请求只构建一次 request_data、只序列化一次 body；
    公共参数校验同样提升为批次级，分发前校验一次并经计划缓存复用。

    Args:
        request_executor: 执行单次生成请求的回调，由各 impl 提供 client 调用差异。
        ctx: MCP 上下文，用于进度上报，可为 None。
    """
    from ...client import shared_request_plan_scope

    with shared_request_plan_scope():
        # 批内公共参数相同，分发前校验一次；失败在分发前上抛，与单请求路径口径
        # 一致，不进入逐请求错误聚合。
        await client.prevalidate_common_generation_params(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
            size=context.size,
            watermark=context.watermark,
            response_format=context.response_format,
            output_format=context.output_format,
            stream=context.stream,
            tools=context.tools,
            layer_decomposition=context.layer_decomposition,
        )
        if context.request_count == 1:
            await safe_report_progress(
                ctx, progress=PROGRESS_GENERATION_START, message="开始调用图像生成接口"
            )
            await _yield_for_cancellation()
            result = await request_executor(client, context)
            await safe_report_progress(
                ctx, progress=PROGRESS_GENERATION_DONE, message="图像生成完成"
            )
            return result

        await safe_report_progress(
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
        # 末个请求的进度已到达 PROGRESS_GENERATION_DONE，重报同值违反严格递增要求。
        return result
