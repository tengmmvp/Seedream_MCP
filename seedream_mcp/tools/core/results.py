"""生成结果处理：图片提取、并行结果聚合、自动保存合并、响应格式化与结构化输出。

各函数为纯数据处理，不触发 I/O：``extract_images`` 将多种响应形态归一化为 list[Dict]；
``aggregate_parallel_generation_results`` 合并多次请求的 data 与 usage 并按成败推导状态；
格式化函数将结果装配为面向模型的文本与 structuredContent 字段。
"""

from __future__ import annotations

from typing import Any

from ...utils.core.errors import sanitize_error_text
from ...utils.io.io_save import AutoSaveResult
from ._helpers import (
    _add_usage_value,
    _classify_generation_error_type,
    _extract_parallel_request_error,
    _is_generation_failed,
)
from .context import GenerationExecutionContext
from .outputs import GenerationStructuredOutput, build_error_dict


def extract_images(result: dict[str, Any]) -> list[dict[str, Any]]:
    """从生成结果中提取图片数据列表。

    支持数组、单个图片字典或嵌套 {"data": ...} 等多种数据结构，统一转换为仅含字典
    的列表；None 与非字典元素一律归一化剔除，确保结果始终符合
    GenerationStructuredOutput.data 的 list[Dict] 声明，避免 outputSchema 校验失败。

    Args:
        result: 图片生成结果字典，包含响应数据及元信息。

    Returns:
        包含图片数据的字典列表，每个字典代表一张图片；无图片时返回空列表。
    """
    data = result.get("data")

    def _coerce(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, list):
            # 过滤 null 及非字典元素，保证 list[Dict] 类型一致。
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = value.get("data")
            if nested is not None:
                return _coerce(nested)
            return [value]
        # str/int 等其他标量类型无法表达图片，归一化为空。
        return []

    return _coerce(data)


