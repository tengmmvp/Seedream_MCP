"""生成请求的并行执行与 lifespan 共享资源获取。

单次请求直接调用 request_executor；request_count > 1 时按 parallelism 构造信号量限流
并发，逐个请求完成后按完成数上报进度。共享的 SeedreamClient 与 DownloadManager 优先从
lifespan 上下文获取，以复用 HTTP/aiohttp 连接池，无 lifespan 场景由调用方回退新建。

批次执行期间经 client.shared_request_plan_scope 绑定共享请求计划：同批请求在 client
侧只构建一次 request_data、只序列化一次 body，N 个并行请求峰值内存 1×body；作用域
退出时统一复位绑定并释放计划引用。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

from ...config import LIFESPAN_KEY_CLIENT, LIFESPAN_KEY_DOWNLOAD_MANAGER
from ...utils.core.errors import format_error_for_user
from ._helpers import (
    PROGRESS_GENERATION_DONE,
    PROGRESS_GENERATION_START,
    _safe_report_progress,
    _yield_for_cancellation,
)
from .context import GenerationExecutionContext
from .results import aggregate_parallel_generation_results

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient
    from ...utils.io.io_download import DownloadManager


# lifespan 共享资源取值的泛型辅助，client/download_manager/config 三处探测共用。
# lifespan 上下文字典键定义在 config 模块，经顶部 import 复用，core 层不依赖顶层装配模块。
_T = TypeVar("_T")


async def _execute_parallel_generation_requests(
    *,
    client: "SeedreamClient",
    context: GenerationExecutionContext,
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[dict[str, Any]]
    ],
    module_logger: Any,
    ctx: Context[Any, Any] | None = None,
) -> dict[str, Any]:
    """按 parallelism 信号量限流并发执行多次生成请求，完成后聚合结果。

    每个请求独立捕获异常并记入 request_errors，不中断其余请求。
    """
    semaphore = asyncio.Semaphore(context.parallelism)
    request_results: list[dict[str, Any] | None] = [None] * context.request_count
    request_errors: dict[int, Exception] = {}
    completed_requests = 0

    async def _run_single_request(request_index: int) -> None:
        """在信号量槽内执行单次请求并记录结果或异常，槽外上报进度。"""
        nonlocal completed_requests
        report_progress = False
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
                # asyncio 单线程模型下，自增之间无 await，不会被其他协程抢占，无需加锁。
                completed_requests += 1
                report_progress = True
        # 进度上报移出信号量槽，避免慢客户端背压拖延槽位释放、阻塞后续请求启动。
        if report_progress:
            progress = PROGRESS_GENERATION_START + (
                PROGRESS_GENERATION_DONE - PROGRESS_GENERATION_START
            ) * (completed_requests / context.request_count)
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


def get_lifespan_resource(
    ctx: Context[Any, Any] | None,
    key: str,
    resource_type: type[_T],
) -> _T | None:
    """从 lifespan 上下文按键取类型匹配的共享资源，无则返回 None。

    无 ctx 或无 lifespan 上下文时返回 None，由调用方回退新建，单元测试直接调用
    handler 时即走此路径。client、download_manager 与 config 三处共享资源探测共用
    此实现。取值路径上各属性缺失的异常形态均视为“不可得”：mcp 的
    Context.request_context 在无请求上下文时抛 ValueError，request_context 为
    None 时 .lifespan_context 抛 AttributeError，旧版本构造路径可能抛 LookupError，
    捕获三者确保守卫本身不逃逸异常。
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
    """从 lifespan 上下文获取共享 SeedreamClient，无则返回 None。

    复用共享客户端可共享 HTTP 连接池。无 lifespan 的场景返回 None，由调用方回退新建。
    """
    from ...client import SeedreamClient

    return get_lifespan_resource(ctx, LIFESPAN_KEY_CLIENT, SeedreamClient)


def _try_get_shared_download_manager(
    ctx: Context[Any, Any] | None,
) -> DownloadManager | None:
    """从 lifespan 上下文获取共享 DownloadManager，无则返回 None。

    复用共享下载管理器可跨请求复用 aiohttp 连接池，避免每次生成重复 TLS 握手。
    """
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
    module_logger: Any,
) -> dict[str, Any]:
    """在给定客户端上执行单次或并行生成请求并返回结果。

    request_count 为 1 时直接调用 request_executor；否则委托
    ``_execute_parallel_generation_requests`` 并行执行。进度按阶段上报。

    批次执行期间绑定共享请求计划，client 侧据此对同批请求只构建一次 request_data、
    只序列化一次 body；作用域退出时经 finally 复位绑定并释放计划，异常与取消路径
    均不泄漏。公共参数校验同样提升为批次级：分发前经
    prevalidate_common_generation_params 校验一次并写入计划缓存，批内各生成方法
    按输入快照命中缓存，100k 级提示词的 CJK 计数不再逐请求重复。
    """
    from ...client import shared_request_plan_scope

    with shared_request_plan_scope():
        # 批次级公共参数校验：批内各请求公共参数相同，分发前校验一次经共享计划
        # 缓存复用。校验失败在分发前上抛，异常与消息和单请求路径的首请求校验失败
        # 一致，由外层流水线统一降级，不进入逐请求错误聚合。
        client.prevalidate_common_generation_params(
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
            await _safe_report_progress(
                ctx, progress=PROGRESS_GENERATION_START, message="开始调用图像生成接口"
            )
            await _yield_for_cancellation()
            result = await request_executor(client, context)
            await _safe_report_progress(
                ctx, progress=PROGRESS_GENERATION_DONE, message="图像生成完成"
            )
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
        await _safe_report_progress(
            ctx, progress=PROGRESS_GENERATION_DONE, message="并行请求执行完成"
        )
        return result
