"""
通用工具处理辅助函数
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Sequence

from mcp.types import CallToolResult, TextContent

from ...config import SeedreamConfig
from ...utils.auto_save import AutoSaveManager
from ...utils.errors import SeedreamValidationError, format_error_for_user
from ...utils.logging import get_logger
from ...utils.path_utils import get_workspace_root, is_path_within_base, normalize_path
from ...utils.validation import (
    validate_generation_tools,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_parallel_generation_options,
    validate_response_format,
    validate_size_for_model,
    validate_watermark,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from ...client import SeedreamClient

logger = get_logger(__name__)


# ==================== 数据类定义 ====================


@dataclass(frozen=True)
class GenerationExecutionContext:
    """
    生成类工具执行上下文

    统一封装四类生成工具共享参数，避免在各 handler 中重复提取与校验。
    """

    prompt: str
    optimize_prompt_options: Optional[Dict[str, Any]]
    size: str
    watermark: bool
    response_format: str
    output_format: Optional[str]
    stream: bool
    tools: Optional[List[Dict[str, Any]]]
    request_count: int
    parallelism: int
    enable_auto_save: bool
    save_path: Optional[str]
    custom_name: Optional[str]


# ==================== 底层辅助函数 ====================


def _add_usage_value(usage: Dict[str, Any], key: str, value: Any) -> None:
    """
    累加用量统计。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    current = usage.get(key, 0)
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        current = 0
    usage[key] = current + value


def _normalize_error_message(raw_error: Any) -> Optional[str]:
    """
    将不同形态的错误对象提取为可读文本。
    """
    if isinstance(raw_error, str):
        message = raw_error.strip()
        return message or None

    if not isinstance(raw_error, dict):
        return None

    for key in ("message", "msg", "detail", "error"):
        value = raw_error.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    code = raw_error.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip()
    return None


def _extract_parallel_request_error(
    result: Optional[Dict[str, Any]], fallback_error: Optional[str]
) -> str:
    """
    提取单个并行请求的失败原因，优先使用结果内错误信息。
    """
    if isinstance(result, dict):
        direct_error = _normalize_error_message(result.get("error"))
        if direct_error:
            return direct_error

        data = result.get("data")
        if isinstance(data, dict):
            nested_error = _normalize_error_message(data.get("error"))
            if nested_error:
                return nested_error
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                item_error = _normalize_error_message(item.get("error"))
                if item_error:
                    return item_error

    fallback = _normalize_error_message(fallback_error)
    return fallback or "请求失败"


def _resolve_base_dir(config: SeedreamConfig, save_path: Optional[str]) -> Path:
    """
    解析自动保存的基础目录路径

    优先使用用户指定路径，若未指定则使用配置中的默认路径。

    Args:
        config: Seedream 配置实例，包含自动保存相关参数。
        save_path: 用户指定的保存路径，可选。

    Returns:
        解析后的安全路径对象。
    """
    workspace_root = get_workspace_root()
    default_base_dir = (
        Path(config.auto_save_base_dir).expanduser().resolve()
        if config.auto_save_base_dir
        else (workspace_root / "images").resolve()
    )

    if not save_path:
        return default_base_dir

    try:
        user_path = normalize_path(save_path, str(default_base_dir))
    except ValueError as exc:
        raise SeedreamValidationError(f"保存路径无效: {exc}", field="save_path", value=save_path)

    if not is_path_within_base(user_path, default_base_dir):
        raise SeedreamValidationError(
            f"save_path 超出允许范围: {default_base_dir}",
            field="save_path",
            value=save_path,
        )

    return user_path


