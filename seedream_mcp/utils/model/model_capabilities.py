"""Seedream 模型家族解析与能力声明。

集中管理各模型家族的识别与能力差异，含 output_format、tools、stream、组图、参考图
上限等维度，供 validation、config、client、schemas 共享，避免分散的子串判定与能力
表重复。MODEL_CAPABILITIES 等数据表是数据驱动校验的唯一数据源，新增模型只需扩展
数据表而无需修改调用方代码。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType

# 参考图上限常量：5.0 Pro 为 10，其余家族为 14，由能力表按家族引用。
SEEDREAM_50PRO_MAX_REFERENCE_IMAGES = 10
SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES = 14

# 各家族像素尺寸范围与倍数约束，供 validate_size_for_model 数据驱动校验。
# 5.0 Pro 上限对应官方 2048x2048x1.1025（4624220）的像素乘积上限。
SEEDREAM_50PRO_MIN_SIZE_PIXELS = 1280 * 720
SEEDREAM_50PRO_MAX_SIZE_PIXELS = 4624220
SEEDREAM_50PRO_SIZE_PIXEL_MULTIPLE = 16
SEEDREAM_5X_MIN_SIZE_PIXELS = 2560 * 1440
SEEDREAM_5X_MAX_SIZE_PIXELS = 4096 * 4096
SEEDREAM_45_MIN_SIZE_PIXELS = 2560 * 1440
SEEDREAM_45_MAX_SIZE_PIXELS = 4096 * 4096
SEEDREAM_40_MIN_SIZE_PIXELS = 1280 * 720
SEEDREAM_40_MAX_SIZE_PIXELS = 4096 * 4096

# 模型家族规范名，作为家族解析的返回值与能力表的键。
MODEL_FAMILY_50_PRO = "5.0-pro"
MODEL_FAMILY_50_LITE = "5.0-lite"  # 5.0 与 5.0-lite 共用 Model ID，此家族代表两者。
MODEL_FAMILY_45 = "4.5"
MODEL_FAMILY_40 = "4.0"
MODEL_FAMILY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelCapabilities:
    """单个模型家族的能力声明，集中描述各模型支持的功能与限制。

    Attributes:
        family: 模型家族规范名。
        display_name: 面向用户输出的模型展示名，供错误消息引用。
        supports_output_format: 是否支持 output_format 参数。
        supports_tools: 是否支持联网搜索等生成工具。
        supports_stream: 是否支持流式输出。
        max_reference_images: 参考图数量上限。
        allowed_presets: 允许的尺寸预设档位白名单。
        min_size_pixels: 像素总量的下限，None 表示该家族不约束像素区间。
        max_size_pixels: 像素总量的上限，None 表示该家族不约束像素区间。
        size_pixel_multiple: 像素宽高须为该值的倍数，None 表示不约束。
        supports_fast_optimize_prompt: 是否支持 optimize_prompt_options.mode=fast。
        supports_sequential_generation: 是否支持组图生成。
        supports_layer_decomposition: 是否支持 layer_decomposition 图层拆分。
        supports_background: 是否支持 background 透明通道参数。
    """

    family: str
    display_name: str
    supports_output_format: bool
    supports_tools: bool
    supports_stream: bool
    max_reference_images: int
    allowed_presets: frozenset[str]
    min_size_pixels: int | None
    max_size_pixels: int | None
    size_pixel_multiple: int | None
    supports_fast_optimize_prompt: bool = True
    supports_sequential_generation: bool = True
    supports_layer_decomposition: bool = False
    supports_background: bool = False


# 家族解析 token 表：顺序敏感，Pro 的 Model ID 含 Lite 的匹配子串，须先匹配 Pro。
_MODEL_FAMILY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (MODEL_FAMILY_50_PRO, ("doubao-seedream-5-0-pro", "doubao-seedream-5.0-pro")),
    (MODEL_FAMILY_50_LITE, ("doubao-seedream-5-0", "doubao-seedream-5.0")),
    (MODEL_FAMILY_45, ("doubao-seedream-4-5", "doubao-seedream-4.5")),
    (MODEL_FAMILY_40, ("doubao-seedream-4-0", "doubao-seedream-4.0")),
)


# 模型友好别名到真实 Model ID 的映射，config.normalize_model_selector 据此展开别名。
# 各公共清单均取只读口径包装，防止被调用方原地改写。
MODEL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "doubao-seedream-5.0-pro": "doubao-seedream-5-0-pro-260628",
        "doubao-seedream-5.0": "doubao-seedream-5-0-260128",
        "doubao-seedream-5.0-lite": "doubao-seedream-5-0-260128",
        "doubao-seedream-4.5": "doubao-seedream-4-5-251128",
        "doubao-seedream-4.0": "doubao-seedream-4-0-250828",
    }
)

# 已下线模型的特征 token，model_id 命中任意 token 时 config 校验拒绝。
DEPRECATED_MODEL_TOKENS: frozenset[str] = frozenset(
    {
        "doubao-seedream-3-0",
        "doubao-seedream-3.0",
        "doubao-seededit-3-0",
        "doubao-seededit-3.0",
    }
)


def _resolve_model_family(model_id: str) -> str:
    """将模型标识解析为规范家族名，未命中已知家族时返回 unknown。"""
    normalized = (model_id or "").lower()
    for family, tokens in _MODEL_FAMILY_TOKENS:
        if any(token in normalized for token in tokens):
            return family
    return MODEL_FAMILY_UNKNOWN


# 各家族能力表；unknown 默认放行全部能力，兼容 Endpoint ID 等无法识别的模型。
MODEL_CAPABILITIES: Mapping[str, ModelCapabilities] = MappingProxyType(
    {
        MODEL_FAMILY_50_PRO: ModelCapabilities(
            family=MODEL_FAMILY_50_PRO,
            display_name="doubao-seedream-5.0-pro",
            supports_output_format=True,
            supports_tools=False,
            supports_stream=False,
            max_reference_images=SEEDREAM_50PRO_MAX_REFERENCE_IMAGES,
            allowed_presets=frozenset({"1K", "1.5K", "2K"}),
            min_size_pixels=SEEDREAM_50PRO_MIN_SIZE_PIXELS,
            max_size_pixels=SEEDREAM_50PRO_MAX_SIZE_PIXELS,
            size_pixel_multiple=SEEDREAM_50PRO_SIZE_PIXEL_MULTIPLE,
            supports_fast_optimize_prompt=True,
            supports_sequential_generation=False,
            supports_layer_decomposition=True,
            supports_background=True,
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
            supports_fast_optimize_prompt=True,
        ),
        MODEL_FAMILY_UNKNOWN: ModelCapabilities(
            family=MODEL_FAMILY_UNKNOWN,
            display_name="当前",
            supports_output_format=True,
            supports_tools=True,
            supports_stream=True,
            max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
            allowed_presets=frozenset({"1K", "1.5K", "2K", "3K", "4K"}),
            min_size_pixels=None,
            max_size_pixels=None,
            size_pixel_multiple=None,
            supports_layer_decomposition=True,
            supports_background=True,
        ),
    }
)


def get_model_capabilities(model_id: str) -> ModelCapabilities:
    """返回模型标识对应的能力声明，未知模型返回放行全部能力的默认声明。"""
    return MODEL_CAPABILITIES[_resolve_model_family(model_id)]


def get_max_reference_images(model_id: str) -> int:
    """返回模型支持的最大参考图数量，由能力表统一提供。"""
    return get_model_capabilities(model_id).max_reference_images


def model_payloads() -> list[dict[str, object]]:
    """按 MODEL_ALIASES 顺序产出各模型的能力展示载荷，供资源与 Web 清单共用。

    每条目含 alias 与 model_id 及 asdict 展开的能力字段；allowed_presets 归一为
    有序列表，能力表新增字段自动进入载荷，消费方无需手工同步字段清单。
    """
    payloads: list[dict[str, object]] = []
    for alias, model_id in MODEL_ALIASES.items():
        capabilities = asdict(get_model_capabilities(model_id))
        presets = capabilities.get("allowed_presets")
        if isinstance(presets, (set, frozenset, list)):
            capabilities["allowed_presets"] = sorted(presets)
        payloads.append({"alias": alias, "model_id": model_id, **capabilities})
    return payloads
