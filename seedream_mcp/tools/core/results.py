"""生成结果处理：图片提取、并行结果聚合、自动保存合并、响应格式化与结构化输出。

各函数为纯数据处理，不触发 I/O：``extract_images`` 将多种响应形态归一化为 list[Dict]；
``aggregate_parallel_generation_results`` 合并多次请求的 data 与 usage 并按成败推导状态；
格式化函数将结果装配为面向模型的文本与 structuredContent 字段。
"""

from __future__ import annotations

from typing import Any

from ...utils.core.errors import sanitize_data_text, sanitize_error_text
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


# 单槽哨兵：最近一次经 _sanitize_image_errors 净化写回的图片列表。文本与结构化
# 两条出口在 common.py 中以同一列表先后调用本函数，两次调用之间无 await，事件
# 循环内不会交错，第二次进入据此识别并跳过重复净化。Python 内建 list 不支持实例
# 属性也不可弱引用，无法在列表对象上打标记，故以模块级单槽承载；命中后即清空，
# 未复用时持有至下一次生成调用覆盖，复位协议可经 reset_last_sanitized_images
# 显式清空；槽位被其他调用覆盖时退化为重复净化，方向安全。
_last_sanitized_images: list[dict[str, Any]] | None = None


def reset_last_sanitized_images() -> None:
    """清空净化哨兵槽位，供复位协议在资源生命周期边界显式调用。

    哨兵默认持有至下一次生成调用覆盖；复位协议调用本函数立即释放槽位持有的
    图片列表引用，避免其生命周期越过资源边界。清空后同一列表再次进入净化
    仅退化为重复净化，对已净化内容幂等，方向安全。
    """
    global _last_sanitized_images
    _last_sanitized_images = None


def _sanitize_usage(usage: Any) -> Any:
    """净化 usage 统计：数值标量原样保留，字符串值过净化管线。

    usage 为 client 侧原样透传的上游 dict，被劫持中间层回显自由文本时可能携带
    CRLF 或凭据片段；数值键为聚合语义保持原值，字符串值与嵌套容器逐层净化。
    遍历采用显式栈迭代而非递归：数百层嵌套（json.loads 同样不限深度）不会触发
    解释器递归上限，使成功结果退化为异常；visited 集合防御循环引用，上游透传
    数据理论无环，命中时以 <truncated:cyclic> 摘要占位终止展开。
    """
    if isinstance(usage, str):
        return sanitize_error_text(usage)
    if not isinstance(usage, (dict, list)):
        return usage

    sanitized_root: dict[str, Any] | list[Any] = {} if isinstance(usage, dict) else []
    visited: set[int] = set()
    # 待写入任务栈：目标容器 + 写入位置（dict 键或 list 下标）+ 待净化值；按下标
    # 寻址写入，出栈顺序不影响容器内元素顺序。
    pending: list[tuple[dict[str, Any] | list[Any], Any, Any]] = (
        [(sanitized_root, key, value) for key, value in usage.items()]
        if isinstance(usage, dict)
        else [(sanitized_root, index, item) for index, item in enumerate(usage)]
    )
    while pending:
        target, key, value = pending.pop()
        sanitized: Any
        if isinstance(value, (dict, list)):
            if id(value) in visited:
                sanitized = "<truncated:cyclic>"
            else:
                visited.add(id(value))
                sanitized = {} if isinstance(value, dict) else []
                if isinstance(value, dict):
                    pending.extend((sanitized, k, item) for k, item in value.items())
                else:
                    pending.extend((sanitized, i, item) for i, item in enumerate(value))
        elif isinstance(value, str):
            sanitized = sanitize_error_text(value)
        else:
            sanitized = value
        target[key] = sanitized
    return sanitized_root


