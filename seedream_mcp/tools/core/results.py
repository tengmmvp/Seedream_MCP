"""生成结果处理：图片提取、并行结果聚合、自动保存合并、响应格式化与结构化输出。

各函数为纯数据处理，不触发 I/O，将多种响应形态归一化为统一的图片列表，再装配为
面向模型的文本与 structuredContent；输出净化子系统位于 _sanitize 模块。
"""

from __future__ import annotations

from typing import Any

from ...utils.core.sanitizers import (
    normalize_message_text,
    sanitize_error_text,
)
from ...utils.io.io_save import AutoSaveResult
from ._sanitize import (
    _REQUEST_FAILED_TYPE,
    _sanitize_image_errors,
    _sanitize_usage,
    message_limit_for,
    sanitize_error_dict,
)
from ._shared import (
    _add_usage_value,
    _classify_generation_error_type,
    _extract_parallel_request_error,
    _is_generation_failed,
    _normalize_error_message,
)
from .context import GenerationExecutionContext
from .outputs import GenerationStructuredOutput, build_error_dict

# 嵌套 data 下钻深度上限，仅兜底受损上游注入的环状或超深结构，超限归一为空。
_MAX_NESTED_DATA_DEPTH = 10_000


def extract_images(result: dict[str, Any]) -> list[dict[str, Any]]:
    """从生成结果中提取图片数据列表。

    支持数组、单个图片字典或嵌套 {"data": ...} 形态，None 与非字典元素一律剔除，
    保证返回值始终为 list[dict]，符合 GenerationStructuredOutput.data 的声明。

    Args:
        result: 图片生成结果字典。

    Returns:
        图片字典列表，每个字典代表一张图片；无图片时返回空列表。
    """
    data = result.get("data")

    def _coerce(value: Any) -> list[dict[str, Any]]:
        """将任意取值归一化为仅含图片字典的列表。

        嵌套 ``{"data": ...}`` 经迭代下钻，深嵌套不触发递归上限；深度超限归一为空。
        """
        depth = 0
        while True:
            if value is None:
                return []
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = value.get("data")
                if nested is not None:
                    depth += 1
                    if depth > _MAX_NESTED_DATA_DEPTH:
                        return []
                    value = nested
                    continue
                return [value]
            return []

    return _coerce(data)


def aggregate_parallel_generation_results(
    *,
    request_results: list[dict[str, Any] | None],
    request_errors: dict[int, Exception],
) -> dict[str, Any]:
    """聚合并行请求结果为统一响应结构。

    合并各请求的图片与用量（软失败请求的用量同样计入，失败请求上游也可能计量），
    失败请求记入 batch.errors；status 按 completed/partial/failed 三态推导，任一
    成功请求自身为 partial 时批次至多为 partial。全部失败时以首个失败异常分类
    错误码，无异常的软失败结果透传上游 error.code，与单发路径的错误码契约一致。

    Args:
        request_results: 各请求结果列表，失败或异常时对应位置为 None。
        request_errors: 请求序号到异常的映射，序号从 1 起。

    Returns:
        聚合后的响应字典，含 success、data、usage、status 与 batch 键，全部失败时
        另含 error 键。
    """
    merged_data: list[dict[str, Any]] = []
    merged_usage: dict[str, Any] = {}
    error_items: list[dict[str, Any]] = []
    success_requests = 0
    partial_requests = 0
    request_count = len(request_results)

    for request_index, result in enumerate(request_results, start=1):
        if not result or _is_generation_failed(result):
            request_exc = request_errors.get(request_index)
            error_message = _extract_parallel_request_error(result, request_exc)
            error_items.append({"request_index": request_index, "message": error_message})
            # 软失败请求的用量照常合并：失败请求上游也可能计量，计费核对不因批次
            # 聚合漏计。
            usage = result.get("usage") if result else None
            if isinstance(usage, dict):
                for key, value in usage.items():
                    _add_usage_value(merged_usage, key, value)
            # 占位项 error 与 build_error_dict 同契约：type 取异常归约码，软失败结果
            # 无异常时维持 generation_failed 兜底档。
            merged_data.append(
                {
                    "type": _REQUEST_FAILED_TYPE,
                    "request_index": request_index,
                    "error": {
                        "type": (
                            _classify_generation_error_type(request_exc)
                            if request_exc is not None
                            else "generation_failed"
                        ),
                        "message": error_message,
                    },
                }
            )
            continue

        success_requests += 1
        if result.get("status") == "partial":
            partial_requests += 1
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
    # request_count 为 0 的空批次无产出，与零成功同归 failed，避免 success=False 而
    # status=completed 的自相矛盾形态。
    if failed_requests == 0 and partial_requests == 0 and request_count > 0:
        status = "completed"
    elif success_requests > 0:
        status = "partial"
    else:
        status = "failed"
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
        representative = next(
            (request_errors[i] for i in range(1, request_count + 1) if i in request_errors),
            None,
        )
        if representative is not None:
            error_type = _classify_generation_error_type(representative)
            aggregated_result["error"] = build_error_dict(error_type, message)
        else:
            # 软失败结果提取上游 error.code 透传，出口处净化；未提取到时维持
            # generation_failed 兜底。
            error_payload = build_error_dict("generation_failed", message)
            for result in request_results:
                if not isinstance(result, dict):
                    continue
                raw_error = result.get("error")
                if not isinstance(raw_error, dict):
                    continue
                raw_code = raw_error.get("code")
                if isinstance(raw_code, str) and raw_code.strip():
                    error_payload["code"] = raw_code.strip()
                    break
            aggregated_result["error"] = error_payload
    return aggregated_result


