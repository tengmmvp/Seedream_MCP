"""生成结果处理：图片提取、并行结果聚合、自动保存合并、响应格式化与结构化输出。

各函数为纯数据处理，不触发 I/O：``extract_images`` 将多种响应形态归一化为 list[Dict]；
``aggregate_parallel_generation_results`` 合并多次请求的 data 与 usage 并按成败推导状态；
格式化函数将结果装配为面向模型的文本与 structuredContent 字段。
"""

from __future__ import annotations

from typing import Any, Callable

from ...utils.core.errors import (
    normalize_message_text,
    sanitize_data_text,
    sanitize_error_text,
)
from ...utils.io.io_save import AutoSaveResult
from ._helpers import (
    _add_usage_value,
    _classify_generation_error_type,
    _extract_parallel_request_error,
    _is_generation_failed,
    _normalize_error_message,
)
from .context import GenerationExecutionContext
from .outputs import GenerationStructuredOutput, build_error_dict

# extract_images 嵌套 data 下钻的深度上限：远高于合法响应的嵌套层级，仅兜底受损
# 上游注入的环状或超深结构，超限归一为空而非挂死或翻错。
_MAX_NESTED_DATA_DEPTH = 10_000


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
        """将任意取值归一化为仅含图片字典的列表。

        嵌套 ``{"data": ...}`` 形态经 while 迭代下钻而非递归：json.loads 的 C 层
        栈开销低于 Python 帧，深嵌套响应可成功解析却在递归实现上抛 RecursionError，
        使已计费的成功生成翻错为错误结果；迭代下钻与 _sanitize_value_tree 的
        遍历口径一致。下钻设深度上限兜底：json.loads 产物不含循环引用，上限仅在
        受损上游经其他途径注入环状结构时触发，超限视为无效图片数据归一为空，
        不使迭代退化为挂死。
        """
        depth = 0
        while True:
            if value is None:
                return []
            if isinstance(value, list):
                # 过滤 null 及非字典元素，保证 list[Dict] 类型一致。
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
    请求决定，status 按 completed/partial/failed 三态推导，任一成功请求自身为 partial
    时批次 status 至多为 partial。全部失败时以首个失败异常为代表分类错误码；无异常的
    软失败结果从各结果字典提取上游 error.code 透传进 error 载荷，与单发路径透传上游
    错误码的契约一致。

    Args:
        request_results: 各请求结果列表，成功为结果字典，失败或异常时对应位置为 None。
        request_errors: 请求序号到异常的映射，序号从 1 起，与结果列表按位置对应。

    Returns:
        聚合后的统一响应字典，含 success、data、usage、status 与 batch 键，全部
        请求失败时额外写入 error 键。
    """
    merged_data: list[dict[str, Any]] = []
    merged_usage: dict[str, Any] = {}
    error_items: list[dict[str, Any]] = []
    success_requests = 0
    partial_requests = 0
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
    # 批次状态推导：全部成功且无任何请求自身为 partial 时才报 completed；任一成功
    # 请求内部存在部分失败时批次完整性同样受损，status 至多为 partial。
    if failed_requests == 0 and partial_requests == 0:
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
        # 以首个失败异常为代表，复用与单发路径一致的错误码分类，避免并发全失败
        # 被硬编码为 generation_failed 而与单发路径的错误码契约分叉。
        representative = next(
            (request_errors[i] for i in range(1, request_count + 1) if i in request_errors),
            None,
        )
        if representative is not None:
            error_type = _classify_generation_error_type(representative)
            aggregated_result["error"] = build_error_dict(error_type, message)
        else:
            # 无异常的软失败结果（200 加顶层 error 的请求级失败）逐个回退提取上游
            # error.code 参与结构化载荷：code 是上游自由文本，出口处经
            # _build_generation_structured_result 的 dict error 分支净化后透传，
            # 未提取到任何可用错误码时维持 generation_failed 兜底。
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


def _sanitize_value_tree(value: Any, sanitize_string: Callable[[Any], Any]) -> Any:
    """以显式栈迭代净化任意嵌套的 dict/list 树，字符串值经 sanitize_string 处理。

    遍历采用显式栈迭代而非递归：数百层嵌套（json.loads 同样不限深度）不会触发
    解释器递归上限，使成功结果退化为异常。祖先集合仅记录当前展开栈上的容器，
    子树处理完毕即移出：合法的共享引用不在同一条展开路径上重复出现，按全量
    id 判重会将其误吞为循环；真正的循环引用表现为祖先再现，命中时以
    <truncated:cyclic> 摘要占位终止展开。usage 净化与未知键净化共用本核心，
    仅字符串净化函数不同，口径由单一实现保证一致。
    """
    if isinstance(value, str):
        return sanitize_string(value)
    if not isinstance(value, (dict, list)):
        return value

    # list 根同样预置等长空槽，与嵌套 list 的按下标写入口径一致。
    sanitized_root: dict[str, Any] | list[Any] = (
        {} if isinstance(value, dict) else [None] * len(value)
    )
    ancestors: set[int] = set()
    # 子树完成哨兵：与写入任务同为三元组形态，目标位置为该哨兵对象，写入位置
    # 携带待移出的容器 id，值为 None。
    subtree_done = object()
    # 待写入任务栈：目标容器 + 写入位置（dict 键或 list 下标）+ 待净化值；按下标
    # 寻址写入，出栈顺序不影响容器内元素顺序。
    pending: list[tuple[Any, Any, Any]] = (
        [(sanitized_root, key, item) for key, item in value.items()]
        if isinstance(value, dict)
        else [(sanitized_root, index, item) for index, item in enumerate(value)]
    )
    while pending:
        target, key, item = pending.pop()
        if target is subtree_done:
            ancestors.discard(key)
            continue
        sanitized: Any
        if isinstance(item, (dict, list)):
            if id(item) in ancestors:
                sanitized = "<truncated:cyclic>"
            else:
                ancestors.add(id(item))
                # list 预置等长空槽，子任务按下标写入时不越界；dict 以键写入无需预置。
                sanitized = {} if isinstance(item, dict) else [None] * len(item)
                # 哨兵先于子任务入栈，LIFO 使子任务先于哨兵弹出，容器恰在其
                # 子树处理期间位于祖先集合中。
                pending.append((subtree_done, id(item), None))
                if isinstance(item, dict):
                    pending.extend((sanitized, k, sub) for k, sub in item.items())
                else:
                    pending.extend((sanitized, i, sub) for i, sub in enumerate(item))
        elif isinstance(item, str):
            sanitized = sanitize_string(item)
        else:
            sanitized = item
        target[key] = sanitized
    return sanitized_root


def _sanitize_usage(usage: Any) -> Any:
    """净化 usage 统计：数值标量原样保留，字符串值过净化管线。

    usage 为 client 侧原样透传的上游 dict，被劫持中间层回显自由文本时可能携带
    CRLF 或凭据片段；数值键为聚合语义保持原值，字符串值与嵌套容器逐层净化。
    遍历经 _sanitize_value_tree 迭代展开，深嵌套不触发解释器递归上限。
    """
    return _sanitize_value_tree(usage, sanitize_error_text)


# 图片条目的已知键：error 与 size/output_format/model/type、url 各自单独净化，
# local_path/markdown_ref 归入数据字段净化，b64_json 为有意保留的图像载荷原样透传，
# request_index/image_index 的 int 实例为本侧聚合写入的整数序号，非 int 形态按
# 错误文本单独净化。不在此列的键按未知键处理。
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


def _sanitize_unknown_value(value: Any) -> Any:
    """净化未知键的取值：字符串走数据字段净化，容器逐层重建，标量原样返回。

    字符串沿用 sanitize_data_text 的数据字段语义，保留 URL 与长文本的可用性。
    遍历经 _sanitize_value_tree 迭代展开，与 _sanitize_usage 同一实现口径：
    被污染上游构造的数百层嵌套容器不会触发解释器递归上限，已计费的成功生成
    不因净化阶段翻错为异常；循环引用以 <truncated:cyclic> 占位终止。
    """
    return _sanitize_value_tree(value, sanitize_data_text)


def _sanitize_image_errors(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """净化图片项内的上游自由字段，就地写回传入列表并返回同一列表。

    覆盖 error.message、error.code、error 的非 dict 形态、url、size、output_format、
    model、type、local_path、markdown_ref、request_index/image_index 的非 int 形态与
    未知键：均为上游可回显自由内容的字段，可能携带 userinfo 凭据或 CRLF。
    url/local_path/markdown_ref 与未知键为数据字段，走 sanitize_data_text 保留
    完整可用性——签名 URL 常见 400-700 字符、本地路径截断即不可寻址；其余短标识
    与自由文本走 sanitize_error_text，截断语义正确。七个已知字段的非 str 形态与
    序号字段的非 int 形态均经 _sanitize_value_tree 处理：字符串按各自语义净化，
    容器逐层净化后保留，标量原样返回，凭据与 CRLF 不借 dict/list 形态整体绕过。
    error.message 与 error.code 的非字符串形态先经 normalize_message_text 归一化
    为文本再净化，与顶层 error.message 的双出口口径一致，dict/list 形态的凭据
    不借原值穿透。request_index/image_index 的合法写入点为本侧聚合写入的整数
    序号，int 实例保持原值；bool 虽为 int 子类但不是序号，同样进入净化路径。
    local_path/markdown_ref 合法写入点仅为自动保存回填，但上游伪造的原始条目
    同样携带这两个键，统一净化闭合两条通道的注入面；未知键由结构化输出
    extra='allow' 直通，字符串值保守净化后保留而非剔除，容器值递归净化后重建，
    嵌套深处的凭据片段与 CRLF 同样不进入 structuredContent。净化结果写回列表条目后，
    文本与结构化两条输出通道复用同一份净化值。同一列表的净化协调由调用方以
    显式参数承担：common.py 顺序调用两条出口，文本出口成功分支首次净化写回，
    结构化出口经 images_sanitized 标记跳过重复净化，重复净化会使超长片段的
    截断标记逐次叠加，本函数自身不做重复进入判定。仅对净化后内容发生变化的项
    做浅拷贝替换列表位置，其余项原样引用，调用方持有的原图片字典对象不被修改。
    SSE 失败事件在 io_sse 源头已净化，此处覆盖非 SSE 路径。
    """
    for index, image in enumerate(images):
        updates: dict[str, Any] = {}

        error = image.get("error")
        if isinstance(error, dict):
            sanitized_error: dict[str, Any] | None = None
            message = error.get("message")
            if message is not None:
                # message 为上游自由内容且可为任意 JSON 形态：非字符串先归一化为
                # 文本再净化，与顶层 error.message 的双出口口径一致。
                sanitized_message = sanitize_error_text(normalize_message_text(message))
                if sanitized_message != message:
                    sanitized_error = {**error, "message": sanitized_message}
            code = error.get("code")
            if code is not None:
                # code 同为上游自由内容，非字符串形态同口径归一化后净化。
                sanitized_code = sanitize_error_text(normalize_message_text(code))
                if sanitized_code != code:
                    if sanitized_error is None:
                        sanitized_error = dict(error)
                    sanitized_error["code"] = sanitized_code
            if sanitized_error is not None:
                updates["error"] = sanitized_error
        elif error is not None:
            # 非 dict 形态与 dict 内的 message/code 同为上游自由内容：字符串走错误
            # 文本净化截断，容器经净化树逐层收敛，凭据与 CRLF 不借非 dict 形态绕过
            # 净化直通 structuredContent。
            sanitized_non_dict = _sanitize_value_tree(error, sanitize_error_text)
            if sanitized_non_dict != error:
                updates["error"] = sanitized_non_dict

        for field in ("size", "output_format", "model", "type"):
            value = image.get(field)
            # 非 str 形态经 _sanitize_value_tree 处理：字符串按错误文本语义净化，
            # 容器逐层净化后保留，标量原样返回，凭据与 CRLF 不借 dict/list 形态
            # 整体绕过净化。
            sanitized_value = _sanitize_value_tree(value, sanitize_error_text)
            if sanitized_value != value:
                updates[field] = sanitized_value

        for field in ("request_index", "image_index"):
            value = image.get(field)
            # int 实例为本侧聚合写入的整数序号，直接保留；bool 虽为 int 子类但不是
            # 序号，与单请求路径透传的其他非 int 形态一并按错误文本净化。
            if not isinstance(value, bool) and isinstance(value, int):
                continue
            sanitized_value = _sanitize_value_tree(value, sanitize_error_text)
            if sanitized_value != value:
                updates[field] = sanitized_value

        for field in ("url", "local_path", "markdown_ref"):
            value = image.get(field)
            # 数据字段与上一循环同构，仅净化语义换为 sanitize_data_text 保留
            # URL 与长文本的可用性。
            sanitized_value = _sanitize_value_tree(value, sanitize_data_text)
            if sanitized_value != value:
                updates[field] = sanitized_value

        for key, value in image.items():
            if key in _KNOWN_IMAGE_KEYS:
                continue
            sanitized_value = _sanitize_unknown_value(value)
            if sanitized_value != value:
                updates[key] = sanitized_value

        if updates:
            sanitized_item = dict(image)
            sanitized_item.update(updates)
            images[index] = sanitized_item

    return images


def _format_failure_section(result: dict[str, Any]) -> str:
    """失败时格式化并行失败详情；无 batch 错误信息时仅返回失败概述。

    error 文本为上游自由内容，出口处过 sanitize_error_text 与异常路径防护一致；
    message 的非字符串形态先经 normalize_message_text 归一化为文本再净化，凭据
    不借 dict/list 形态在净化之后经插值穿透文本通道。error 形态为 dict 时优先经
    _normalize_error_message 的五级阶梯提取 message/msg/detail/error/code，阶梯
    未命中且 message 为非 None 形态时归一化该分量；缺键、None 等空值回落未知
    错误，字面量 None 与字典 repr 不进入用户可见文本。
    """
    # error 键缺失或值为 None 等空值时归入未知错误；结构化出口对空值施以同一
    # or 回落，两条通道口径一致。
    raw_error = result.get("error") or "未知错误"
    if isinstance(raw_error, dict):
        # dict 形态优先复用五级提取阶梯，与并行聚合的错误提取同口径；阶梯只命中
        # 字符串分量，非 str 的 message 仍归一化为文本再净化，凭据不借形态穿透；
        # message 缺键或为 None 时回落未知错误，dict repr 与字面 None 不进入文本。
        error_text = _normalize_error_message(raw_error)
        if error_text is None:
            message = raw_error.get("message")
            error_text = (
                sanitize_error_text(normalize_message_text(message))
                if message is not None
                else "未知错误"
            )
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
        error_message = sanitize_error_text(normalize_message_text(item.get("message", "请求失败")))
        if request_index is None:
            parts.append(f"  {error_message}")
        else:
            parts.append(f"  请求 {request_index}: {error_message}")
    return "\n".join(parts)


def _render_sanitized_value(value: Any) -> str:
    """将已净化的取值渲染为文本行内容：字符串原样，其余形态归一化为文本。

    容器等非字符串形态经 f-string 插值会以 Python repr 输出，净化后的嵌套字符串
    仍会被引号与转义包裹成可疑文本；此处统一经 normalize_message_text 归一化为
    文本，repr 形态不出现在用户可见行中。
    """
    return value if isinstance(value, str) else normalize_message_text(value)


def _format_image_item(index: int, image: dict[str, Any]) -> list[str]:
    """格式化单张图片的可读详情行。

    入参条目已由 format_generation_response 经 _sanitize_image_errors 统一净化并
    写回，此处直接消费已净化值，不再对同一字段重复过净化管线；非字符串取值经
    _render_sanitized_value 渲染归一化文本，不以 repr 插值。URL 是模型向用户
    展示图片的直接载体，始终输出；local_path 为自动保存回填的持久化信息，存在时
    附加输出。markdown_ref 可由本地路径平凡推导，文本通道不单独成行，结构化通道
    仍完整携带。
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
        # 伪造的 int 等不可计长度形态会使 len 抛 TypeError，已计费结果被外层翻错
        # 为失败；仅对可计长度的形态输出字符数，其余归入无数据分支。
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
    上游透传 dict，字符串值经插值会把换行与敏感片段带入文本通道。入口对非 dict
    形态自守归空：client 与 io_sse 两层已归一 usage 形态，此处为纵深防线，畸形
    形态不使已计费的成功生成在文本格式化阶段翻错。
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
    prompt: str | None,
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
        prompt: 生成图片所用的提示词，图层拆分场景可为 None；为保持既有调用签名
            保留，文本通道不再回显，完整值由结构化通道的 prompt 字段携带。
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
    # 构建经 images_sanitized 显式标记复用同一列表、跳过重复净化；io_sse 源头
    # 净化保持独立防线。
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
    images_sanitized: bool = False,
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
        images_sanitized: images 是否已经 format_generation_response 净化写回；
            成功路径的文本出口先净化同一列表，置 True 跳过重复净化，使超长片段的
            截断标记不叠加。默认 False，独立调用与失败批次路径在此完成首次净化。

    Returns:
        构造并经 model_dump 输出的 structuredContent 字典，成功路径排除 error 键。
    """
    # b64_json 模式下 data 内的完整 base64 为有意保留：用户显式请求 b64 即期望取回图像
    # 数据，故此处不做截断；并行与组图场景的大载荷由调用方或客户端按需处理。
    if images is not None and images_sanitized:
        sanitized_images = images
    else:
        sanitized_images = _sanitize_image_errors(
            images if images is not None else extract_images(result)
        )
    # status 的非 SSE 路径取自响应体原文，属上游自由文本；与 data/usage 同口径净化，
    # 控制字符与凭据片段不借 status 键进入 structuredContent。非 str 形态归 None：
    # 声明 schema 的 status 为 str | None，畸形形态直传会使 model 构造抛校验异常，
    # 已计费的成功生成被翻错为失败结果。
    raw_status = result.get("status")
    # usage 与 batch 的非 dict 形态同样按声明 schema 收敛：usage 归空 dict、batch 归
    # None，client 与 io_sse 两层已归一形态，此处为纵深防线。
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
        # data 项可能携带上游 per-image error 与 url/model/type 等自由字段，统一净化
        # 后进入结构化输出，与异常路径防护一致。成功路径的文本出口已就同一列表净化
        # 写回，上方经 images_sanitized 跳过重复净化；独立调用场景自行净化兜底。
        "data": sanitized_images,
        # usage 为 client 侧原样透传的上游 dict，字符串值过净化管线防 CRLF 与凭据注入，
        # 数值键保持原值供聚合与计费核对。
        "usage": _sanitize_usage(raw_usage) if isinstance(raw_usage, dict) else {},
        "batch": raw_batch if isinstance(raw_batch, dict) else None,
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
        # error 键缺失或值为 None 等空值时回落未知错误，与文本通道的 or 口径一致，
        # 字面量 None 不进入 structuredContent.error。
        raw_error = result.get("error") or "未知错误"
        if isinstance(raw_error, dict):
            sanitized_error = dict(raw_error)
            # message 为上游自由内容且可为任意 JSON 形态：非字符串先归一化为文本再
            # 净化，dict/list 的凭据不借原值直通 structuredContent.error.message。
            if "message" in sanitized_error:
                sanitized_error["message"] = sanitize_error_text(
                    normalize_message_text(sanitized_error["message"])
                )
            # code 同为上游自由文本，200 加顶层 error 的请求级失败经 client 透传
            # 到此，CRLF 与凭据片段不借错误码进入 structuredContent。
            if isinstance(sanitized_error.get("code"), str):
                sanitized_error["code"] = sanitize_error_text(sanitized_error["code"])
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
