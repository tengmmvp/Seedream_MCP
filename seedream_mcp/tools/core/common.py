"""生成类工具通用处理门面。

内部按职责拆分到 _helpers/context/results/auto_save/parallel 子模块；本模块聚合公共
符号，供 tools/impl 与测试经 ``from ...core.common import X`` 导入。
``execute_generation_handler`` 作为四类生成工具的统一处理流水线留在此处，依次执行参数
归一化与校验、客户端调用、自动保存、响应与结构化结果格式化，成功路径按配置生成已保存
图片的缩略图预览，并对异常做统一降级处理。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Sequence

from mcp.types import CallToolResult, ImageContent, TextContent

from ...config import SeedreamConfig
from ...utils.images.image_thumbnail import build_preview_contents
from ...utils.io.io_save import AutoSaveResult
from ...utils.core.errors import format_error_for_user, resolve_error_profile
from ._helpers import (
    PROGRESS_AUTOSAVE_DONE,
    PROGRESS_AUTOSAVE_START,
    PROGRESS_COMPLETE,
    PROGRESS_RECEIVED,
    PROGRESS_VALIDATED,
    _classify_generation_error_type,
    _is_generation_failed,
    _resolve_failure_guidance,
    _safe_report_progress,
    _yield_for_cancellation,
)
from .auto_save import auto_save_from_base64, auto_save_from_urls
from .context import GenerationExecutionContext, build_generation_context
from .outputs import build_error_structured
from .parallel import (
    _run_generation_requests,
    _try_get_shared_client,
    _try_get_shared_download_manager,
    get_lifespan_resource,
)
from .results import (  # noqa: F401
    _build_generation_structured_result,
    aggregate_parallel_generation_results,
    extract_images,
    format_generation_response,
    update_result_with_auto_save,
)
from .schemas import GenerationInputParams

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient


# 门面对外导出的公共符号。_safe_* 与 _try_get_shared_* 等私有辅助供内部子模块和
# 测试经各自定义模块显式导入。
__all__ = [
    "GenerationExecutionContext",
    "aggregate_parallel_generation_results",
    "auto_save_from_base64",
    "auto_save_from_urls",
    "build_generation_context",
    "execute_generation_handler",
    "extract_images",
    "format_generation_response",
    "get_lifespan_resource",
    "update_result_with_auto_save",
]


async def execute_generation_handler(
    *,
    params: GenerationInputParams,
    config: SeedreamConfig,
    module_logger: Any,
    tool_name: str,
    completion_title: str,
    failure_prefix: str,
    start_log_message: str,
    start_log_values_builder: Callable[[GenerationExecutionContext], Sequence[Any]],
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[dict[str, Any]]
    ],
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """执行生成类工具的通用处理流水线，返回 MCP 结构化工具结果。

    流水线依次为：构建并校验执行上下文、按 request_count 单次或并行调用客户端、按
    response_format 触发 URL 下载或 Base64 解码的自动保存、格式化面向模型的文本与
    structuredContent，随后在预览开启且存在成功保存图片时生成缩略图 ImageContent 追加
    进 content。任意阶段抛出的异常都被捕获并降级为 ``is_error=True`` 的结果，
    不向调用方抛出。

    Args:
        params: 经 pydantic 校验的工具输入模型，由各 impl handler 透传。
        config: 当前生效的 SeedreamConfig。
        module_logger: 各 impl 模块的 loguru logger，用于离线日志。
        tool_name: 工具标识，写入 structuredContent.tool 与日志。
        completion_title: 成功时响应文本的标题。
        failure_prefix: 失败时错误消息与日志的前缀。
        start_log_message: 请求开始时的日志模板。
        start_log_values_builder: 基于执行上下文构造日志模板参数的回调。
        request_executor: 执行单次生成请求的回调，由各 impl 提供 client 调用差异。
        ctx: MCP 上下文，用于进度上报，无会话时可为 None。

    Returns:
        MCP 结构化工具结果。成功时含文本摘要与 structuredContent，预览开启且自动保存
        成功时另含缩略图 ImageContent；失败时 isError 为 True。
    """
    try:
        from ...client import SeedreamClient

        await _safe_report_progress(
            ctx, progress=PROGRESS_RECEIVED, message=f"{failure_prefix}请求已接收"
        )
        await _yield_for_cancellation()
        context = build_generation_context(params, config)
        await _safe_report_progress(ctx, progress=PROGRESS_VALIDATED, message="参数校验完成")

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

        auto_save_results: list[AutoSaveResult] = []
        saveable_indices: list[int] = []
        auto_save_error: str | None = None
        is_generation_failed = _is_generation_failed(result)
        # 图片列表在自动保存前提取一次并传入 auto_save_from_*，供收集阶段直接复用，
        # 消除收集阶段对同一结果的重复提取。
        images = extract_images(result)
        if context.enable_auto_save and not is_generation_failed:
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
                        images=images,
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
                        images=images,
                    )

                if auto_save_results:
                    result = update_result_with_auto_save(
                        result, auto_save_results, saveable_indices
                    )
                    # 回填合并改写了 data（补充 local_path/markdown_ref），展示与结构化
                    # 输出按合并后的结果重新提取；未触发合并时沿用上方已提取的列表。
                    images = extract_images(result)
                await _safe_report_progress(
                    ctx, progress=PROGRESS_AUTOSAVE_DONE, message="自动保存完成"
                )
            except Exception as exc:
                auto_save_error = format_error_for_user(exc)
                module_logger.warning("自动保存失败，已降级跳过: {}", auto_save_error)

        # images 供后续纯函数复用，避免 extract_images 在格式化、结构化与日志阶段
        # 重复遍历同一结果。
        response_text = format_generation_response(
            completion_title,
            result,
            context.prompt,
            context.size,
            auto_save_results,
            context.enable_auto_save,
            auto_save_error=auto_save_error,
            images=images,
            saveable_indices=saveable_indices,
        )

        # 净化协调：文本出口仅在成功分支消费图片列表并就地净化写回，失败分支经
        # _format_failure_section 提前返回、不经净化。结构化出口据此显式获知列表
        # 净化状态：成功路径跳过重复净化复用同一份净化值，失败路径自行完成首次
        # 净化，净化次数恒为一，超长片段的截断标记不叠加。
        structured_result = _build_generation_structured_result(
            tool_name=tool_name,
            result=result,
            context=context,
            auto_save_results=auto_save_results,
            auto_save_error=auto_save_error,
            images=images,
            images_sanitized=not is_generation_failed,
        )

        # 预览从自动保存落盘的本地文件生成：未开启、生成失败或没有成功保存的图片时
        # 列表为空，content 退化为纯文本，行为与本功能引入前一致。单张缩略图失败在
        # build_preview_contents 内部跳过，不影响工具结果。
        preview_contents: list[ImageContent] = []
        if config.preview_enabled and not is_generation_failed and auto_save_results:
            saved_paths = [
                Path(save_result.local_path)
                for save_result in auto_save_results
                if save_result.success and save_result.local_path
            ]
            preview_contents = await build_preview_contents(saved_paths)

        await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="请求处理完成")
        return CallToolResult(
            content=[TextContent(type="text", text=response_text), *preview_contents],
            structured_content=structured_result,
            is_error=is_generation_failed,
        )
    except Exception as exc:
        module_logger.error("{}处理失败", failure_prefix, exc_info=True)
        await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="请求处理失败")
        user_facing_error = format_error_for_user(exc)
        # format_error_for_user 已在档案携带 user_hint 时把建议拼入文案，此时不再
        # 叠加查表排查建议，避免 429/402 等场景同一句建议逐字出现两遍；档案无
        # user_hint 时才以查表建议补充，参数类错误不附带凭据与网络指引。
        if resolve_error_profile(exc).user_hint:
            error_message = f"{failure_prefix}失败：{user_facing_error}"
        else:
            error_message = (
                f"{failure_prefix}失败：{user_facing_error}\n{_resolve_failure_guidance(exc)}"
            )
        return CallToolResult(
            content=[TextContent(type="text", text=error_message)],
            structured_content=build_error_structured(
                tool_name,
                _classify_generation_error_type(exc),
                user_facing_error,
            ),
            is_error=True,
        )
