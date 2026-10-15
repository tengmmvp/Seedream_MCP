"""模型信息工具的核心载荷组装。

从配置的 model_id 派生当前模型的能力快照：家族、别名与能力字段，结构化载荷、
文本摘要与工具描述同源派生。
"""

from __future__ import annotations

from typing import Any

from ...config import SeedreamConfig
from ...utils.model.model_capabilities import (
    CAPABILITY_LABELS,
    MODEL_ALIASES,
    MODEL_FAMILY_UNKNOWN,
    get_model_capabilities,
    preset_numeric_sort_key,
)

# 取值型能力字段的中文标签序，每条标签映射其覆盖的 payload 字段元组，
# 工具描述的取值段列举由此派生。
_NUMERIC_FIELD_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("提示词优化档位", ("optimize_prompt_modes",)),
    ("参考图上限", ("max_reference_images",)),
    ("尺寸档位与像素区间", ("size_presets", "min_size_pixels", "max_size_pixels")),
)


def build_model_info_payload(config: SeedreamConfig) -> dict[str, Any]:
    """组装当前配置模型的能力快照。"""
    caps = get_model_capabilities(config.model_id)
    known = caps.family != MODEL_FAMILY_UNKNOWN
    return {
        "model_id": config.model_id,
        "display_name": caps.display_name if known else None,
        "aliases": [
            alias for alias, model_id in MODEL_ALIASES.items() if model_id == config.model_id
        ],
        "family": caps.family,
        "capabilities": {
            "optimize_prompt_modes": (
                ["standard", "fast"] if caps.supports_fast_optimize_prompt else ["standard"]
            ),
            "max_reference_images": caps.max_reference_images,
            "layer_decomposition": caps.supports_layer_decomposition,
            "transparent_background": caps.supports_background,
            "size_presets": sorted(caps.allowed_presets, key=preset_numeric_sort_key),
            "min_size_pixels": caps.min_size_pixels,
            "max_size_pixels": caps.max_size_pixels,
            "output_format": caps.supports_output_format,
            "stream": caps.supports_stream,
            "web_search": caps.supports_tools,
            "sequential_generation": caps.supports_sequential_generation,
        },
    }


def _pixel_range_text(caps: dict[str, Any]) -> str:
    """渲染像素总量区间，None 边表示单边约束，双 None 表示不设限。"""
    low, high = caps["min_size_pixels"], caps["max_size_pixels"]
    if low is None and high is None:
        return "不设限"
    if low is None:
        return f"不超过 {high}"
    if high is None:
        return f"不低于 {low}"
    return f"{low}-{high}"


def build_model_info_description() -> str:
    """组装 get_model_info 的工具描述，两段列举由标签序派生。"""
    numeric_fields = "、".join(label for label, _ in _NUMERIC_FIELD_LABELS)
    capabilities = "、".join(entry.label for entry in CAPABILITY_LABELS)
    return (
        "查询当前配置模型的能力快照。\n"
        "\n"
        f"返回当前模型 ID、别名与能力字段：{numeric_fields}，\n"
        f"以及{capabilities}的支持情况。\n"
        "生成工具的模型相关参数是否可用以本工具结果为准；不支持时生成工具会报错并附支持该能力的模型清单。\n"
        "模型由服务器配置决定，更换模型请告知用户调整服务器配置。"
    )


def build_model_info_text(payload: dict[str, Any]) -> str:
    """将能力快照渲染为模型可读的中文摘要，「支持/不支持」两行与工具描述共用标签序。"""
    caps: dict[str, Any] = payload["capabilities"]
    if payload["family"] == MODEL_FAMILY_UNKNOWN:
        lines = [f"当前模型：{payload['model_id']}（未识别家族，能力按全放行兜底）"]
    else:
        lines = [f"当前模型：{payload['display_name']}（{payload['model_id']}）"]
    if payload["aliases"]:
        lines.append(f"别名：{'、'.join(payload['aliases'])}")
    lines.append(f"提示词优化：{'、'.join(caps['optimize_prompt_modes'])}")
    lines.append(f"参考图上限：{caps['max_reference_images']} 张")
    lines.append(f"尺寸档位：{'/'.join(caps['size_presets'])}；像素总量 {_pixel_range_text(caps)}")
    supported = [entry.label for entry in CAPABILITY_LABELS if caps[entry.payload_key]]
    unsupported = [entry.label for entry in CAPABILITY_LABELS if not caps[entry.payload_key]]
    lines.append(f"支持：{'、'.join(supported) if supported else '无'}")
    lines.append(f"不支持：{'、'.join(unsupported) if unsupported else '无'}")
    return "\n".join(lines)