# 图片条目的已知键：error 与 size/output_format/model/type、url 各自单独净化，
# local_path/markdown_ref 归入数据字段净化，b64_json 为有意保留的图像载荷原样透传，
# request_index/image_index 为本侧聚合写入的整数序号。不在此列的键按未知键处理。
_KNOWN_IMAGE_KEYS = frozenset(
    {
        "type",
        "size",
        "output_format",
        "model",
        "url",
        "error",
        "local_path",
        "markdown_ref",
        "b64_json",
        "request_index",
        "image_index",
    }
)


def _sanitize_image_errors(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """净化图片项内的上游自由字段，就地写回传入列表并返回同一列表。

    覆盖 error.message、error.code、url、size、output_format、model、type、
    local_path、markdown_ref 与未知键：均为上游可回显自由内容的字段，可能携带
    userinfo 凭据或 CRLF。url/local_path/markdown_ref 与未知键为数据字段，走
    sanitize_data_text 保留完整可用性——签名 URL 常见 400-700 字符、本地路径截断
    即不可寻址；其余短标识与自由文本走 sanitize_error_text，截断语义正确。
    local_path/markdown_ref 合法写入点仅为自动保存回填，但上游伪造的原始条目
    同样携带这两个键，统一净化闭合两条通道的注入面；未知键由结构化输出
    extra='allow' 直通，字符串值保守净化后保留而非剔除。净化结果写回列表条目后，
    文本与结构化两条输出通道复用同一份净化值；已净化列表再次进入时经模块级
    哨兵跳过，超长片段的截断标记不再叠加。仅对净化后内容发生变化的项做浅拷贝
    替换列表位置，其余项原样引用，调用方持有的原图片字典对象不被修改。
    SSE 失败事件在 io_sse 源头已净化，此处覆盖非 SSE 路径；对已脱敏内容幂等。
    """
    global _last_sanitized_images
    if _last_sanitized_images is images:
        _last_sanitized_images = None
        return images

    for index, image in enumerate(images):
        updates: dict[str, Any] = {}

        error = image.get("error")
        if isinstance(error, dict):
            sanitized_error: dict[str, Any] | None = None
            message = error.get("message")
            if isinstance(message, str):
                sanitized_message = sanitize_error_text(message)
                if sanitized_message != message:
                    sanitized_error = {**error, "message": sanitized_message}
            code = error.get("code")
            if isinstance(code, str):
                sanitized_code = sanitize_error_text(code)
                if sanitized_code != code:
                    if sanitized_error is None:
                        sanitized_error = dict(error)
                    sanitized_error["code"] = sanitized_code
            if sanitized_error is not None:
                updates["error"] = sanitized_error

        for field in ("size", "output_format", "model", "type"):
            value = image.get(field)
            if isinstance(value, str):
                sanitized_value = sanitize_error_text(value)
                if sanitized_value != value:
                    updates[field] = sanitized_value

        for field in ("url", "local_path", "markdown_ref"):
            value = image.get(field)
            if isinstance(value, str):
                sanitized_value = sanitize_data_text(value)
                if sanitized_value != value:
                    updates[field] = sanitized_value

        for key, value in image.items():
            if key in _KNOWN_IMAGE_KEYS or not isinstance(value, str):
                continue
            sanitized_value = sanitize_data_text(value)
            if sanitized_value != value:
                updates[key] = sanitized_value

        if updates:
            sanitized_item = dict(image)
            sanitized_item.update(updates)
            images[index] = sanitized_item

    _last_sanitized_images = images
    return images


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
    """格式化单张图片的可读详情行。

    入参条目已由 format_generation_response 经 _sanitize_image_errors 统一净化并
    写回，此处直接消费已净化值，不再对同一字段重复过净化管线。保存成功的条目
    已有本地路径，取回结果以路径为准，URL 行省略；未保存或保存失败时 URL 是取回
    结果的唯一途径，保留输出。markdown_ref 可由本地路径平凡推导，文本通道不再
    单独成行，结构化通道仍完整携带。
    """
    parts = [f"图片 {index}:"]
    if "request_index" in image:
        parts.append(f"  请求序号: {image['request_index']}")
    error_info = image.get("error")
    if isinstance(error_info, dict):
        parts.append("  状态: 失败")
        if error_info.get("code"):
            parts.append(f"  错误码: {error_info['code']}")
        if error_info.get("message"):
            parts.append(f"  错误信息: {error_info['message']}")
    if image.get("url") and "local_path" not in image:
        parts.append(f"  URL: {image['url']}")
    if "size" in image:
        parts.append(f"  尺寸: {image['size']}")
    if "output_format" in image:
        parts.append(f"  输出格式: {image['output_format']}")
    if "image_index" in image:
        parts.append(f"  序号: {image['image_index']}")
    if "local_path" in image:
        parts.append(f"  本地路径: {image['local_path']}")
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
    """格式化自动保存摘要与失败明细。

    成功项的本地路径已在图片列表段落以「本地路径」行展示，此处折叠为一行
    N/M 成功摘要；仅失败项保留明细行。图片编号基准与图片列表段落一致，取可保存
    图片在 extract_images 归一化列表中的原始索引：失败占位项占号不重排，混合成败
    时两条「图片 N」指向同一条目。saveable_indices 与 auto_save_results 按位置
    对位；缺失或长度不足时回退保存序号，避免编号悬空。
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
        # error 为异常文本，与结构化通道 to_dict 的净化对齐，防止换行注入用户可见文本。
        error_text = sanitize_error_text(save_result.error or "未知原因")
        parts.append(f"  图片 {display_index}: 保存失败 - {error_text}")
    parts.append("")
    return parts


def _format_usage_section(usage: dict[str, Any]) -> list[str]:
    """格式化使用统计。

    生成图片数已由自动保存摘要或图片列表段落表达——摘要分母即待保存图片总数，
    此处不再重复计数；无可渲染条目时整段省略。标量字段仅渲染数值取值：usage 为
    上游透传 dict，字符串值经插值会把换行与敏感片段带入文本通道。
    """
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
    """提取 SSE 解析记录的超限丢弃事件数，仅接受正整数。

    client 侧在发生超限丢弃时向 result 写入 truncated_events 键，未发生时缺省
    无键；bool 与非正整数等异常形态一律视为无该信息。
    """
    value = result.get("truncated_events")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


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

    将生成结果、尺寸、保存信息及使用统计等数据，按规范化格式输出为结构清晰的
    多行文本字符串。提示词不在文本通道回显：structuredContent.prompt 已携带完整
    值，且调用方模型刚发送过该提示词。

    Args:
        title: 响应标题，用于标识生成任务类型。
        result: 图片生成结果字典，包含图片数据及使用统计。
        prompt: 生成图片所用的提示词；为保持既有调用签名保留，文本通道不再回显，
            完整值由结构化通道的 prompt 字段携带。
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
    # 统一净化一次并写回列表：_format_image_item 直接消费已净化值，后续结构化
    # 构建复用同一列表并经哨兵跳过重复净化；io_sse 源头净化保持独立防线。
    images = _sanitize_image_errors(images)
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
        # data 项可能携带上游 per-image error 与 url/model/type 等自由字段，统一净化
        # 后进入结构化输出，与异常路径防护一致。format_generation_response 已就同一
        # 列表净化写回，此处经模块级哨兵跳过重复净化；独立调用场景自行净化兜底。
        "data": _sanitize_image_errors(images if images is not None else extract_images(result)),
        # usage 为 client 侧原样透传的上游 dict，字符串值过净化管线防 CRLF 与凭据注入，
        # 数值键保持原值供聚合与计费核对。
        "usage": _sanitize_usage(result.get("usage", {})),
        "batch": result.get("batch"),
    }

    truncated_events = _extract_truncated_events(result)
    if truncated_events is not None:
        payload["truncated_events"] = truncated_events

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
