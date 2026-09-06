"""生成类工具通用处理门面。

内部按职责拆分到 _helpers/context/results/auto_save/parallel/outputs/schemas
子模块，本模块聚合公共符号供 tools/impl 与测试导入。``ToolMetadata`` 收纳各工具的静态元数据，
``execute_generation_handler`` 是四类生成工具的统一处理流水线，各阶段职责与异常降级
契约见该函数 docstring。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, ImageContent, TextContent

from ...config import SeedreamConfig
from ...utils.images.image_thumbnail import PREVIEW_MAX_IMAGES, build_preview_contents
from ...utils.io.io_save import AutoSaveResult
from ...utils.core.errors import format_error_for_user, resolve_error_profile
from ._helpers import (  # noqa: F401
    PROGRESS_AUTOSAVE_DONE,
    PROGRESS_AUTOSAVE_START,
    PROGRESS_COMPLETE,
    PROGRESS_GENERATION_DONE,
    PROGRESS_GENERATION_START,
    PROGRESS_RECEIVED,
    PROGRESS_SCAN_SPAN,
    PROGRESS_SCAN_START,
    PROGRESS_VALIDATED,
    _classify_generation_error_type,
    _is_generation_failed,
    _resolve_failure_guidance,
    _yield_for_cancellation,
    prevalidate_save_path,
    resolve_default_base_dir,
    safe_report_progress,
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
    _is_aggregated_result,
    _sanitize_image_errors,
    aggregate_parallel_generation_results,
    extract_images,
    format_generation_response,
    update_result_with_auto_save,
)
from .schemas import GenerationInputParams

if TYPE_CHECKING:
    from loguru import Logger

    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient


# 门面对外导出的公共符号，私有辅助经各自定义模块显式导入。
__all__ = [
    "GenerationExecutionContext",
    "ToolMetadata",
    "aggregate_parallel_generation_results",
    "auto_save_from_base64",
    "auto_save_from_urls",
    "build_generation_context",
    "execute_generation_handler",
    "extract_images",
    "format_generation_response",
    "get_lifespan_resource",
    "preview_inclusion_scope",
    "resolve_default_base_dir",
    "safe_report_progress",
    "update_result_with_auto_save",
]


# 预览装配的执行期开关。runner 层经 preview_inclusion_scope 关闭后沿异步上下文
# 传播到 execute_generation_handler，impl 处理器无需在签名上透传该开关。
_preview_enabled: ContextVar[bool] = ContextVar("seedream_preview_enabled", default=True)


@contextmanager
def preview_inclusion_scope(include_previews: bool) -> Iterator[None]:
    """在作用域内设置预览装配开关，退出时恢复先前的取值。

    仅 Web 端点等不消费 ImageContent 的调用方传 False，MCP 工具路径保持默认 True。
    """
    token = _preview_enabled.set(include_previews)
    try:
        yield
    finally:
        _preview_enabled.reset(token)


@dataclass(frozen=True)
class ToolMetadata:
    """单个生成工具透传给 ``execute_generation_handler`` 的静态元数据。

    常量字段直接收纳；开始日志参数依赖运行时执行上下文，以回调形式由各工具的元数据
    常量携带。

    Attributes:
        tool_name: 工具标识，写入 structuredContent.tool 与日志。
        completion_title: 成功时响应文本的标题。
        failure_prefix: 失败时错误消息与日志的前缀。
        start_log_message: 请求开始时的日志模板。
        start_log_values_builder: 由执行上下文构造开始日志模板的参数序列。
    """

    tool_name: str
    completion_title: str
    failure_prefix: str
    start_log_message: str
    start_log_values_builder: Callable[[GenerationExecutionContext], Sequence[Any]]


async def _prepare_generation_context(
    *,
    params: GenerationInputParams,
    config: SeedreamConfig,
    metadata: ToolMetadata,
    ctx: Context[Any, Any] | None,
    module_logger: Logger,
) -> GenerationExecutionContext:
    """校验与上下文准备阶段：预检参数、构建执行上下文并记录请求开始日志。"""
    await safe_report_progress(
        ctx, progress=PROGRESS_RECEIVED, message=f"{metadata.failure_prefix}请求已接收"
    )
    await _yield_for_cancellation()
    context = build_generation_context(params, config)
    # 预检含目录 resolve 等同步文件系统调用，下沉工作线程避免阻塞事件循环；
    # 仍在计费请求分发前完成。
    if params.save_path:
        await asyncio.to_thread(prevalidate_save_path, config, params.save_path)
    await safe_report_progress(ctx, progress=PROGRESS_VALIDATED, message="参数校验完成")

    module_logger.info(metadata.start_log_message, *metadata.start_log_values_builder(context))
    return context


async def _dispatch_generation_requests(
    *,
    config: SeedreamConfig,
    context: GenerationExecutionContext,
    ctx: Context[Any, Any] | None,
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[dict[str, Any]]
    ],
    module_logger: Logger,
) -> dict[str, Any]:
    """请求分发阶段：优先复用 lifespan 共享客户端执行单发或并行生成请求。"""
    from ...client import SeedreamClient

    shared_client = _try_get_shared_client(ctx)
    if shared_client is not None:
        return await _run_generation_requests(
            client=shared_client,
            context=context,
            ctx=ctx,
            request_executor=request_executor,
            module_logger=module_logger,
        )
    async with SeedreamClient(config) as client:
        return await _run_generation_requests(
            client=client,
            context=context,
            ctx=ctx,
            request_executor=request_executor,
            module_logger=module_logger,
        )


async def _auto_save_generation_images(
    *,
    result: dict[str, Any],
    images: list[dict[str, Any]],
    is_generation_failed: bool,
    context: GenerationExecutionContext,
    config: SeedreamConfig,
    tool_name: str,
    ctx: Context[Any, Any] | None,
    module_logger: Logger,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[AutoSaveResult], list[int], str | None]:
    """自动保存阶段：保存成功生成的图片，失败时降级记录错误并保留原结果。"""
    auto_save_results: list[AutoSaveResult] = []
    saveable_indices: list[int] = []
    auto_save_error: str | None = None
    if context.enable_auto_save and not is_generation_failed:
        try:
            await safe_report_progress(
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
                result = update_result_with_auto_save(result, auto_save_results, saveable_indices)
                # 合并改写了 data，重新提取供展示与结构化输出复用。
                images = extract_images(result)
            await safe_report_progress(ctx, progress=PROGRESS_AUTOSAVE_DONE, message="自动保存完成")
        except Exception as exc:
            auto_save_error = format_error_for_user(exc)
            module_logger.warning("自动保存失败，已降级跳过: {}", auto_save_error)
    return result, images, auto_save_results, saveable_indices, auto_save_error


def _format_generation_outputs(
    *,
    metadata: ToolMetadata,
    result: dict[str, Any],
    context: GenerationExecutionContext,
    auto_save_results: list[AutoSaveResult],
    auto_save_error: str | None,
    sanitized_images: list[dict[str, Any]],
    saveable_indices: list[int],
) -> tuple[str, dict[str, Any]]:
    """结果格式化阶段：生成响应文本与 structuredContent。"""
    response_text = format_generation_response(
        metadata.completion_title,
        result,
        context.size,
        auto_save_results,
        context.enable_auto_save,
        auto_save_error=auto_save_error,
        images=sanitized_images,
        saveable_indices=saveable_indices,
    )

    structured_result = _build_generation_structured_result(
        tool_name=metadata.tool_name,
        result=result,
        context=context,
        auto_save_results=auto_save_results,
        auto_save_error=auto_save_error,
        images=sanitized_images,
    )
    return response_text, structured_result


async def _build_generation_preview(
    *,
    config: SeedreamConfig,
    is_generation_failed: bool,
    auto_save_results: list[AutoSaveResult],
    module_logger: Logger,
    response_text: str,
) -> tuple[str, list[ImageContent]]:
    """预览装配阶段：为成功保存的图片生成缩略图内容并补充超上限说明。"""
    # 预览从已保存的本地文件生成，未开启、生成失败或无成功保存时退化为纯文本；
    # 超上限仅取前 PREVIEW_MAX_IMAGES 张，完整清单仍在 structuredContent.data。
    preview_contents: list[ImageContent] = []
    if config.preview_enabled and not is_generation_failed and auto_save_results:
        saved_paths = [
            Path(save_result.local_path)
            for save_result in auto_save_results
            if save_result.success and save_result.local_path
        ]
        if len(saved_paths) > PREVIEW_MAX_IMAGES:
            module_logger.info(
                "已保存图片 {} 张超过预览上限 {}，仅生成前 {} 张缩略图预览",
                len(saved_paths),
                PREVIEW_MAX_IMAGES,
                PREVIEW_MAX_IMAGES,
            )
            response_text += (
                f"\n（共已保存 {len(saved_paths)} 张，"
                f"仅附前 {PREVIEW_MAX_IMAGES} 张缩略图预览）"
            )
            saved_paths = saved_paths[:PREVIEW_MAX_IMAGES]
        preview_contents = await build_preview_contents(saved_paths)
    return response_text, preview_contents


async def execute_generation_handler(
    *,
    params: GenerationInputParams,
    config: SeedreamConfig,
    metadata: ToolMetadata,
    module_logger: Logger,
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[dict[str, Any]]
    ],
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """执行生成类工具的统一处理流水线，返回 MCP 结构化工具结果。

    按 request_count 单次或并行调用客户端，按 response_format 自动保存，随后净化
    图片数据并格式化文本与 structuredContent，预览开启且存在成功保存图片时追加缩略图
    ImageContent。任意阶段抛出的异常均降级为 ``is_error=True`` 的结果，不向调用方
    抛出。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        config: 当前生效配置。
        metadata: 工具静态元数据，携带工具名、文案前缀与开始日志模板及参数构造回调。
        module_logger: 调用方模块的 loguru logger。
        request_executor: 执行单次生成请求，由各 impl 提供 client 调用差异。
        ctx: MCP 上下文，用于进度上报，可为 None。

    Returns:
        工具结果，成功时含文本摘要与 structuredContent 及可选缩略图，失败时 isError
        为 True。
    """
    try:
        context = await _prepare_generation_context(
            params=params,
            config=config,
            metadata=metadata,
            ctx=ctx,
            module_logger=module_logger,
        )

        result = await _dispatch_generation_requests(
            config=config,
            context=context,
            ctx=ctx,
            request_executor=request_executor,
            module_logger=module_logger,
        )

        is_generation_failed = _is_generation_failed(result)
        # 图片列表提取一次供自动保存与格式化阶段复用，避免重复提取。
        images = extract_images(result)
        result, images, auto_save_results, saveable_indices, auto_save_error = (
            await _auto_save_generation_images(
                result=result,
                images=images,
                is_generation_failed=is_generation_failed,
                context=context,
                config=config,
                tool_name=metadata.tool_name,
                ctx=ctx,
                module_logger=module_logger,
            )
        )

        # 单一显式净化步骤：净化一次返回新列表，文本与结构化两出口共用同一结果；
        # 净化非幂等，重复净化会使超长片段的截断标记叠加。
        sanitized_images = _sanitize_image_errors(images, aggregated=_is_aggregated_result(result))

        response_text, structured_result = _format_generation_outputs(
            metadata=metadata,
            result=result,
            context=context,
            auto_save_results=auto_save_results,
            auto_save_error=auto_save_error,
            sanitized_images=sanitized_images,
            saveable_indices=saveable_indices,
        )

        preview_contents: list[ImageContent] = []
        if _preview_enabled.get():
            response_text, preview_contents = await _build_generation_preview(
                config=config,
                is_generation_failed=is_generation_failed,
                auto_save_results=auto_save_results,
                module_logger=module_logger,
                response_text=response_text,
            )

        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="请求处理完成")
        return CallToolResult(
            content=[TextContent(type="text", text=response_text), *preview_contents],
            structured_content=structured_result,
            is_error=is_generation_failed,
        )
    except Exception as exc:
        module_logger.error("{}处理失败", metadata.failure_prefix, exc_info=True)
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="请求处理失败")
        user_facing_error = format_error_for_user(exc)
        # 档案已带 user_hint 时文案已含建议，不再叠加查表建议，避免同一句出现两遍。
        if resolve_error_profile(exc).user_hint:
            error_message = f"{metadata.failure_prefix}失败：{user_facing_error}"
        else:
            error_message = (
                f"{metadata.failure_prefix}失败：{user_facing_error}\n"
                f"{_resolve_failure_guidance(exc)}"
            )
        return CallToolResult(
            content=[TextContent(type="text", text=error_message)],
            structured_content=build_error_structured(
                metadata.tool_name,
                _classify_generation_error_type(exc),
                user_facing_error,
            ),
            is_error=True,
        )
