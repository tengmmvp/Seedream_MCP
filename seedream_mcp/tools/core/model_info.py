"""模型信息工具的核心载荷组装。

从配置的 model_id 派生当前模型的能力快照：家族、别名与能力字段，结构化载荷
与文本摘要同源派生。
"""

from __future__ import annotations

from typing import Any

from ...config import SeedreamConfig
from ...utils.model.model_capabilities import (
    MODEL_ALIASES,
    MODEL_FAMILY_UNKNOWN,
    get_model_capabilities,
    preset_numeric_sort_key,
)

# 能力开关的中文标签序与 capabilities 字段序一致，「支持/不支持」两行文本由此
# 派生，括号内为对应工具侧标识。
_CAPABILITY_LABELS: tuple[tuple[str, str], ...] = (
    ("图层拆分（layer_decomposition）", "layer_decomposition"),
    ("透明背景（background）", "transparent_background"),
    ("输出格式（output_format）", "output_format"),
    ("流式输出（stream）", "stream"),
    ("联网搜索（web_search）", "web_search"),
    ("组图生成（sequential_generation）", "sequential_generation"),
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


def build_model_info_text(payload: dict[str, Any]) -> str:
    """将能力快照渲染为模型可读的中文摘要。"""
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
    supported = [label for label, key in _CAPABILITY_LABELS if caps[key]]
    unsupported = [label for label, key in _CAPABILITY_LABELS if not caps[key]]
    lines.append(f"支持：{'、'.join(supported) if supported else '无'}")
    lines.append(f"不支持：{'、'.join(unsupported) if unsupported else '无'}")
    return "\n".join(lines)
