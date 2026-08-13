"""生成类工具通用处理门面。

内部按职责拆分到 _helpers/context/results/auto_save/parallel 子模块；本模块聚合公共
符号供 tools/impl 与测试按既有路径 ``from ...core.common import X`` 导入。
``execute_generation_handler`` 作为四类生成工具的统一处理流水线留在此处，依次执行参数
归一化与校验、客户端调用、自动保存、响应与结构化结果格式化，并对异常做统一降级处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Sequence

from mcp.types import CallToolResult, TextContent

from ...config import SeedreamConfig
from ...utils.errors import format_error_for_user
from ._helpers import (
    PROGRESS_AUTOSAVE_DONE,
    PROGRESS_AUTOSAVE_START,
    PROGRESS_COMPLETE,
    PROGRESS_RECEIVED,
    PROGRESS_VALIDATED,
    _classify_generation_error_type,
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


# 门面对外导出的公共符号。_safe_* 与 _resolve_base_dir 等私有辅助供内部子模块和测试显式导入。
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
    arguments: dict[str, Any],
    config: SeedreamConfig,
    module_logger: Any,
    tool_name: str,
    completion_title: str,
    failure_prefix: str,
    guidance: str,
    start_log_message: str,
    start_log_values_builder: Callable[[GenerationExecutionContext], Sequence[Any]],
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[dict[str, Any]]
    ],
    ctx: Context[Any, Any, Any] | None = None,
) -> CallToolResult:
    """执行生成类工具的通用处理流水线，返回 MCP 结构化工具结果。

    流水线依次为：构建并校验执行上下文、按 request_count 单次或并行调用客户端、按
    response_format 触发 URL 下载或 Base64 解码的自动保存、格式化面向模型的文本与
    structuredContent。任意阶段抛出的异常都被捕获并降级为 ``isError=True`` 的结果，
    不向调用方抛出。

    Args:
        arguments: 工具原始参数字典，由各 impl handler 透传。
        config: 当前生效的 SeedreamConfig。
        module_logger: 各 impl 模块的 loguru logger，用于离线日志。
        tool_name: 工具标识，写入 structuredContent.tool 与日志。
        completion_title: 成功时响应文本的标题。
        failure_prefix: 失败时错误消息与日志的前缀。
        guidance: 失败时追加给用户的排查建议文本。
        start_log_message: 请求开始时的日志模板。
        start_log_values_builder: 基于执行上下文构造日志模板参数的回调。
        request_executor: 执行单次生成请求的回调，由各 impl 提供 client 调用差异。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 结构化工具结果。成功时含文本摘要与 structuredContent，失败时 isError 为 True。
    """
    try:
        from ...client import SeedreamClient

        await _safe_report_progress(
            ctx, progress=PROGRESS_RECEIVED, message=f"{failure_prefix}请求已接收"
        )
        await _yield_for_cancellation()
        context = build_generation_context(arguments, config)
        await _safe_report_progress(ctx, progress=PROGRESS_VALIDATED, message="参数校验完成")
        await _safe_ctx_log(
            ctx,
            "info",
            f"{tool_name} 请求开始：尺寸={context.size}, "
            f"请求数={context.request_count}, 并行度={context.parallelism}",
        )

        module_logger.info(start_log_message, *start_log_values_builder(context))

        # 优先复用 lifespan 注入的共享客户端，避免每次请求重建 HTTP 连接池；
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

        auto_save_results: list[Any] = []
        auto_save_error: str | None = None
        if context.enable_auto_save and result.get("success"):
            try:
                await _safe_report_progress(
                    ctx, progress=PROGRESS_AUTOSAVE_START, message="开始自动保存"
                )
                await _yield_for_cancellation()
                shared_download_manager = _try_get_shared_download_manager(ctx)
                if context.response_format == "url":
                    auto_save_results, saveable_indices = await auto_save_from_urls(
                        result,
                        context.prompt,
                        config,
                        context.save_path,
                        context.custom_name,
                        tool_name,
                        download_manager=shared_download_manager,
                    )
                else:
                    auto_save_results, saveable_indices = await auto_save_from_base64(
                        result,
                        context.prompt,
                        config,
                        context.save_path,
                        context.custom_name,
                        tool_name,
                        download_manager=shared_download_manager,
                    )

                if auto_save_results:
                    result = update_result_with_auto_save(
                        result, auto_save_results, saveable_indices
                    )
                    saved_count = sum(1 for r in auto_save_results if getattr(r, "success", False))
                    await _safe_ctx_log(
                        ctx,
                        "info",
                        f"已自动保存 {saved_count}/{len(auto_save_results)} 张图片到本地",
                    )
                await _safe_report_progress(
                    ctx, progress=PROGRESS_AUTOSAVE_DONE, message="自动保存完成"
                )
            except Exception as exc:
                auto_save_error = format_error_for_user(exc)
                module_logger.warning("自动保存失败，已降级跳过: {}", auto_save_error)
                await _safe_ctx_log(ctx, "warning", f"自动保存失败，已降级跳过：{auto_save_error}")

        # 自动保存可能改写 result，须在其之后计算一次图片列表，供后续纯函数复用，
        # 避免 extract_images 在格式化、结构化与日志阶段重复遍历同一结果。
        images = extract_images(result)
        response_text = format_generation_response(
            completion_title,
            result,
            context.prompt,
            context.size,
            auto_save_results,
            context.enable_auto_save,
            auto_save_error=auto_save_error,
            images=images,
        )

        structured_result = _build_generation_structured_result(
            tool_name=tool_name,
            result=result,
            context=context,
            auto_save_results=auto_save_results,
            auto_save_error=auto_save_error,
            images=images,
        )
        await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="请求处理完成")
        await _safe_ctx_log(
            ctx,
            "info" if result.get("success") else "warning",
            f"{tool_name} {'完成' if result.get('success') else '未成功'}，"
            f"共 {len(images)} 张图片",
        )
        return CallToolResult(
            content=[TextContent(type="text", text=response_text)],
            structuredContent=structured_result,
            isError=not bool(result.get("success")),
        )
    except Exception as exc:
        module_logger.error("{}处理失败", failure_prefix, exc_info=True)
        await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="请求处理失败")
        await _safe_ctx_log(ctx, "error", f"{failure_prefix}失败：{format_error_for_user(exc)}")
        error_message = f"{failure_prefix}失败：{format_error_for_user(exc)}\n{guidance}"
        return CallToolResult(
            content=[TextContent(type="text", text=error_message)],
            structuredContent={
                "tool": tool_name,
                "success": False,
                "status": "failed",
                "error": {
                    "type": _classify_generation_error_type(exc),
                    "message": format_error_for_user(exc),
                },
            },
            isError=True,
        )