def aggregate_parallel_generation_results(
    *,
    request_results: list[dict[str, Any] | None],
    request_errors: dict[int, Exception],
) -> dict[str, Any]:
    """聚合并行请求结果为统一响应结构。

    合并各成功请求的图片与用量统计，失败请求记入 batch.errors；success 由是否有任一成功
    请求决定，status 按 completed/partial/failed 三态推导。
    """
    merged_data: list[dict[str, Any]] = []
    merged_usage: dict[str, Any] = {}
    error_items: list[dict[str, Any]] = []
    success_requests = 0
    request_count = len(request_results)

    for request_index, result in enumerate(request_results, start=1):
        if not result or _is_generation_failed(result):
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
            normalized_image = image.copy()
            normalized_image["request_index"] = request_index
            merged_data.append(normalized_image)

    failed_requests = request_count - success_requests
    status = (
        "completed" if failed_requests == 0 else ("partial" if success_requests > 0 else "failed")
    )
    aggregated_result: dict[str, Any] = {
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
            message = f"并行请求全部失败。{error_preview}"
        else:
            message = "并行请求全部失败"
        # 以首个失败异常为代表，复用与单发路径一致的错误码分类，避免并发全失败
        # 被硬编码为 generation_failed 而与单发路径的错误码契约分叉。
        representative = next(
            (request_errors[i] for i in range(1, request_count + 1) if i in request_errors),
            None,
        )
        error_type = (
            _classify_generation_error_type(representative)
            if representative is not None
            else "generation_failed"
        )
        aggregated_result["error"] = build_error_dict(error_type, message)
    return aggregated_result


def is_saveable_image(image: Any, data_key: str) -> bool:
    """判断图片项是否含可保存数据，即指定键的值非空。

    供自动保存收集阶段筛选待保存图片；回填阶段依据收集时记录的索引列表定位，
    不重复执行此判定。
    """
    return isinstance(image, dict) and bool(image.get(data_key))


def update_result_with_auto_save(
    result: dict[str, Any],
    auto_save_results: list[AutoSaveResult],
    saveable_indices: list[int],
) -> dict[str, Any]:
    """按收集阶段记录的原始索引将自动保存结果合并到生成结果。

    回填严格按 saveable_indices 指定的原始图片位置写入，不重复执行可保存性过滤，
    消除收集与回填两次独立过滤可能错位的风险。不修改原结果对象，返回新的字典副本。
    保存统计与结果列表由 _build_generation_structured_result 从 auto_save_results 直接构建，
    不在此重复写入。

    Args:
        result: 图片生成结果字典，包含原始响应数据。
        auto_save_results: 自动保存结果对象列表。
        saveable_indices: 可保存图片在归一化列表中的原始索引，由收集阶段产出。

    Returns:
        更新后的结果字典，图片项含本地路径与 Markdown 引用。
    """
    updated_result = result.copy()
    # 经 extract_images 将 data 归一化为扁平字典列表，对每个图片项创建浅拷贝并回写。
    # 这样无论原始 data 是列表还是嵌套 {"data": ...} 字典，拷贝都覆盖到实际图片项，
    # 避免下方补充 local_path/markdown_ref 时修改传入的原始 result。
    images = extract_images(updated_result)
    copied_images: list[dict[str, Any]] = [dict(image) for image in images]
    updated_result["data"] = copied_images

    for i, save_result in enumerate(auto_save_results):
        if i >= len(saveable_indices):
            break
        idx = saveable_indices[i]
        if idx >= len(copied_images):
            break
        if save_result.success:
            copied_images[idx]["local_path"] = save_result.local_path
            copied_images[idx]["markdown_ref"] = save_result.markdown_ref

    return updated_result


def _sanitize_image_errors(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """净化图片项内的上游 error.message 与 url 字段，返回净化后的列表。

    仅对净化后内容发生变化的项做浅拷贝替换，其余项原样引用，避免改动调用方传入的
    原结果对象。SSE 失败事件在 io_sse 源头已净化，此处覆盖非 SSE 路径的 per-image
    error 与上游 url，url 可能携带 userinfo 凭据或 CRLF，与 error 通道防护对称。
    """
    sanitized: list[dict[str, Any]] = []
    for image in images:
        item: dict[str, Any] | None = None
        error = image.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            item = dict(image)
            item["error"] = {**error, "message": sanitize_error_text(error["message"])}
        url = image.get("url")
        if isinstance(url, str):
            sanitized_url = sanitize_error_text(url)
            if sanitized_url != url:
                if item is None:
                    item = dict(image)
                item["url"] = sanitized_url
        sanitized.append(item if item is not None else image)
    return sanitized


def _format_failure_section(result: dict[str, Any]) -> str:
    """失败时格式化并行失败详情；无 batch 错误信息时仅返回失败概述。

    error 文本为上游自由内容，出口处过 sanitize_error_text 与异常路径防护一致。
    """
    raw_error = result.get("error", "未知错误")
    # error 形态为 dict 时取其 message，形态为 str 时直接使用，避免字典 repr 进入用户可见文本。
    error_text = sanitize_error_text(
        raw_error.get("message", str(raw_error)) if isinstance(raw_error, dict) else raw_error
    )
    failure_message = f"图片生成失败: {error_text}"
    batch_info = result.get("batch")
    if not isinstance(batch_info, dict):
        return failure_message
    error_items = batch_info.get("errors")
    if not isinstance(error_items, list) or not error_items:
        return failure_message

    parts = [failure_message, "", "并行失败详情:"]
    for item in error_items:
        if not isinstance(item, dict):
            continue
        request_index = item.get("request_index")
        error_message = sanitize_error_text(item.get("message", "请求失败"))
        if request_index is None:
            parts.append(f"  {error_message}")
        else:
            parts.append(f"  请求 {request_index}: {error_message}")
    return "\n".join(parts)


def _format_image_item(index: int, image: dict[str, Any]) -> list[str]:
    """格式化单张图片的可读详情行。"""
    parts = [f"图片 {index}:"]
    if "request_index" in image:
        parts.append(f"  请求序号: {image['request_index']}")
    error_info = image.get("error")
    if isinstance(error_info, dict):
        parts.append("  状态: 失败")
        if error_info.get("code"):
            parts.append(f"  错误码: {error_info['code']}")
        if error_info.get("message"):
            parts.append(f"  错误信息: {sanitize_error_text(error_info['message'])}")
    if image.get("url"):
        # 上游 url 为自由内容，可能携带 userinfo 凭据或 CRLF，输出前统一净化，
        # 与 error 通道的结构化输出防护对称。
        parts.append(f"  URL: {sanitize_error_text(image['url'])}")
    if "size" in image:
        parts.append(f"  尺寸: {image['size']}")
    if "output_format" in image:
        parts.append(f"  输出格式: {image['output_format']}")
    if "image_index" in image:
        parts.append(f"  序号: {image['image_index']}")
    if "local_path" in image:
        parts.append(f"  本地路径: {image['local_path']}")
    if "markdown_ref" in image:
        parts.append(f"  Markdown 引用: {image['markdown_ref']}")
    if "b64_json" in image:
        b64_data = image.get("b64_json")
        if b64_data:
            parts.append(f"  Base64 数据: {len(b64_data)} 字符")
        else:
            parts.append("  Base64 数据: 无")
    parts.append("")
    return parts


def _format_auto_save_section(
    auto_save_results: list[AutoSaveResult] | None,
    auto_save_error: str | None,
    saveable_indices: list[int] | None = None,
) -> list[str]:
    """格式化自动保存统计与逐项结果。

    图片编号基准与图片列表段落一致，取可保存图片在 extract_images 归一化列表中的
    原始索引：失败占位项占号不重排，混合成败时两条「图片 N」指向同一条目。
    saveable_indices 与 auto_save_results 按位置对位；缺失或长度不足时回退保存序号，
    避免编号悬空。
    """
    if auto_save_error:
        return [f"自动保存失败: {auto_save_error}", ""]
    if not auto_save_results:
        return ["自动保存: 已开启但未生成可保存的图片", ""]

    successful_saves = sum(1 for r in auto_save_results if r.success)
    failed_saves = len(auto_save_results) - successful_saves
    parts = [
        "自动保存信息:",
        f"  总图片数: {len(auto_save_results)}",
        f"  成功保存: {successful_saves}",
    ]
    if failed_saves:
        parts.append(f"  保存失败: {failed_saves}")
    for i, save_result in enumerate(auto_save_results):
        if saveable_indices is not None and i < len(saveable_indices):
            display_index = saveable_indices[i] + 1
        else:
            display_index = i + 1
        if save_result.success:
            parts.append(f"  图片 {display_index}: 已保存到 {save_result.local_path}")
        else:
            parts.append(f"  图片 {display_index}: 保存失败 - {save_result.error or '未知原因'}")
    parts.append("")
    return parts


def _format_usage_section(usage: dict[str, Any]) -> list[str]:
    """格式化使用统计。"""
    parts = ["使用统计:"]
    if "input_images" in usage:
        parts.append(f"  输入图片数: {usage['input_images']}")
    if "generated_images" in usage:
        parts.append(f"  生成图片数: {usage['generated_images']}")
    if "output_tokens" in usage:
        parts.append(f"  输出 tokens: {usage['output_tokens']}")
    if "total_tokens" in usage:
        parts.append(f"  总 tokens: {usage['total_tokens']}")
    tool_usage = usage.get("tool_usage")
    if isinstance(tool_usage, dict):
        web_search_count = tool_usage.get("web_search")
        if isinstance(web_search_count, int) and web_search_count > 0:
            parts.append(f"  联网搜索: {web_search_count} 次")
    parts.append("")
    return parts


def format_generation_response(
    title: str,
    result: dict[str, Any],
    prompt: str,
    size: str,
    auto_save_results: list[AutoSaveResult] | None = None,
    auto_save_enabled: bool = False,
    auto_save_error: str | None = None,
    images: list[dict[str, Any]] | None = None,
    saveable_indices: list[int] | None = None,
) -> str:
    """格式化图片生成结果为可读文本。

    将生成结果、提示词、尺寸、保存信息及使用统计等数据，
    按规范化格式输出为结构清晰的多行文本字符串。

    Args:
        title: 响应标题，用于标识生成任务类型。
        result: 图片生成结果字典，包含图片数据及使用统计。
        prompt: 生成图片所用的提示词。
        size: 生成图片的尺寸规格。
        auto_save_results: 自动保存结果列表，可选。
        auto_save_enabled: 是否启用自动保存功能，默认 False。
        auto_save_error: 自动保存错误信息，存在时表示已降级跳过自动保存。
        images: 预提取的图片列表，传入时跳过内部 extract_images 以避免重复计算；
            None 时按需从 result 提取，便于函数独立调用。
        saveable_indices: 可保存图片在归一化图片列表中的原始索引，由自动保存收集
            阶段产出并与 auto_save_results 按位置对位；传入时自动保存段落的图片编号
            与图片列表编号同基准。None 时回退保存序号。

    Returns:
        格式化后的响应文本，包含完整生成信息及元数据。
    """
    if _is_generation_failed(result):
        return _format_failure_section(result)

    if images is None:
        images = extract_images(result)
    usage = result.get("usage", {})

    parts: list[str] = [title, f"提示词: {prompt}", f"尺寸: {size}", ""]

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
        parts.extend(_format_image_item(i, image))

    if auto_save_enabled:
        parts.extend(
            _format_auto_save_section(auto_save_results, auto_save_error, saveable_indices)
        )

    if usage:
        parts.extend(_format_usage_section(usage))

    return "\n".join(parts)


def _build_generation_structured_result(
    *,
    tool_name: str,
    result: dict[str, Any],
    context: GenerationExecutionContext,
    auto_save_results: list[AutoSaveResult] | None,
    auto_save_error: str | None,
    images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构建 MCP 工具结果的 structuredContent 字段。

    经 GenerationStructuredOutput 构造后 model_dump，使 runtime 输出与声明的
    outputSchema 绑定，字段漂移在构造时即暴露。成功与失败均返回同一结构，失败时额外
    写入归一化后的 error 字典；成功路径不输出 error 键，与既有契约一致。

    Args:
        tool_name: 工具标识。
        result: 图片生成结果字典。
        context: 执行上下文，提供面向 schema 的参数字段。
        auto_save_results: 自动保存结果对象列表。
        auto_save_error: 自动保存错误信息。
        images: 预提取的图片列表，传入时直接写入 data，避免重复调用 extract_images；
            None 时从 result 提取，便于函数独立调用。
    """
    # b64_json 模式下 data 内的完整 base64 为有意保留：用户显式请求 b64 即期望取回图像
    # 数据，故此处不做截断；并行与组图场景的大载荷由调用方或客户端按需处理。
    payload: dict[str, Any] = {
        "tool": tool_name,
        "success": not _is_generation_failed(result),
        "status": result.get("status"),
        "prompt": context.prompt,
        "size": context.size,
        "response_format": context.response_format,
        "output_format": context.output_format,
        "stream": context.stream,
        "tools": context.tools,
        "request_count": context.request_count,
        "parallelism": context.parallelism,
        # data 项可能携带上游 per-image error 自由文本，统一净化后进入结构化输出，
        # 与异常路径防护一致；仅对含 error 的项浅拷贝，避免改动原结果对象。
        "data": _sanitize_image_errors(images if images is not None else extract_images(result)),
        "usage": result.get("usage", {}),
        "batch": result.get("batch"),
    }

    if context.enable_auto_save:
        payload["auto_save"] = {
            "enabled": True,
            "error": sanitize_error_text(auto_save_error),
            "results": [r.to_dict() for r in auto_save_results] if auto_save_results else [],
        }
    else:
        payload["auto_save"] = {"enabled": False}

    failed = _is_generation_failed(result)
    if failed:
        raw_error = result.get("error", "未知错误")
        if isinstance(raw_error, dict):
            sanitized_error = dict(raw_error)
            if isinstance(sanitized_error.get("message"), str):
                sanitized_error["message"] = sanitize_error_text(sanitized_error["message"])
            payload["error"] = sanitized_error
        else:
            payload["error"] = build_error_dict("generation_failed", str(raw_error))

    output = GenerationStructuredOutput(**payload)
    if failed:
        return output.model_dump()
    return output.model_dump(exclude={"error"})