def extract_images(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    从生成结果中提取图片数据列表

    支持多种数据结构，兼容嵌套字典与数组格式，统一转换为列表输出。

    Args:
        result: 图片生成结果字典，包含响应数据及元信息。

    Returns:
        包含图片数据的字典列表，每个字典代表一张图片的完整信息。
    """
    data = result.get("data", {})
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data:
        nested = data.get("data", [])
        return nested if isinstance(nested, list) else [nested]
    return [data]


async def _safe_report_progress(
    ctx: Optional["Context[Any, Any, Any]"],
    *,
    progress: float,
    total: float = 100.0,
    message: str,
) -> None:
    """
    在支持进度能力的 MCP 会话中上报进度；上报失败不影响主流程。
    """
    if ctx is None:
        return

    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except Exception as exc:
        logger.debug("进度上报失败，已忽略: {}", exc)


async def _yield_for_cancellation() -> None:
    """
    协作式让出执行权，确保取消信号能尽快生效。
    """
    await asyncio.sleep(0)


def _build_generation_structured_result(
    *,
    tool_name: str,
    result: Dict[str, Any],
    context: GenerationExecutionContext,
    auto_save_results: Optional[List[Any]],
    auto_save_error: Optional[str],
) -> Dict[str, Any]:
    """
    构建 MCP 工具结果的结构化字段。
    """
    structured: Dict[str, Any] = {
        "tool": tool_name,
        "success": bool(result.get("success")),
        "status": result.get("status"),
        "prompt": context.prompt,
        "size": context.size,
        "response_format": context.response_format,
        "output_format": context.output_format,
        "stream": context.stream,
        "tools": context.tools,
        "request_count": context.request_count,
        "parallelism": context.parallelism,
        "data": result.get("data", []),
        "usage": result.get("usage", {}),
        "batch": result.get("batch"),
    }

    if context.enable_auto_save:
        structured["auto_save"] = {
            "enabled": True,
            "error": auto_save_error,
            "results": [r.to_dict() for r in auto_save_results] if auto_save_results else [],
        }
    else:
        structured["auto_save"] = {"enabled": False}

    if not result.get("success"):
        structured["error"] = result.get("error", "未知错误")

    return structured


# ==================== 上下文构建函数 ====================


def build_generation_context(
    arguments: Dict[str, Any], config: SeedreamConfig
) -> GenerationExecutionContext:
    """
    从工具参数构建统一执行上下文

    Args:
        arguments: 工具原始参数字典。
        config: 当前生效配置。

    Returns:
        统一执行上下文对象。
    """
    prompt = arguments.get("prompt", "")
    optimize_prompt_options = arguments.get("optimize_prompt_options")
    raw_size = arguments.get("size") if "size" in arguments else None
    watermark_value = arguments.get("watermark")
    response_format = arguments.get("response_format", "url")
    output_format = arguments.get("output_format")
    stream = bool(arguments.get("stream", False))
    tools = arguments.get("tools")
    request_count = arguments.get("request_count", 1)
    parallelism_value = arguments.get("parallelism")
    auto_save = arguments.get("auto_save")
    save_path = arguments.get("save_path")
    custom_name = arguments.get("custom_name")

    validated_optimize_options = validate_optimize_prompt_options(
        optimize_prompt_options, config.model_id
    )

    size_value = config.default_size if raw_size is None else raw_size
    validated_size = validate_size_for_model(size_value, config.model_id)

    watermark = (
        validate_watermark(watermark_value)
        if watermark_value is not None
        else config.default_watermark
    )

    validated_response_format = validate_response_format(response_format)
    validated_output_format = validate_output_format(output_format, config.model_id)
    validated_tools = validate_generation_tools(tools, config.model_id)

    request_count, parallelism = validate_parallel_generation_options(
        request_count=request_count,
        parallelism=parallelism_value,
        stream=stream,
        max_request_count=4,
    )

    enable_auto_save = auto_save if auto_save is not None else config.auto_save_enabled

    return GenerationExecutionContext(
        prompt=prompt,
        optimize_prompt_options=validated_optimize_options,
        size=validated_size,
        watermark=watermark,
        response_format=validated_response_format,
        output_format=validated_output_format,
        stream=stream,
        tools=validated_tools,
        request_count=request_count,
        parallelism=parallelism,
        enable_auto_save=enable_auto_save,
        save_path=save_path,
        custom_name=custom_name,
    )


# ==================== 结果处理函数 ====================


def aggregate_parallel_generation_results(
    *,
    request_results: List[Optional[Dict[str, Any]]],
    request_errors: Dict[int, str],
) -> Dict[str, Any]:
    """
    聚合并行请求结果为统一响应结构。
    """
    merged_data: List[Dict[str, Any]] = []
    merged_usage: Dict[str, Any] = {}
    error_items: List[Dict[str, Any]] = []
    success_requests = 0
    request_count = len(request_results)

    for request_index, result in enumerate(request_results, start=1):
        if not result or not result.get("success"):
            error_message = _extract_parallel_request_error(
                result, request_errors.get(request_index)
            )
            error_items.append({"request_index": request_index, "message": error_message})
            merged_data.append(
                {
                    "type": "image_generation.request_failed",
                    "request_index": request_index,
                    "error": {"message": error_message},
                }
            )
            continue

        success_requests += 1
        usage = result.get("usage", {})
        if isinstance(usage, dict):
            for key, value in usage.items():
                _add_usage_value(merged_usage, key, value)

        images = extract_images(result)
        for image in images:
            if not isinstance(image, dict):
                continue
            normalized_image = image.copy()
            normalized_image["request_index"] = request_index
            merged_data.append(normalized_image)

    failed_requests = request_count - success_requests
    status = (
        "completed"
        if failed_requests == 0
        else ("partial_completed" if success_requests > 0 else "failed")
    )
    aggregated_result: Dict[str, Any] = {
        "success": success_requests > 0,
        "data": merged_data,
        "usage": merged_usage,
        "status": status,
        "batch": {
            "request_count": request_count,
            "success_requests": success_requests,
            "failed_requests": failed_requests,
            "errors": error_items,
        },
    }
    if success_requests == 0:
        if error_items:
            error_preview = "；".join(
                f"请求{item['request_index']}: {item['message']}" for item in error_items[:3]
            )
            if len(error_items) > 3:
                error_preview += f"；其余 {len(error_items) - 3} 个请求也失败"
            aggregated_result["error"] = f"并行请求全部失败。{error_preview}"
        else:
            aggregated_result["error"] = "并行请求全部失败"
    return aggregated_result


def update_result_with_auto_save(result: Dict[str, Any], auto_save_results: List) -> Dict[str, Any]:
    """
    将自动保存结果合并到生成结果中

    在原结果基础上添加保存统计信息，并为可保存图片补充本地路径和 Markdown 引用，
    不修改原结果对象，返回新的字典副本。

    Args:
        result: 图片生成结果字典,包含原始响应数据。
        auto_save_results: 自动保存结果对象列表。

    Returns:
        更新后的结果字典,包含自动保存信息及本地路径。
    """
    updated_result = result.copy()

    auto_save_info = {
        "enabled": True,
        "total_images": len(auto_save_results),
        "successful_saves": sum(1 for r in auto_save_results if getattr(r, "success", False)),
        "failed_saves": sum(1 for r in auto_save_results if not getattr(r, "success", False)),
        "results": [r.to_dict() for r in auto_save_results],
    }
    updated_result["auto_save"] = auto_save_info

    images = extract_images(updated_result)
    auto_save_iter = iter(auto_save_results)
    for image in images:
        if not isinstance(image, dict):
            continue
        # 自动保存仅处理包含图片数据的项
        if not (image.get("url") or image.get("b64_json")):
            continue

        save_result = next(auto_save_iter, None)
        if save_result is None:
            break

        if getattr(save_result, "success", False):
            image["local_path"] = save_result.local_path
            image["markdown_ref"] = save_result.markdown_ref

    return updated_result


def format_generation_response(
    title: str,
    result: Dict[str, Any],
    prompt: str,
    size: str,
    auto_save_results: Optional[List] = None,
    auto_save_enabled: bool = False,
    auto_save_error: Optional[str] = None,
) -> str:
    """
    格式化图片生成结果为可读文本

    将生成结果、提示词、尺寸、保存信息及使用统计等数据，
    按规范化格式输出为结构清晰的多行文本字符串。

    Args:
        title: 响应标题,用于标识生成任务类型。
        result: 图片生成结果字典,包含图片数据及使用统计。
        prompt: 生成图片所用的提示词。
        size: 生成图片的尺寸规格。
        auto_save_results: 自动保存结果列表,可选。
        auto_save_enabled: 是否启用自动保存功能,默认 False。
        auto_save_error: 自动保存错误信息，存在时表示已降级跳过自动保存。

    Returns:
        格式化后的响应文本,包含完整生成信息及元数据。
    """
    if not result.get("success"):
        failure_message = f"图片生成失败: {result.get('error', '未知错误')}"
        batch_info = result.get("batch")
        if not isinstance(batch_info, dict):
            return failure_message
        error_items = batch_info.get("errors")
        if not isinstance(error_items, list) or not error_items:
            return failure_message

        failure_parts = [failure_message, "", "并行失败详情:"]
        for item in error_items:
            if not isinstance(item, dict):
                continue
            request_index = item.get("request_index")
            error_message = item.get("message", "请求失败")
            if request_index is None:
                failure_parts.append(f"  {error_message}")
            else:
                failure_parts.append(f"  请求 {request_index}: {error_message}")
        return "\n".join(failure_parts)

    images = extract_images(result)
    usage = result.get("usage", {})

    parts: List[str] = []
    parts.append(title)
    parts.append(f"提示词: {prompt}")
    parts.append(f"尺寸: {size}")
    parts.append("")

    batch_info = result.get("batch")
    if isinstance(batch_info, dict):
        parts.append("并行请求信息:")
        if "request_count" in batch_info:
            parts.append(f"  请求总数: {batch_info['request_count']}")
        if "success_requests" in batch_info:
            parts.append(f"  成功请求: {batch_info['success_requests']}")
        if "failed_requests" in batch_info:
            parts.append(f"  失败请求: {batch_info['failed_requests']}")
        parts.append("")

    for i, image in enumerate(images, 1):
        if isinstance(image, dict):
            parts.append(f"图片 {i}:")
            if "request_index" in image:
                parts.append(f"  请求序号: {image['request_index']}")
            error_info = image.get("error")
            if isinstance(error_info, dict):
                parts.append("  状态: 失败")
                if error_info.get("code"):
                    parts.append(f"  错误码: {error_info['code']}")
                if error_info.get("message"):
                    parts.append(f"  错误信息: {error_info['message']}")
            if image.get("url"):
                parts.append(f"  URL: {image['url']}")
            if "size" in image:
                parts.append(f"  尺寸: {image['size']}")
            if "image_index" in image:
                parts.append(f"  序号: {image['image_index']}")
            if "local_path" in image:
                parts.append(f"  本地路径: {image['local_path']}")
            if "markdown_ref" in image:
                parts.append(f"  Markdown 引用: {image['markdown_ref']}")
            if "b64_json" in image:
                b64_data = image.get("b64_json")
                parts.append(
                    f"  Base64 数据: {len(b64_data)} 字符" if b64_data else "  Base64 数据: 无"
                )
            parts.append("")

    if auto_save_enabled:
        if auto_save_error:
            parts.append(f"自动保存失败: {auto_save_error}")
            parts.append("")
        elif auto_save_results:
            parts.append("自动保存信息:")
            successful_saves = sum(1 for r in auto_save_results if getattr(r, "success", False))
            failed_saves = len(auto_save_results) - successful_saves
            parts.append(f"  总图片数: {len(auto_save_results)}")
            parts.append(f"  成功保存: {successful_saves}")
            if failed_saves:
                parts.append(f"  保存失败: {failed_saves}")
            for i, save_result in enumerate(auto_save_results, 1):
                if getattr(save_result, "success", False):
                    parts.append(f"  图片 {i}: 已保存到 {save_result.local_path}")
                else:
                    parts.append(
                        f"  图片 {i}: 保存失败 - {getattr(save_result, 'error', '未知原因')}"
                    )
            parts.append("")
        else:
            parts.append("自动保存: 已开启但未生成可保存的图片")
            parts.append("")

    if usage:
        parts.append("使用统计:")
        if "generated_images" in usage:
            parts.append(f"  生成图片数: {usage['generated_images']}")
        if "output_tokens" in usage:
            parts.append(f"  输出 tokens: {usage['output_tokens']}")
        if "total_tokens" in usage:
            parts.append(f"  总 tokens: {usage['total_tokens']}")
        if "prompt_tokens" in usage:
            parts.append(f"  提示词 tokens: {usage['prompt_tokens']}")
        if "completion_tokens" in usage:
            parts.append(f"  完成 tokens: {usage['completion_tokens']}")
        if "cost" in usage:
            parts.append(f"  成本: {usage['cost']}")
        parts.append("")

    return "\n".join(parts)


# ==================== 自动保存函数 ====================


async def auto_save_from_urls(
    result: Dict[str, Any],
    prompt: str,
    config: SeedreamConfig,
    save_path: Optional[str],
    custom_name: Optional[str],
    tool_name: str,
) -> List:
    """
    从 URL 异步下载并保存图片

    根据配置项自动解析基础目录，支持批量下载并记录保存结果，
    包含超时控制、重试机制及并发管理。

    Args:
        result: 图片生成结果字典，包含 URL 等信息。
        prompt: 生成图片所用的提示词，用于元数据记录。
        config: Seedream 配置实例，包含保存参数。
        save_path: 用户指定的保存路径，可选。
        custom_name: 自定义文件名前缀，可选。
        tool_name: 工具名称标识，用于路径组织。

    Returns:
        保存结果对象列表，每个对象包含成功状态、路径及错误信息。
    """
    base_dir = _resolve_base_dir(config, save_path)
    auto_save_manager = AutoSaveManager(
        base_dir=base_dir,
        download_timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
        max_concurrent=config.auto_save_max_concurrent,
        date_folder=config.auto_save_date_folder,
        cleanup_days=config.auto_save_cleanup_days,
    )

    images = extract_images(result)
    image_data = []
    for i, image in enumerate(images):
        if isinstance(image, dict) and image.get("url"):
            image_data.append(
                {
                    "url": image["url"],
                    "prompt": prompt,
                    "custom_name": f"{custom_name}_{i + 1}" if custom_name else None,
                    "alt_text": f"Generated image {i + 1}",
                }
            )

    if not image_data:
        logger.warning("未找到可保存的图片 URL")
        await auto_save_manager.close()
        return []

    try:
        return await auto_save_manager.save_multiple_images(image_data, tool_name=tool_name)
    finally:
        await auto_save_manager.close()


async def auto_save_from_base64(
    result: Dict[str, Any],
    prompt: str,
    config: SeedreamConfig,
    save_path: Optional[str],
    custom_name: Optional[str],
    tool_name: str,
) -> List:
    """
    从 Base64 数据异步解码并保存图片

    根据配置项自动解析基础目录，支持批量解码并保存，
    包含文件大小限制、重试机制及并发管理。

    Args:
        result: 图片生成结果字典,包含 b64_json 等信息。
        prompt: 生成图片所用的提示词,用于元数据记录。
        config: Seedream 配置实例,包含保存参数。
        save_path: 用户指定的保存路径,可选。
        custom_name: 自定义文件名前缀,可选。
        tool_name: 工具名称标识,用于路径组织。

    Returns:
        保存结果对象列表,每个对象包含成功状态、路径及错误信息。
    """
    base_dir = _resolve_base_dir(config, save_path)
    auto_save_manager = AutoSaveManager(
        base_dir=base_dir,
        download_timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
        max_concurrent=config.auto_save_max_concurrent,
        date_folder=config.auto_save_date_folder,
        cleanup_days=config.auto_save_cleanup_days,
    )

    images = extract_images(result)
    image_data = []
    for i, image in enumerate(images):
        if isinstance(image, dict) and image.get("b64_json"):
            image_data.append(
                {
                    "b64_json": image["b64_json"],
                    "prompt": prompt,
                    "custom_name": f"{custom_name}_{i + 1}" if custom_name else None,
                    "alt_text": f"Generated image {i + 1}",
                }
            )

    if not image_data:
        logger.warning("未找到可保存的 base64 图片数据")
        await auto_save_manager.close()
        return []

    try:
        return await auto_save_manager.save_multiple_base64_images(image_data, tool_name=tool_name)
    finally:
        await auto_save_manager.close()


# ==================== 并行执行函数 ====================


async def execute_parallel_generation_requests(
    *,
    client: "SeedreamClient",
    context: GenerationExecutionContext,
    request_executor: Callable[
        ["SeedreamClient", GenerationExecutionContext], Awaitable[Dict[str, Any]]
    ],
    module_logger: Any,
    ctx: Optional["Context[Any, Any, Any]"] = None,
    progress_start: float = 20.0,
    progress_span: float = 50.0,
) -> Dict[str, Any]:
    """
    按并发上限执行多次生成请求并聚合结果。
    """
    semaphore = asyncio.Semaphore(context.parallelism)
    request_results: List[Optional[Dict[str, Any]]] = [None] * context.request_count
    request_errors: Dict[int, str] = {}
    completed_requests = 0
    progress_lock = asyncio.Lock()

    async def _run_single_request(request_index: int) -> None:
        nonlocal completed_requests
        async with semaphore:
            await _yield_for_cancellation()
            try:
                request_results[request_index - 1] = await request_executor(client, context)
            except Exception as exc:
                request_errors[request_index] = format_error_for_user(exc)
                module_logger.warning(
                    "并行请求 {}/{} 失败: {}",
                    request_index,
                    context.request_count,
                    request_errors[request_index],
                )
            finally:
                async with progress_lock:
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


# ==================== 主执行器函数 ====================


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

        module_logger.info(start_log_message, *start_log_values_builder(context))

        async with SeedreamClient(config) as client:
            if context.request_count == 1:
                await _safe_report_progress(ctx, progress=20.0, message="开始调用图像生成接口")
                await _yield_for_cancellation()
                result = await request_executor(client, context)
                await _safe_report_progress(ctx, progress=70.0, message="图像生成完成")
            else:
                await _safe_report_progress(
                    ctx,
                    progress=20.0,
                    message=f"开始并行请求，共 {context.request_count} 次",
                )
                result = await execute_parallel_generation_requests(
                    client=client,
                    context=context,
                    request_executor=request_executor,
                    module_logger=module_logger,
                    ctx=ctx,
                )
                await _safe_report_progress(ctx, progress=70.0, message="并行请求执行完成")

        auto_save_results: List[Any] = []
        auto_save_error: Optional[str] = None
        if context.enable_auto_save and result.get("success"):
            try:
                await _safe_report_progress(ctx, progress=75.0, message="开始自动保存")
                await _yield_for_cancellation()
                if context.response_format == "url":
                    auto_save_results = await auto_save_from_urls(
                        result,
                        context.prompt,
                        config,
                        context.save_path,
                        context.custom_name,
                        tool_name,
                    )
                else:
                    auto_save_results = await auto_save_from_base64(
                        result,
                        context.prompt,
                        config,
                        context.save_path,
                        context.custom_name,
                        tool_name,
                    )

                if auto_save_results:
                    result = update_result_with_auto_save(result, auto_save_results)
                await _safe_report_progress(ctx, progress=95.0, message="自动保存完成")
            except Exception as exc:
                auto_save_error = format_error_for_user(exc)
                module_logger.warning("自动保存失败，已降级跳过: {}", auto_save_error)

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
        return CallToolResult(
            content=[TextContent(type="text", text=response_text)],
            structuredContent=structured_result,
            isError=not bool(result.get("success")),
        )
    except Exception as exc:
        module_logger.error(f"{failure_prefix}处理失败", exc_info=True)
        await _safe_report_progress(ctx, progress=100.0, message="请求处理失败")
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