def is_saveable_image(image: Any, data_key: str) -> bool:
    """判断图片项的指定键取值非空，供自动保存收集阶段筛选待保存图片。"""
    return isinstance(image, dict) and bool(image.get(data_key))


def update_result_with_auto_save(
    result: dict[str, Any],
    auto_save_results: list[AutoSaveResult],
    saveable_indices: list[int],
) -> dict[str, Any]:
    """按收集阶段记录的原始索引将自动保存结果合并到生成结果。

    不修改原结果对象，返回新副本；保存统计由 _build_generation_structured_result 从
    auto_save_results 直接构建，不在此写入。

    Args:
        result: 待合并的生成结果字典。
        auto_save_results: 自动保存结果列表，与 saveable_indices 按位置对位。
        saveable_indices: 可保存图片在归一化列表中的原始索引，由收集阶段产出。

    Returns:
        更新后的结果字典，图片项含本地路径与 Markdown 引用。
    """
    updated_result = result.copy()
    # 归一化为扁平字典列表并逐项浅拷贝，回填时不修改传入的原始 result。
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


def _is_aggregated_result(result: dict[str, Any]) -> bool:
    """判定结果是否由并行聚合格式产出。

    batch 键仅由 aggregate_parallel_generation_results 写入，client 的单请求归一化
    不透传该键；聚合格式的 error 与 batch.errors 的 message 为本侧组装产物
    （format_error_for_user 逐段净化后拼装，含恢复指引，可超错误通道上限），
    出口侧据此改用防御性宽上限净化。
    """
    return isinstance(result.get("batch"), dict)


def _aggregated_message_limit(result: dict[str, Any]) -> int:
    """消息净化上限：聚合组装产物走防御性宽限，判定单点见 message_limit_for。"""
    return message_limit_for(_is_aggregated_result(result))


def _format_failure_section(result: dict[str, Any]) -> str:
    """失败时格式化并行失败详情；无 batch 错误信息时仅返回失败概述。

    error 为上游自由内容，一律经净化与归一化输出：dict 形态优先按
    message/msg/detail/error/code 阶梯提取，未命中时归一化 message 分量；空值回落
    未知错误，字面量 None 与字典 repr 不进入用户可见文本。聚合格式的消息为本侧
    组装产物（含恢复指引），净化改用防御性宽上限防截断指引；脱敏口径两档一致。
    """
    aggregated = _is_aggregated_result(result)
    message_limit = message_limit_for(aggregated)

    # 空值归入未知错误，与结构化出口口径一致。
    raw_error = result.get("error") or "未知错误"
    if isinstance(raw_error, dict):
        if aggregated:
            # 聚合 error 为本侧组装的 {type, message} 结构，直取 message 走宽限
            # 净化；五级阶梯服务于上游未知结构，其内部固定错误通道上限。
            message = raw_error.get("message")
            error_text = (
                sanitize_error_text(message, limit=message_limit)
                if isinstance(message, str) and message
                else "未知错误"
            )
        else:
            # dict 形态优先复用五级提取阶梯，与并行聚合的错误提取同口径。
            ladder_text = _normalize_error_message(raw_error)
            if ladder_text is None:
                message = raw_error.get("message")
                error_text = (
                    sanitize_error_text(normalize_message_text(message))
                    if message is not None
                    else "未知错误"
                )
            else:
                error_text = ladder_text
    else:
        error_text = sanitize_error_text(normalize_message_text(raw_error))
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
        message = item.get("message", "请求失败")
        error_message = sanitize_error_text(
            message if isinstance(message, str) else normalize_message_text(message),
            limit=message_limit,
        )
        if request_index is None:
            parts.append(f"  {error_message}")
        else:
            parts.append(f"  请求 {request_index}: {error_message}")
    return "\n".join(parts)


