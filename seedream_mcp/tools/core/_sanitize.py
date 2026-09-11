"""生成结果的输出净化：图片项字段净化、usage 净化与并行失败占位标记。"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ...utils.core.sanitizers import (
    CONTROL_CHARS_PATTERN,
    DATA_OUTPUT_LIMIT,
    MESSAGE_OUTPUT_LIMIT,
    normalize_message_text,
    sanitize_data_text,
    sanitize_error_text,
)


def _sanitize_leaf(item: Any, sanitize_string: Callable[[Any], Any]) -> Any:
    """叶子值净化：字符串经通道处理，非有限浮点归零，其余透传。"""
    if isinstance(item, str):
        return sanitize_string(item)
    if isinstance(item, float):
        return item if math.isfinite(item) else 0.0
    return item


def _unique_flat_key(taken: dict[Any, Any], key: Any) -> Any:
    """压平键的控制字符，与已占用键碰撞时附加 <dup> 后缀直到空闲，条目不覆盖不丢弃。

    非字符串键无控制字符语义，原样参与判重。
    """
    if isinstance(key, str):
        key = CONTROL_CHARS_PATTERN.sub(" ", key)
    while key in taken:
        key = f"{key}<dup>"
    return key


def _flatten_mapping_keys(mapping: dict[Any, Any]) -> dict[Any, Any]:
    """压平全部键名并保留首个净名，干净映射原样返回保持引用。"""
    # 前置探测避免干净映射的抛弃式 dict 构建；无控制字符的键不压平也不会碰撞。
    if not any(isinstance(key, str) and CONTROL_CHARS_PATTERN.search(key) for key in mapping):
        return mapping
    flattened: dict[Any, Any] = {}
    for key, value in mapping.items():
        flattened[_unique_flat_key(flattened, key)] = value
    return flattened


def _sanitize_value_tree(value: Any, sanitize_string: Callable[[Any], Any]) -> Any:
    """以显式栈迭代净化任意嵌套的 dict/list 树，字符串值经 sanitize_string 处理，
    dict 键压平控制字符，压平碰撞以 <dup> 后缀保留。

    迭代遍历使深嵌套不触发解释器递归上限；循环引用以 <truncated:cyclic> 占位终止
    展开。usage 净化与未知键净化共用本核心，仅字符串净化函数不同。
    """
    if not isinstance(value, (dict, list)):
        return _sanitize_leaf(value, sanitize_string)

    # list 根预置等长空槽，与嵌套 list 按下标写入口径一致。
    sanitized_root: dict[str, Any] | list[Any] = (
        {} if isinstance(value, dict) else [None] * len(value)
    )
    ancestors: set[int] = set()
    # 子树完成哨兵：与写入任务同为三元组，写入位置携带待移出的容器 id。
    subtree_done = object()
    # 待写入任务栈：目标容器 + 写入位置 + 待净化值。dict 键在容器展开时经
    # _flatten_mapping_keys 压平，写入点直接落键。
    pending: list[tuple[Any, Any, Any]] = (
        [
            (sanitized_root, key, item)
            for key, item in reversed(_flatten_mapping_keys(value).items())
        ]
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
                sanitized = {} if isinstance(item, dict) else [None] * len(item)
                # 哨兵先于子任务入栈，LIFO 使容器恰在其子树处理期间位于祖先集合。
                pending.append((subtree_done, id(item), None))
                if isinstance(item, dict):
                    pending.extend(
                        (sanitized, sub_key, sub)
                        for sub_key, sub in reversed(_flatten_mapping_keys(item).items())
                    )
                else:
                    pending.extend((sanitized, i, sub) for i, sub in enumerate(item))
        else:
            sanitized = _sanitize_leaf(item, sanitize_string)
        target[key] = sanitized
    return sanitized_root


def _sanitize_usage(usage: Any) -> Any:
    """净化 usage：非有限数值归零，字符串值与嵌套容器逐层净化防 CRLF 与凭据注入。"""
    return _sanitize_value_tree(usage, sanitize_error_text)


# 图片条目的已知键：b64_json 为有意保留的图像载荷原样透传，request_index/image_index
# 的 int 实例为本侧写入的序号，其余键各自单独净化；不在列的键按未知键处理。
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
    """净化未知键取值：字符串经 sanitize_data_text 保留 URL 与长文本可用性，容器
    逐层净化，标量原样返回。"""
    return _sanitize_value_tree(value, sanitize_data_text)


def sanitize_error_dict(
    error: dict[str, Any], *, message_limit: int = MESSAGE_OUTPUT_LIMIT
) -> dict[str, Any]:
    """净化错误 dict 的各分量，返回净化后的新 dict，供全部错误出口共用。

    键名在拷贝入口压平控制字符（碰撞以 <dup> 后缀保留），message 与 code 归一化
    后过错误文本通道，其余键值经容器净化，凭据与 CRLF 不借旁路键穿透；
    message_limit 覆盖 message 的截断上限，供本侧组装的长文案（含恢复指引的
    聚合消息）改用防御性宽上限，脱敏口径不变。
    """
    sanitized_error: dict[str, Any] = {}
    for key, value in error.items():
        flat_key = _unique_flat_key(sanitized_error, key)
        if key == "message" and value is not None:
            sanitized_error[flat_key] = sanitize_error_text(
                normalize_message_text(value), limit=message_limit
            )
        elif key == "code" and value is not None:
            sanitized_error[flat_key] = sanitize_error_text(normalize_message_text(value))
        else:
            sanitized_error[flat_key] = _sanitize_value_tree(value, sanitize_error_text)
    return sanitized_error


def _sanitize_image_error_entry(
    error: Any, *, message_limit: int = MESSAGE_OUTPUT_LIMIT
) -> dict[str, Any]:
    """净化图片项的 error 字段，返回需回写的更新项。

    dict 形态经 sanitize_error_dict 单点净化；非 dict 形态整体经容器净化，
    凭据与 CRLF 不借形态绕过。message_limit 的语义见 sanitize_error_dict。
    """
    updates: dict[str, Any] = {}
    if isinstance(error, dict):
        sanitized_error = sanitize_error_dict(error, message_limit=message_limit)
        if sanitized_error != error:
            updates["error"] = sanitized_error
    elif error is not None:
        sanitized_non_dict = _sanitize_value_tree(error, sanitize_error_text)
        if sanitized_non_dict != error:
            updates["error"] = sanitized_non_dict
    return updates


def _sanitize_fields_with(
    image: dict[str, Any], fields: tuple[str, ...], sanitize_string: Callable[[Any], Any]
) -> dict[str, Any]:
    """按指定净化函数净化一组字段，返回需回写的更新项。"""
    updates: dict[str, Any] = {}
    for field in fields:
        value = image.get(field)
        sanitized_value = _sanitize_value_tree(value, sanitize_string)
        if sanitized_value != value:
            updates[field] = sanitized_value
    return updates


def _sanitize_index_fields(image: dict[str, Any]) -> dict[str, Any]:
    """净化 request_index/image_index 字段，返回需回写的更新项。

    int 实例为本侧写入的序号直接保留，bool 与其他非 int 形态按错误文本净化。
    """
    updates: dict[str, Any] = {}
    for field in ("request_index", "image_index"):
        value = image.get(field)
        if not isinstance(value, bool) and isinstance(value, int):
            continue
        sanitized_value = _sanitize_value_tree(value, sanitize_error_text)
        if sanitized_value != value:
            updates[field] = sanitized_value
    return updates


def _sanitize_unknown_fields(image: dict[str, Any]) -> dict[str, Any]:
    """净化不在已知键清单内的字段，返回需回写的更新项，键已在入口压平。"""
    updates: dict[str, Any] = {}
    for key, value in image.items():
        if key in _KNOWN_IMAGE_KEYS:
            continue
        sanitized_value = _sanitize_unknown_value(value)
        if sanitized_value != value:
            updates[key] = sanitized_value
    return updates


# 并行批次失败占位项的 type 标识；其 error.message 已在聚合源头净化。
_REQUEST_FAILED_TYPE = "image_generation.request_failed"


def message_limit_for(trusted_message: bool) -> int:
    """消息净化上限单点：本侧组装产物走防御性宽限，上游自由文本走错误通道上限。

    结果聚合与图片项净化共用本判定，宽限策略调整只改此处。
    """
    return DATA_OUTPUT_LIMIT if trusted_message else MESSAGE_OUTPUT_LIMIT


def _sanitize_image_errors(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """净化图片项内上游可回显自由内容的字段，返回净化后的列表。

    error 与 size/output_format/model/type 等短标识走 sanitize_error_text 截断语义；
    url、local_path/markdown_ref 与未知键走 sanitize_data_text 保留完整可用性；非
    字符串形态经 _sanitize_value_tree 逐层净化，int 序号保持原值。图片项键名在
    入口压平控制字符（碰撞以 <dup> 后缀保留），合并时原始脏键不再以原值反杀
    净化值。占位项（type 为请求失败标识）的 error message 为本侧组装产物（含
    恢复指引），改用防御性宽上限；上游伪造该 type 的可利用面为宽截断，与数据
    通道的 16KB 上限同量级，脱敏口径不变。仅键名或净化后内容变化的项做浅拷贝，
    其余项保持原对象引用，传入列表不被修改。限内内容重复净化恒等；超长 URL
    的截断产物再净化会按错误文本通道口径收敛（产物已不可用，仅长度与形态变
    化）。全部图片项（含 SSE 失败事件）经此处统一净化，io_sse 源头不做净化。
    """
    sanitized_images = images
    for index, image in enumerate(images):
        flat_image = _flatten_mapping_keys(image)
        updates: dict[str, Any] = {}
        message_limit = message_limit_for(flat_image.get("type") == _REQUEST_FAILED_TYPE)
        updates.update(
            _sanitize_image_error_entry(flat_image.get("error"), message_limit=message_limit)
        )
        # b64_json 合法形态为 str 载荷原样保留，非字符串的畸形取值置 None。
        b64_value = flat_image.get("b64_json")
        if b64_value is not None and not isinstance(b64_value, str):
            updates["b64_json"] = None
        updates.update(
            _sanitize_fields_with(
                flat_image, ("size", "output_format", "model", "type"), sanitize_error_text
            )
        )
        updates.update(_sanitize_index_fields(flat_image))
        updates.update(
            _sanitize_fields_with(
                flat_image, ("url", "local_path", "markdown_ref"), sanitize_data_text
            )
        )
        updates.update(_sanitize_unknown_fields(flat_image))
        if updates or flat_image is not image:
            if sanitized_images is images:
                sanitized_images = list(images)
            sanitized_images[index] = {**flat_image, **updates}
    return sanitized_images
