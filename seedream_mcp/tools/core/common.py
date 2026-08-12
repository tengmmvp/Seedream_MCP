"""生成类工具通用处理门面。

内部按职责拆分到 _helpers/context/results/auto_save/parallel 子模块；本模块聚合公共
符号供 tools/impl 与测试按既有路径 ``from ...core.common import X`` 导入，
execute_generation_handler 作为四类生成工具的统一处理流水线留在此处。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Sequence

from mcp.types import CallToolResult, TextContent

from ...config import SeedreamConfig
from ...utils.errors import format_error_for_user
from ._helpers import (
    _safe_ctx_log,
    _safe_report_progress,
    _yield_for_cancellation,
)
from ._helpers import _resolve_base_dir as _resolve_base_dir  # noqa: F401
from .auto_save import auto_save_from_base64, auto_save_from_urls
from .context import GenerationExecutionContext, build_generation_context
from .parallel import (
    _run_generation_requests,
    _try_get_shared_client,
    _try_get_shared_download_manager,
)
from .results import (  # noqa: F401
    _build_generation_structured_result,
    aggregate_parallel_generation_results,
    extract_images,
    format_generation_response,
    update_result_with_auto_save,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from ...client import SeedreamClient


# 门面对外导出的公共符号；私有辅助（_safe_*、_resolve_base_dir 等）供内部与测试显式导入
__all__ = [
    "GenerationExecutionContext",
    "aggregate_parallel_generation_results",
    "auto_save_from_base64",
    "auto_save_from_urls",
    "build_generation_context",
    "execute_generation_handler",
    "extract_images",
    "format_generation_response",
    "update_result_with_auto_save",
]


async def execute_generation_handler(
    *,
    arguments: Dict[str, Any],
    config: SeedreamConfig,
    module_logger: Any,
    tool_name: str,
    completion_title: str,
    failure_prefix: str,
    guidance: str,
    start_log_message: str,
    start_log_values_builder: Callable[[GenerationExecutionContext], Sequence[Any]],
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[Dict[str, Any]]
    ],
    ctx: Optional["Context[Any, Any, Any]"] = None,
) -> CallToolResult:
    """
    执行生成类工具的通用处理流水线

    包括：参数归一化、调用客户端、自动保存、响应格式化、统一错误处理。
    """
    try:
        from ...client import SeedreamClient

        await _safe_report_progress(ctx, progress=0.0, message=f"{failure_prefix}请求已接收")
        await _yield_for_cancellation()
        context = build_generation_context(arguments, config)
        await _safe_report_progress(ctx, progress=10.0, message="参数校验完成")
        await _safe_ctx_log(
            ctx,
            "info",
            f"{tool_name} 请求开始：尺寸={context.size}, "
            f"请求数={context.request_count}, 并行度={context.parallelism}",
        )

        module_logger.info(start_log_message, *start_log_values_builder(context))

        # 优先复用 lifespan 注入的共享客户端以复用 HTTP 连接池，避免每次请求重建连接；
        # 无 lifespan 上下文时，例如直接调用 handler 的单元测试，回退到按需新建。
        shared_client = _try_get_shared_client(ctx)
        if shared_client is not None:
            result = await _run_generation_requests(
                client=shared_client,
                context=context,
                ctx=ctx,
                request_executor=request_executor,
                module_logger=module_logger,
            )
        else:
            async with SeedreamClient(config) as client:
                result = await _run_generation_requests(
                    client=client,
                    context=context,
                    ctx=ctx,
                    request_executor=request_executor,
                    module_logger=module_logger,
                )

        auto_save_results: List[Any] = []
        auto_save_error: Optional[str] = None
        if context.enable_auto_save and result.get("success"):
            try:
                await _safe_report_progress(ctx, progress=75.0, message="开始自动保存")
                await _yield_for_cancellation()
                shared_download_manager = _try_get_shared_download_manager(ctx)
                if context.response_format == "url":
                    auto_save_results = await auto_save_from_urls(
                        result,
                        context.prompt,
                        config,
                        context.save_path,
                        context.custom_name,
                        tool_name,
                        download_manager=shared_download_manager,
                    )
                else:
                    auto_save_results = await auto_save_from_base64(
                        result,
                        context.prompt,
                        config,
                        context.save_path,
                        context.custom_name,
                        tool_name,
                        download_manager=shared_download_manager,
                    )

                if auto_save_results:
                    result = update_result_with_auto_save(result, auto_save_results)
                    saved_count = sum(1 for r in auto_save_results if getattr(r, "success", False))
                    await _safe_ctx_log(
                        ctx,
                        "info",
                        f"已自动保存 {saved_count}/{len(auto_save_results)} 张图片到本地",
                    )
                await _safe_report_progress(ctx, progress=95.0, message="自动保存完成")
            except Exception as exc:
                auto_save_error = format_error_for_user(exc)
                module_logger.warning("自动保存失败，已降级跳过: {}", auto_save_error)
                await _safe_ctx_log(ctx, "warning", f"自动保存失败，已降级跳过：{auto_save_error}")

        response_text = format_generation_response(
            completion_title,
            result,
            context.prompt,
            context.size,
            auto_save_results,
            context.enable_auto_save,
            auto_save_error=auto_save_error,
        )

        structured_result = _build_generation_structured_result(
            tool_name=tool_name,
            result=result,
            context=context,
            auto_save_results=auto_save_results,
            auto_save_error=auto_save_error,
        )
        await _safe_report_progress(ctx, progress=100.0, message="请求处理完成")
        image_count = len(extract_images(result))
        await _safe_ctx_log(
            ctx,
            "info" if result.get("success") else "warning",
            f"{tool_name} {'完成' if result.get('success') else '未成功'}，"
            f"共 {image_count} 张图片",
        )
        return CallToolResult(
            content=[TextContent(type="text", text=response_text)],
            structuredContent=structured_result,
            isError=not bool(result.get("success")),
        )
    except Exception as exc:
        module_logger.error("{}处理失败", failure_prefix, exc_info=True)
        await _safe_report_progress(ctx, progress=100.0, message="请求处理失败")
        await _safe_ctx_log(ctx, "error", f"{failure_prefix}失败：{format_error_for_user(exc)}")
        error_message = f"{failure_prefix}失败：{format_error_for_user(exc)}\n{guidance}"
        return CallToolResult(
            content=[TextContent(type="text", text=error_message)],
            structuredContent={
                "tool": tool_name,
                "success": False,
                "status": "failed",
                "error": {
                    "type": exc.__class__.__name__,
                    "message": format_error_for_user(exc),
                },
            },
            isError=True,
        )