def _render_sanitized_value(value: Any) -> str:
    """渲染文本行内容：字符串原样，其余形态经 normalize_message_text 归一化，
    repr 不出现在用户可见行中。"""
    return value if isinstance(value, str) else normalize_message_text(value)


def _format_image_item(index: int, image: dict[str, Any]) -> list[str]:
    """格式化单张图片的可读详情行。

    入参已由调用方净化，直接消费；URL 存在时始终输出，local_path 存在时附加；
    markdown_ref 可由本地路径推导，文本通道不单独成行。
    """
    parts = [f"图片 {index}:"]
    if "request_index" in image:
        parts.append(f"  请求序号: {_render_sanitized_value(image['request_index'])}")
    error_info = image.get("error")
    if isinstance(error_info, dict):
        parts.append("  状态: 失败")
        if error_info.get("code"):
            parts.append(f"  错误码: {_render_sanitized_value(error_info['code'])}")
        if error_info.get("message"):
            parts.append(f"  错误信息: {_render_sanitized_value(error_info['message'])}")
    if image.get("url"):
        parts.append(f"  URL: {_render_sanitized_value(image['url'])}")
    if "size" in image:
        parts.append(f"  尺寸: {_render_sanitized_value(image['size'])}")
    if "output_format" in image:
        parts.append(f"  输出格式: {_render_sanitized_value(image['output_format'])}")
    if "image_index" in image:
        parts.append(f"  序号: {_render_sanitized_value(image['image_index'])}")
    if "local_path" in image:
        parts.append(f"  本地路径: {_render_sanitized_value(image['local_path'])}")
    if "b64_json" in image:
        b64_data = image.get("b64_json")
        # 不可计长度形态会使 len 抛 TypeError，仅对可计长度形态输出字符数。
        if b64_data and isinstance(b64_data, (str, bytes, list, dict, tuple)):
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
    """格式化自动保存摘要与失败明细。

    成功项折叠为一行 N/M 摘要，仅失败项保留明细行；图片编号取可保存图片在归一化
    列表中的原始索引，与图片列表段落同基准。saveable_indices 缺失或长度不足时
    回退保存序号。
    """
    if auto_save_error:
        return [f"自动保存失败: {auto_save_error}", ""]
    if not auto_save_results:
        return ["自动保存: 已开启但未生成可保存的图片", ""]

    successful_saves = sum(1 for r in auto_save_results if r.success)
    parts = [f"自动保存: {successful_saves}/{len(auto_save_results)} 成功"]
    for i, save_result in enumerate(auto_save_results):
        if save_result.success:
            continue
        if saveable_indices is not None and i < len(saveable_indices):
            display_index = saveable_indices[i] + 1
        else:
            display_index = i + 1
        # 保存失败原因经错误消息净化口径处理，防止换行注入。
        error_text = sanitize_error_text(save_result.error or "未知原因")
        parts.append(f"  图片 {display_index}: 保存失败 - {error_text}")
    parts.append("")
    return parts


