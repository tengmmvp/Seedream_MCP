"""Seedream 模型家族解析与能力声明。

集中管理各模型家族的识别与能力差异（output_format / tools / stream / 组图 / 参考图上限），
供 validation、config、client、schemas 共享，避免分散的子串判定与能力表重复。

本模块是数据驱动校验的唯一数据源：MODEL_CAPABILITIES 等数据表驱动各处的模型相关
判定，新增模型只需扩展数据表而无需修改调用方代码。需注意 5.0 Pro 的 Model ID 含
"doubao-seedream-5-0" 子串，与 5.0 Lite 规则重叠，家族解析时须先匹配 Pro 以免误判。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# 参考图上限常量
SEEDREAM_50PRO_MAX_REFERENCE_IMAGES = 10  # 5.0 Pro 最多 10 张参考图
SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES = 14  # 5.0 Lite / 4.5 / 4.0 最多 14 张参考图

# 各家族像素尺寸范围与倍数约束，供 validate_size_for_model 数据驱动校验
SEEDREAM_50PRO_MIN_SIZE_PIXELS = 1280 * 720  # 921600
SEEDREAM_50PRO_MAX_SIZE_PIXELS = 2048 * 2048  # 4194304
SEEDREAM_50PRO_SIZE_PIXEL_MULTIPLE = 16  # 5.0 Pro 像素宽高须为 16 的倍数
SEEDREAM_5X_MIN_SIZE_PIXELS = 2560 * 1440
SEEDREAM_5X_MAX_SIZE_PIXELS = 4096 * 4096  # 16777216
SEEDREAM_45_MIN_SIZE_PIXELS = 2560 * 1440
SEEDREAM_45_MAX_SIZE_PIXELS = 4096 * 4096
SEEDREAM_40_MIN_SIZE_PIXELS = 1280 * 720  # 921600
SEEDREAM_40_MAX_SIZE_PIXELS = 4096 * 4096

# 模型家族规范名
MODEL_FAMILY_50_PRO = "5.0-pro"
MODEL_FAMILY_50_LITE = "5.0-lite"  # 5.0 与 5.0-lite 共用 Model ID，此家族代表两者
MODEL_FAMILY_45 = "4.5"
MODEL_FAMILY_40 = "4.0"
MODEL_FAMILY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelCapabilities:
    """单个模型家族的能力声明，集中描述各模型支持的功能与限制。

    字段含义：
    - supports_output_format: 是否支持 output_format 参数，仅 5.0 系列支持。
    - supports_tools: 是否支持联网搜索等生成工具，仅 5.0 Lite 支持。
    - supports_stream: 是否支持流式输出，5.0 Pro 不支持。
    - max_reference_images: 参考图数量上限，5.0 Pro 为 10，其余家族为 14。
    - allowed_presets: 允许的尺寸预设档位白名单，驱动 validate_size_for_model 档位校验。
    - min_size_pixels/max_size_pixels: 像素总量的上下限，None 表示该家族不约束像素区间。
    - size_pixel_multiple: 像素宽高须为该值的倍数，None 表示不约束（5.0 Pro 要求 16 的倍数）。
    - supports_fast_optimize_prompt: 是否支持 optimize_prompt_options.mode=fast。
    """

    family: str
    display_name: str
    supports_output_format: bool
    supports_tools: bool
    supports_stream: bool
    max_reference_images: int
    allowed_presets: frozenset[str]
    min_size_pixels: Optional[int]
    max_size_pixels: Optional[int]
    size_pixel_multiple: Optional[int]
    supports_fast_optimize_prompt: bool = True


# 家族解析 token 表：顺序敏感，5.0 Pro 须先于 5.0 Lite。
# Pro 的 Model ID 含 "doubao-seedream-5-0" 子串，若 Lite 规则在前会被误归入 Lite。
_MODEL_FAMILY_TOKENS: list[tuple[str, tuple[str, ...]]] = [
    (MODEL_FAMILY_50_PRO, ("doubao-seedream-5-0-pro", "doubao-seedream-5.0-pro")),
    (MODEL_FAMILY_50_LITE, ("doubao-seedream-5-0", "doubao-seedream-5.0")),
    (MODEL_FAMILY_45, ("doubao-seedream-4-5", "doubao-seedream-4.5")),
    (MODEL_FAMILY_40, ("doubao-seedream-4-0", "doubao-seedream-4.0")),
]


def _resolve_model_family(model_id: str) -> str:
    """将模型标识解析为规范家族名，未命中已知家族时返回 unknown。"""
    normalized = (model_id or "").lower()
    for family, tokens in _MODEL_FAMILY_TOKENS:
        if any(token in normalized for token in tokens):
            return family
    return MODEL_FAMILY_UNKNOWN


# 各家族能力表；unknown 默认放行全部能力，兼容 Endpoint ID 等无法识别的模型
MODEL_CAPABILITIES: dict[str, ModelCapabilities] = {
    MODEL_FAMILY_50_PRO: ModelCapabilities(
        family=MODEL_FAMILY_50_PRO,
        display_name="doubao-seedream-5.0-pro",
        supports_output_format=True,
        supports_tools=False,
        supports_stream=False,
        max_reference_images=SEEDREAM_50PRO_MAX_REFERENCE_IMAGES,
        allowed_presets=frozenset({"1K", "2K"}),
        min_size_pixels=SEEDREAM_50PRO_MIN_SIZE_PIXELS,
        max_size_pixels=SEEDREAM_50PRO_MAX_SIZE_PIXELS,
        size_pixel_multiple=SEEDREAM_50PRO_SIZE_PIXEL_MULTIPLE,
        supports_fast_optimize_prompt=False,
    ),
    MODEL_FAMILY_50_LITE: ModelCapabilities(
        family=MODEL_FAMILY_50_LITE,
        display_name="doubao-seedream-5.0",
        supports_output_format=True,
        supports_tools=True,
        supports_stream=True,
        max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
        allowed_presets=frozenset({"2K", "3K", "4K"}),
        min_size_pixels=SEEDREAM_5X_MIN_SIZE_PIXELS,
        max_size_pixels=SEEDREAM_5X_MAX_SIZE_PIXELS,
        size_pixel_multiple=None,
        supports_fast_optimize_prompt=False,
    ),
    MODEL_FAMILY_45: ModelCapabilities(
        family=MODEL_FAMILY_45,
        display_name="doubao-seedream-4.5",
        supports_output_format=False,
        supports_tools=False,
        supports_stream=True,
        max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
        allowed_presets=frozenset({"2K", "4K"}),
        min_size_pixels=SEEDREAM_45_MIN_SIZE_PIXELS,
        max_size_pixels=SEEDREAM_45_MAX_SIZE_PIXELS,
        size_pixel_multiple=None,
        supports_fast_optimize_prompt=False,
    ),
    MODEL_FAMILY_40: ModelCapabilities(
        family=MODEL_FAMILY_40,
        display_name="doubao-seedream-4.0",
        supports_output_format=False,
        supports_tools=False,
        supports_stream=True,
        max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
        allowed_presets=frozenset({"1K", "2K", "4K"}),
        min_size_pixels=SEEDREAM_40_MIN_SIZE_PIXELS,
        max_size_pixels=SEEDREAM_40_MAX_SIZE_PIXELS,
        size_pixel_multiple=None,
    ),
    MODEL_FAMILY_UNKNOWN: ModelCapabilities(
        family=MODEL_FAMILY_UNKNOWN,
        display_name="当前",
        supports_output_format=True,
        supports_tools=True,
        supports_stream=True,
        max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
        allowed_presets=frozenset({"1K", "2K", "3K", "4K"}),
        min_size_pixels=None,
        max_size_pixels=None,
        size_pixel_multiple=None,
    ),
}


def get_model_capabilities(model_id: str) -> ModelCapabilities:
    """返回模型标识对应的能力声明，未知模型返回放行全部能力的默认声明。"""
    return MODEL_CAPABILITIES[_resolve_model_family(model_id)]


def is_seedream_50_pro_model(model_id: str) -> bool:
    """判断是否为 Seedream 5.0 Pro 模型，供外部调用方做模型相关分支。

    5.0 Pro 与 5.0 Lite 存在能力差异：不支持组图（sequential_image_generation）、
    联网搜索（tools）、流式输出（stream），参考图上限为 10 张，尺寸规则不同。
    """
    return _resolve_model_family(model_id) == MODEL_FAMILY_50_PRO


def get_max_reference_images(model_id: str) -> int:
    """返回模型支持的最大参考图数量，由能力表统一提供。

    - Seedream 5.0 Pro：10 张
    - Seedream 5.0 Lite / 4.5 / 4.0 及未知模型：14 张
    """
    return get_model_capabilities(model_id).max_reference_images
