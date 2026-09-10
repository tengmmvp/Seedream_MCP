"""生成结果的输出净化：图片项字段净化、usage 净化与并行失败占位标记。"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ...utils.core.sanitizers import (
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


def _sanitize_value_tree(value: Any, sanitize_string: Callable[[Any], Any]) -> Any:
    """以显式栈迭代净化任意嵌套的 dict/list 树，字符串值经 sanitize_string 处理。

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
    # 待写入任务栈：目标容器 + 写入位置 + 待净化值。
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
                sanitized = {} if isinstance(item, dict) else [None] * len(item)
                # 哨兵先于子任务入栈，LIFO 使容器恰在其子树处理期间位于祖先集合。
                pending.append((subtree_done, id(item), None))
                if isinstance(item, dict):
                    pending.extend((sanitized, k, sub) for k, sub in item.items())
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


def _sanitize_image_error_entry(error: Any) -> dict[str, Any]:
    """净化图片项的 error 字段，返回需回写的更新项。

    dict 形态净化 message 与 code 两个自由文本分量；非 dict 形态整体经容器净化，
    凭据与 CRLF 不借形态绕过。
    """
    updates: dict[str, Any] = {}
    if isinstance(error, dict):
        sanitized_error = dict(error)
        changed = False
        message = error.get("message")
        if message is not None:
            sanitized_message = sanitize_error_text(normalize_message_text(message))
            if sanitized_message != message:
                sanitized_error["message"] = sanitized_message
                changed = True
        code = error.get("code")
        if code is not None:
            sanitized_code = sanitize_error_text(normalize_message_text(code))
            if sanitized_code != code:
                sanitized_error["code"] = sanitized_code
                changed = True
        # message/code 以外的键同样过净化管线，凭据与 CRLF 不借旁路键穿透。
        for key, value in error.items():
            if key in ("message", "code"):
                continue
            sanitized_value = _sanitize_value_tree(value, sanitize_error_text)
            if sanitized_value != value:
                sanitized_error[key] = sanitized_value
                changed = True
        if changed:
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
    """净化不在已知键清单内的字段，返回需回写的更新项。"""
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

# 占位项的内部标记键：error 净化豁免仅在聚合结果内对携带该标记的条目生效，
# 上游透传的 data 项即使伪造 sentinel type 也无法获得豁免。聚合复制上游项时剔除
# 该键防注入，净化出口剔除该键使其不进入 structuredContent。
_PLACEHOLDER_MARKER = "__seedream_request_failed__"


def _sanitize_image_errors(
    images: list[dict[str, Any]], *, aggregated: bool = False
) -> list[dict[str, Any]]:
    """净化图片项内上游可回显自由内容的字段，返回净化后的列表。

    error 与 size/output_format/model/type 等短标识走 sanitize_error_text 截断语义；
    url、local_path/markdown_ref 与未知键走 sanitize_data_text 保留完整可用性；非
    字符串形态经 _sanitize_value_tree 逐层净化，int 序号保持原值。仅净化后内容变化
    的项做浅拷贝，其余项保持原对象引用，传入列表不被修改。净化非幂等，重复净化会使
    超长片段的截断标记叠加，全部图片项（含 SSE 失败事件）经此处统一净化一次，
    io_sse 源头不做净化。aggregated 为真时，携带本侧占位标记的
    并行失败占位项整体跳过净化并剔除标记键；伪造 sentinel type 的上游透传项不
    携带标记，照常净化。
    """
    sanitized_images = images
    for index, image in enumerate(images):
        updates: dict[str, Any] = {}
        # 占位项豁免以聚合来源加内部标记双因子判定：全部字段均为本侧生成的常量、
        # 序号与聚合源头已净化的消息，整体跳过净化，出口仅剔除标记键。
        if aggregated and image.get(_PLACEHOLDER_MARKER) is True:
            if sanitized_images is images:
                sanitized_images = list(images)
            sanitized_images[index] = {
                key: value for key, value in image.items() if key != _PLACEHOLDER_MARKER
            }
            continue
        updates.update(_sanitize_image_error_entry(image.get("error")))
        # b64_json 合法形态为 str 载荷原样保留，非字符串的畸形取值置 None。
        b64_value = image.get("b64_json")
        if b64_value is not None and not isinstance(b64_value, str):
            updates["b64_json"] = None
        updates.update(
            _sanitize_fields_with(
                image, ("size", "output_format", "model", "type"), sanitize_error_text
            )
        )
        updates.update(_sanitize_index_fields(image))
        updates.update(
            _sanitize_fields_with(image, ("url", "local_path", "markdown_ref"), sanitize_data_text)
        )
        updates.update(_sanitize_unknown_fields(image))
        if updates:
            if sanitized_images is images:
                sanitized_images = list(images)
            sanitized_images[index] = {**image, **updates}
    return sanitized_images