def _format_usage_section(usage: dict[str, Any]) -> list[str]:
    """格式化使用统计，无可渲染条目时整段省略。

    仅渲染数值取值，字符串值不插值以免带入换行与敏感片段；非 dict 形态自守归空，
    畸形形态不使成功生成在格式化阶段翻错。
    """
    if not isinstance(usage, dict):
        return []
    items: list[str] = []

    def _render_number(label: str, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        items.append(f"  {label}: {value}")

    if "input_images" in usage:
        _render_number("输入图片数", usage["input_images"])
    if "output_tokens" in usage:
        _render_number("输出 tokens", usage["output_tokens"])
    if "total_tokens" in usage:
        _render_number("总 tokens", usage["total_tokens"])
    tool_usage = usage.get("tool_usage")
    if isinstance(tool_usage, dict):
        web_search_count = tool_usage.get("web_search")
        if (
            isinstance(web_search_count, int)
            and not isinstance(web_search_count, bool)
            and web_search_count > 0
        ):
            items.append(f"  联网搜索: {web_search_count} 次")
    if not items:
        return []
    return ["使用统计:", *items, ""]


def _extract_truncated_events(result: dict[str, Any]) -> int | None:
    """提取 SSE 解析记录的超限丢弃事件数，仅接受正整数，其余形态视为无该信息。"""
    value = result.get("truncated_events")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _extract_deadline_exceeded(result: dict[str, Any]) -> bool | None:
    """提取 SSE 流超时提前终止标记，仅接受真值布尔，其余形态视为无该信息。"""
    return True if result.get("deadline_exceeded") is True else None


def format_generation_response(
    title: str,
    result: dict[str, Any],
    size: str,
    auto_save_results: list[AutoSaveResult] | None = None,
    auto_save_enabled: bool = False,
    auto_save_error: str | None = None,
    images: list[dict[str, Any]] | None = None,
    saveable_indices: list[int] | None = None,
) -> str:
    """格式化图片生成结果为可读文本。

    提示词不在文本通道回显，完整值由 structuredContent.prompt 携带。

    Args:
        auto_save_error: 自动保存错误信息，存在时表示已降级跳过。
        images: 预提取且已净化的图片列表，None 时内部提取并净化；净化幂等，
            重复传入已净化列表不改变结果。
        saveable_indices: 可保存图片在归一化列表中的原始索引，与 auto_save_results
            按位置对位；None 时自动保存段落回退保存序号。

    Returns:
        格式化后的响应文本。
    """
    if _is_generation_failed(result):
        return _format_failure_section(result)

    if images is None:
        images = _sanitize_image_errors(extract_images(result))
    usage = result.get("usage", {})

    parts: list[str] = [title, f"尺寸: {size}", ""]

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

    truncated_events = _extract_truncated_events(result)
    if truncated_events:
        # usage 与自动保存段落自带收尾空行，追加提示行前去除一个，避免连续空行。
        if parts and parts[-1] == "":
            parts.pop()
        parts.append(f"因单事件体积超限丢弃 {truncated_events} 个事件")

    if _extract_deadline_exceeded(result):
        if parts and parts[-1] == "":
            parts.pop()
        parts.append("响应流超过总时长预算，已保留提前终止前收到的结果")

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

    经 GenerationStructuredOutput 构造后 model_dump，使输出与声明的 outputSchema
    绑定；成功与失败同构。

    Args:
        images: 预提取且已净化的图片列表，None 时内部提取并净化；净化幂等，
            重复传入已净化列表不改变结果。

    Returns:
        structuredContent 字典，成功路径排除 error 键。
    """
    # b64_json 为用户显式请求的图像载荷，有意不做截断。流水线传入的 images 已
    # 净化且净化幂等，直接消费；独立调用未传时在此完成首次净化。
    sanitized_images = (
        images if images is not None else _sanitize_image_errors(extract_images(result))
    )
    # status 为上游自由文本，同口径净化；非 str 归 None，畸形形态不使构造抛校验异常。
    raw_status = result.get("status")
    # usage 与 batch 的非 dict 形态按声明 schema 收敛为空 dict 与 None。
    raw_usage = result.get("usage", {})
    raw_batch = result.get("batch")
    payload: dict[str, Any] = {
        "tool": tool_name,
        "success": not _is_generation_failed(result),
        "status": sanitize_error_text(raw_status) if isinstance(raw_status, str) else None,
        "prompt": context.prompt,
        "size": context.size,
        "response_format": context.response_format,
        "output_format": context.output_format,
        "stream": context.stream,
        "tools": context.tools,
        "layer_decomposition": context.layer_decomposition,
        "background": context.background,
        "max_images": context.max_images,
        "request_count": context.request_count,
        "parallelism": context.parallelism,
        "data": sanitized_images,
        # usage 字符串值过净化管线防 CRLF 与凭据注入，数值保持原值供计费核对。
        "usage": _sanitize_usage(raw_usage) if isinstance(raw_usage, dict) else {},
        "batch": raw_batch if isinstance(raw_batch, dict) else None,
    }

    truncated_events = _extract_truncated_events(result)
    if truncated_events is not None:
        payload["truncated_events"] = truncated_events

    deadline_exceeded = _extract_deadline_exceeded(result)
    if deadline_exceeded is not None:
        payload["deadline_exceeded"] = deadline_exceeded

    if context.enable_auto_save:
        payload["auto_save"] = {
            "enabled": True,
            # 已由 format_error_for_user 在源头净化，净化幂等，无需二次处理。
            "error": auto_save_error,
            "results": [r.to_dict() for r in auto_save_results] if auto_save_results else [],
        }
    else:
        payload["auto_save"] = {"enabled": False}

    failed = _is_generation_failed(result)
    if failed:
        # 空值回落未知错误，与文本通道口径一致。
        raw_error = result.get("error") or "未知错误"
        if isinstance(raw_error, dict):
            # 聚合来源的 message 为本侧组装产物（含恢复指引），走防御性宽上限。
            sanitized_error = sanitize_error_dict(
                raw_error, message_limit=_aggregated_message_limit(result)
            )
            # 上游透传错误不含 type 键时兜底补齐，与 build_error_dict 的错误结构对齐。
            sanitized_error.setdefault("type", "generation_failed")
            payload["error"] = sanitized_error
        else:
            payload["error"] = build_error_dict(
                "generation_failed",
                sanitize_error_text(normalize_message_text(raw_error)),
            )

    output = GenerationStructuredOutput(**payload)
    if failed:
        return output.model_dump()
    return output.model_dump(exclude={"error"})
