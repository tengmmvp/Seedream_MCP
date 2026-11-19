"""Seedream 模型家族解析与能力声明。

集中管理各模型家族的识别与能力差异，含 output_format、tools、stream、组图、参考图上限等维度，
避免分散的子串判定与能力表重复。MODEL_CAPABILITIES 等数据表是数据驱动校验的唯一数据源，
新增模型只需扩展数据表而无需修改调用方代码。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

from ...definitions.conventions import (
    LAYER_DECOMPOSITION_MAX_IMAGES,
    MAX_SEQUENTIAL_TOTAL_IMAGES,
)

# 参考图上限常量：Seedream 5.5、5.0 Pro 与 5.0 Flash 为 10，其余家族为 14。
SEEDREAM_LIMITED_MAX_REFERENCE_IMAGES = 10
SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES = 14

# 官方口径：1 张底图 + 至多 16 个图层；5.0 Pro 与 5.0 Flash 沿用此上限。
SEEDREAM_LEGACY_LAYER_DECOMPOSITION_MAX_IMAGES = 17

# 各家族像素尺寸范围界值，供 validate_size_for_model 数据驱动校验；跨家族共用的
# 界值按共用家族的输出档位区间命名（区间一致双段、单边一致单段）。
SEEDREAM_1K_MIN_SIZE_PIXELS = 1280 * 720
SEEDREAM_2K_4K_MIN_SIZE_PIXELS = 2560 * 1440
SEEDREAM_1K_2K_MAX_SIZE_PIXELS = 4624220  # 官方 2048x2048x1.1025 的像素乘积上限
SEEDREAM_4K_MAX_SIZE_PIXELS = 4096 * 4096

# background 调用形态的文案场景名，新增形态须在此登记场景标签。
BACKGROUND_FORM_SCENES: Mapping[str, str] = MappingProxyType(
    {"text": "文生", "single": "单参考图的图生", "multi": "多参考图"}
)

# background 调用形态的值域类型，取值集与场景标签键同集，新增形态须两侧同步。
BackgroundForm = Literal["text", "single", "multi"]

# background 的调用形态全集，由场景标签键派生。
ALL_BACKGROUND_FORMS: frozenset[str] = frozenset(BACKGROUND_FORM_SCENES)

# 各取值不限调用形态的绑定表，供全放行家族与 unknown 兜底声明共用。
_BACKGROUND_FORMS_ALL_MODES: dict[str, frozenset[str]] = {
    "auto": ALL_BACKGROUND_FORMS,
    "opaque": ALL_BACKGROUND_FORMS,
    "transparent": ALL_BACKGROUND_FORMS,
}

# 模型家族规范名，作为家族解析的返回值与能力表的键。
MODEL_FAMILY_55 = "5.5"
MODEL_FAMILY_50_PRO = "5.0-pro"
MODEL_FAMILY_50_LITE = "5.0-lite"  # 5.0 与 5.0-lite 共用 Model ID，此家族代表两者。
MODEL_FAMILY_50_FLASH = "5.0-flash"
MODEL_FAMILY_45 = "4.5"
MODEL_FAMILY_40 = "4.0"
MODEL_FAMILY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelCapabilities:
    """单个模型家族的能力声明，集中描述各模型支持的功能与限制。

    字段序与 get_model_info 能力快照的调用流程一致。

    Attributes:
        family: 模型家族规范名。
        display_name: 面向用户输出的模型展示名。
        supports_fast_optimize_prompt: 是否支持 optimize_prompt_options.mode=fast。
        max_reference_images: 参考图数量上限。
        supports_layer_decomposition: 是否支持 layer_decomposition 图层拆分。
        max_layer_decomposition_images: 图层拆分单请求满额产图数（含底图），
            不支持拆分的家族为 0。
        supports_layer_decomposition_options: 是否支持 layer_decomposition_options
            配置（max_images 与 text_recognition）。
        allowed_background_modes: 允许的 background 取值白名单，空集表示不支持
            该参数。
        background_forms_by_mode: 各 background 取值的调用形态绑定表：键为取值，
            值为该取值可用的形态集合（text 文生、single 单参考图的图生、multi
            多参考图），不限形态的取值声明全形态集合常量；键集与
            allowed_background_modes 一致。
        allowed_presets: 允许的尺寸预设档位白名单。
        min_size_pixels: 像素总量的下限，None 表示该家族不约束像素区间。
        max_size_pixels: 像素总量的上限，None 表示该家族不约束像素区间。
        supports_output_format: 是否支持 output_format 参数。
        supports_stream: 是否支持流式输出。
        supports_tools: 是否支持联网搜索等生成工具。
        supports_sequential_generation: 是否支持组图生成。
        supports_safety_identifier: 是否接受 safety_identifier 终端用户标识；
            该参数由服务器自动附加，不支持时附加会被 API 拒绝。
    """

    family: str
    display_name: str
    supports_fast_optimize_prompt: bool
    max_reference_images: int
    supports_layer_decomposition: bool
    max_layer_decomposition_images: int
    supports_layer_decomposition_options: bool
    allowed_background_modes: frozenset[str]
    background_forms_by_mode: Mapping[str, frozenset[str]]
    allowed_presets: frozenset[str]
    min_size_pixels: int | None
    max_size_pixels: int | None
    supports_output_format: bool
    supports_stream: bool
    supports_tools: bool
    supports_sequential_generation: bool
    supports_safety_identifier: bool

    @property
    def allowed_background_forms(self) -> frozenset[str]:
        """transparent 取值的形态白名单，快照布尔与模型清单载荷的既有呈现投影。"""
        return self.background_forms_by_mode.get("transparent", frozenset())


# 家族解析 token 表按匹配优先级排列，区别于下方能力表的档次展示序：5.0 Pro 与
# 5.0 Flash 的 Model ID 含 5.0 Lite 的匹配子串，5.0 Flash 行必须先于 5.0 Lite 行，
# 否则 5.0 Flash 模型被误判。
_MODEL_FAMILY_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (MODEL_FAMILY_55, ("doubao-seedream-5-5", "doubao-seedream-5.5")),
    (MODEL_FAMILY_50_PRO, ("doubao-seedream-5-0-pro", "doubao-seedream-5.0-pro")),
    (MODEL_FAMILY_50_FLASH, ("doubao-seedream-5-0-flash", "doubao-seedream-5.0-flash")),
    (MODEL_FAMILY_50_LITE, ("doubao-seedream-5-0", "doubao-seedream-5.0")),
    (MODEL_FAMILY_45, ("doubao-seedream-4-5", "doubao-seedream-4.5")),
    (MODEL_FAMILY_40, ("doubao-seedream-4-0", "doubao-seedream-4.0")),
)


# 模型友好别名到真实 Model ID 的映射，config.normalize_model_selector 据此展开别名。
# 各公共清单均取只读口径包装，防止被调用方原地改写。
MODEL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "doubao-seedream-5.5": "doubao-seedream-5-5-261028",
        "doubao-seedream-5.0-pro": "doubao-seedream-5-0-pro-260628",
        "doubao-seedream-5.0": "doubao-seedream-5-0-260128",
        "doubao-seedream-5.0-lite": "doubao-seedream-5-0-260128",
        "doubao-seedream-5.0-flash": "doubao-seedream-5-0-flash-260915",
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
# safety_identifier 例外：该参数由服务器主动附加，unknown 不附加，避免向能力
# 未确认的模型发参而被 API 拒绝。
MODEL_CAPABILITIES: Mapping[str, ModelCapabilities] = MappingProxyType(
    {
        MODEL_FAMILY_55: ModelCapabilities(
            family=MODEL_FAMILY_55,
            display_name="doubao-seedream-5.5",
            supports_fast_optimize_prompt=False,
            max_reference_images=SEEDREAM_LIMITED_MAX_REFERENCE_IMAGES,
            supports_layer_decomposition=True,
            # 官方口径：1 张底图 + 至多 99 个图层，与配置值域上界同源。
            max_layer_decomposition_images=LAYER_DECOMPOSITION_MAX_IMAGES,
            supports_layer_decomposition_options=True,
            allowed_background_modes=frozenset({"auto", "opaque", "transparent"}),
            background_forms_by_mode=_BACKGROUND_FORMS_ALL_MODES,
            allowed_presets=frozenset({"1K", "1.5K", "2K"}),
            min_size_pixels=SEEDREAM_1K_MIN_SIZE_PIXELS,
            max_size_pixels=SEEDREAM_1K_2K_MAX_SIZE_PIXELS,
            supports_output_format=True,
            supports_stream=False,
            supports_tools=False,
            supports_sequential_generation=False,
            supports_safety_identifier=True,
        ),
        MODEL_FAMILY_50_PRO: ModelCapabilities(
            family=MODEL_FAMILY_50_PRO,
            display_name="doubao-seedream-5.0-pro",
            supports_fast_optimize_prompt=True,
            max_reference_images=SEEDREAM_LIMITED_MAX_REFERENCE_IMAGES,
            supports_layer_decomposition=True,
            max_layer_decomposition_images=SEEDREAM_LEGACY_LAYER_DECOMPOSITION_MAX_IMAGES,
            supports_layer_decomposition_options=False,
            allowed_background_modes=frozenset({"opaque", "transparent"}),
            background_forms_by_mode={
                "opaque": ALL_BACKGROUND_FORMS,
                "transparent": frozenset({"single"}),
            },
            allowed_presets=frozenset({"1K", "1.5K", "2K"}),
            min_size_pixels=SEEDREAM_1K_MIN_SIZE_PIXELS,
            max_size_pixels=SEEDREAM_1K_2K_MAX_SIZE_PIXELS,
            supports_output_format=True,
            supports_stream=False,
            supports_tools=False,
            supports_sequential_generation=False,
            supports_safety_identifier=True,
        ),
        MODEL_FAMILY_50_LITE: ModelCapabilities(
            family=MODEL_FAMILY_50_LITE,
            display_name="doubao-seedream-5.0",
            supports_fast_optimize_prompt=False,
            max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
            supports_layer_decomposition=False,
            max_layer_decomposition_images=0,
            supports_layer_decomposition_options=False,
            allowed_background_modes=frozenset(),
            background_forms_by_mode={},
            allowed_presets=frozenset({"2K", "3K", "4K"}),
            min_size_pixels=SEEDREAM_2K_4K_MIN_SIZE_PIXELS,
            max_size_pixels=SEEDREAM_4K_MAX_SIZE_PIXELS,
            supports_output_format=True,
            supports_stream=True,
            supports_tools=True,
            supports_sequential_generation=True,
            supports_safety_identifier=False,
        ),
        MODEL_FAMILY_50_FLASH: ModelCapabilities(
            family=MODEL_FAMILY_50_FLASH,
            display_name="doubao-seedream-5.0-flash",
            supports_fast_optimize_prompt=False,
            max_reference_images=SEEDREAM_LIMITED_MAX_REFERENCE_IMAGES,
            supports_layer_decomposition=True,
            max_layer_decomposition_images=SEEDREAM_LEGACY_LAYER_DECOMPOSITION_MAX_IMAGES,
            supports_layer_decomposition_options=False,
            allowed_background_modes=frozenset({"opaque", "transparent"}),
            background_forms_by_mode={
                "opaque": ALL_BACKGROUND_FORMS,
                "transparent": frozenset({"single"}),
            },
            allowed_presets=frozenset({"1K", "1.5K", "2K"}),
            min_size_pixels=SEEDREAM_1K_MIN_SIZE_PIXELS,
            max_size_pixels=SEEDREAM_1K_2K_MAX_SIZE_PIXELS,
            supports_output_format=True,
            supports_stream=False,
            supports_tools=False,
            supports_sequential_generation=False,
            supports_safety_identifier=True,
        ),
        MODEL_FAMILY_45: ModelCapabilities(
            family=MODEL_FAMILY_45,
            display_name="doubao-seedream-4.5",
            supports_fast_optimize_prompt=False,
            max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
            supports_layer_decomposition=False,
            max_layer_decomposition_images=0,
            supports_layer_decomposition_options=False,
            allowed_background_modes=frozenset(),
            background_forms_by_mode={},
            allowed_presets=frozenset({"2K", "4K"}),
            min_size_pixels=SEEDREAM_2K_4K_MIN_SIZE_PIXELS,
            max_size_pixels=SEEDREAM_4K_MAX_SIZE_PIXELS,
            supports_output_format=False,
            supports_stream=True,
            supports_tools=False,
            supports_sequential_generation=True,
            supports_safety_identifier=False,
        ),
        MODEL_FAMILY_40: ModelCapabilities(
            family=MODEL_FAMILY_40,
            display_name="doubao-seedream-4.0",
            supports_fast_optimize_prompt=True,
            max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
            supports_layer_decomposition=False,
            max_layer_decomposition_images=0,
            supports_layer_decomposition_options=False,
            allowed_background_modes=frozenset(),
            background_forms_by_mode={},
            allowed_presets=frozenset({"1K", "2K", "4K"}),
            min_size_pixels=SEEDREAM_1K_MIN_SIZE_PIXELS,
            max_size_pixels=SEEDREAM_4K_MAX_SIZE_PIXELS,
            supports_output_format=False,
            supports_stream=True,
            supports_tools=False,
            supports_sequential_generation=True,
            supports_safety_identifier=False,
        ),
        MODEL_FAMILY_UNKNOWN: ModelCapabilities(
            family=MODEL_FAMILY_UNKNOWN,
            display_name="当前",
            supports_fast_optimize_prompt=True,
            max_reference_images=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
            supports_layer_decomposition=True,
            # options 放行使产图数按请求投影取值，满额须对齐值域上界。
            max_layer_decomposition_images=LAYER_DECOMPOSITION_MAX_IMAGES,
            supports_layer_decomposition_options=True,
            allowed_background_modes=frozenset({"auto", "opaque", "transparent"}),
            background_forms_by_mode=_BACKGROUND_FORMS_ALL_MODES,
            allowed_presets=frozenset({"1K", "1.5K", "2K", "3K", "4K"}),
            min_size_pixels=None,
            max_size_pixels=None,
            supports_output_format=True,
            supports_stream=True,
            supports_tools=True,
            supports_sequential_generation=True,
            supports_safety_identifier=False,
        ),
    }
)


class CapabilityLabel(NamedTuple):
    """能力开关的中文标签条目。

    Attributes:
        zh: 中文短语。
        param: 标签括注，对齐工具参数名。
        capability: ModelCapabilities 的能力字段名。
        payload_key: 能力快照的载荷键，透明背景、透明通道全形态与联网搜索三键与工具参数名不同。
    """

    zh: str
    param: str
    capability: str
    payload_key: str

    @property
    def label(self) -> str:
        """完整标签，形如「联网搜索（tools）」。"""
        return f"{self.zh}（{self.param}）"


# 能力开关的中文标签表，序与能力快照的 capabilities 字段序一致。
CAPABILITY_LABELS: tuple[CapabilityLabel, ...] = (
    CapabilityLabel(
        "图层拆分", "layer_decomposition", "supports_layer_decomposition", "layer_decomposition"
    ),
    CapabilityLabel(
        "图层拆分配置与富文本 OCR",
        "layer_decomposition_options",
        "supports_layer_decomposition_options",
        "layer_decomposition_options",
    ),
    CapabilityLabel("透明背景", "background", "allowed_background_modes", "transparent_background"),
    CapabilityLabel(
        "透明通道全形态",
        "background",
        "allowed_background_forms",
        "full_form_transparency",
    ),
    CapabilityLabel("输出格式", "output_format", "supports_output_format", "output_format"),
    CapabilityLabel("流式输出", "stream", "supports_stream", "stream"),
    CapabilityLabel("联网搜索", "tools", "supports_tools", "web_search"),
    CapabilityLabel(
        "组图生成",
        "sequential_generation",
        "supports_sequential_generation",
        "sequential_generation",
    ),
    CapabilityLabel(
        "终端用户标识",
        "safety_identifier",
        "supports_safety_identifier",
        "safety_identifier",
    ),
)

# capability 字段名到完整标签的只读索引，capability_param_label 的查找源。
_CAPABILITY_LABEL_BY_FIELD: Mapping[str, str] = MappingProxyType(
    {entry.capability: entry.label for entry in CAPABILITY_LABELS}
)


def capability_param_label(capability: str) -> str:
    """按能力字段名返回完整参数标签，未登记的字段名抛 KeyError 暴露笔误。"""
    return _CAPABILITY_LABEL_BY_FIELD[capability]


def get_model_capabilities(model_id: str) -> ModelCapabilities:
    """返回模型标识对应的能力声明，未知模型返回放行全部能力的默认声明（safety_identifier 例外）。"""
    return MODEL_CAPABILITIES[_resolve_model_family(model_id)]


def get_max_reference_images(model_id: str) -> int:
    """返回模型支持的最大参考图数量，由能力表统一提供。"""
    return get_model_capabilities(model_id).max_reference_images


def max_per_request_image_count() -> int:
    """返回单请求合法满额产图数，响应体上限推导与解析条目下限的单一取值点。"""
    # 组图总数为跨家族协议常量、拆分满额随能力表声明，家族配额以能力表为唯一来源。
    return max(
        MAX_SEQUENTIAL_TOTAL_IMAGES,
        max(caps.max_layer_decomposition_images for caps in MODEL_CAPABILITIES.values()),
    )


# 能力表为静态数据，导入期绑定一次供请求热路径直用；需随能力表活算的场合（如测试）走函数。
MAX_PER_REQUEST_IMAGE_COUNT = max_per_request_image_count()


def supported_family_display_names(capability: str) -> str:
    """返回能力表声明该能力为真的各家族展示名，以顿号拼接供文案直用。

    真值判定覆盖布尔与白名单两类能力字段（布尔真、非空集合）；unknown 为
    Endpoint ID 的兜底声明而非真实家族，不进入文案枚举。
    """
    return "、".join(
        caps.display_name
        for family, caps in MODEL_CAPABILITIES.items()
        if family != MODEL_FAMILY_UNKNOWN and getattr(caps, capability)
    )


def layer_decomposition_presets() -> frozenset[str]:
    """返回支持图层拆分家族的尺寸档位并集，描述文案与档位校验同源派生。"""
    presets: set[str] = set()
    for family, caps in MODEL_CAPABILITIES.items():
        if family != MODEL_FAMILY_UNKNOWN and caps.supports_layer_decomposition:
            presets |= caps.allowed_presets
    return frozenset(presets)


def layer_decomposition_presets_text() -> str:
    """返回图层拆分档位并集的斜杠拼接展示串。"""
    return "/".join(sorted(layer_decomposition_presets(), key=preset_numeric_sort_key))


def limited_reference_families_annotation() -> str:
    """按上限分组枚举参考图上限低于默认值的家族，返回带括号注记，无受限家族时为空串。"""
    groups: dict[int, list[str]] = {}
    for family, caps in MODEL_CAPABILITIES.items():
        if family == MODEL_FAMILY_UNKNOWN or caps.max_reference_images >= (
            SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
        ):
            continue
        groups.setdefault(caps.max_reference_images, []).append(caps.display_name)
    parts = [f"{'、'.join(names)} 最多 {limit} 张" for limit, names in groups.items()]
    return f"（{'；'.join(parts)}）" if parts else ""


def layer_decomposition_layer_limit_groups() -> tuple[tuple[int, str], ...]:
    """按图层数上限降序返回 (上限, 家族展示名) 分组，供拆分描述文案直用。

    上限为扣除底图后的纯图层数；unknown 为兜底声明不进入枚举。
    """
    groups: dict[int, list[str]] = {}
    for family, caps in MODEL_CAPABILITIES.items():
        if family == MODEL_FAMILY_UNKNOWN or not caps.supports_layer_decomposition:
            continue
        groups.setdefault(caps.max_layer_decomposition_images - 1, []).append(caps.display_name)
    return tuple(
        (layers, "、".join(names)) for layers, names in sorted(groups.items(), reverse=True)
    )


def all_background_modes() -> frozenset[str]:
    """返回能力表声明的 background 取值并集，供校验层白名单单一来源派生。"""
    modes: set[str] = set()
    for caps in MODEL_CAPABILITIES.values():
        modes |= caps.allowed_background_modes
    return frozenset(modes)


def layer_decomposition_layer_limits_text() -> str:
    """返回拆分图层数上限的分组展示串，形如「最多 99 个（A）或 16 个（B、C）」。"""
    groups = layer_decomposition_layer_limit_groups()
    if len(groups) == 1:
        return f"最多 {groups[0][0]} 个"
    return "最多 " + "或 ".join(f"{layers} 个（{names}）" for layers, names in groups)


def preset_numeric_sort_key(preset: str) -> tuple[float, str]:
    """尺寸档位的排序键：按数值前缀升序，其次按字典序保证稳定。

    字典序会把 1.5K 排在 1K 之前，与档位的数值视觉顺序相反。
    数值前缀无法解析的档位排在末尾，不阻断排序。
    """
    try:
        return (float(preset.removesuffix("K")), preset)
    except ValueError:
        return (float("inf"), preset)


def model_payloads() -> list[dict[str, object]]:
    """按 MODEL_ALIASES 顺序产出各模型的能力展示载荷。

    每条目含 alias 与 model_id 及 asdict 展开的能力字段；集合型字段归一为
    有序列表保证 JSON 可序列化，能力表新增字段自动进入载荷，消费方无需手工
    同步字段清单。background_forms_by_mode 例外：换出为既有
    allowed_background_forms 呈现键（transparent 形态投影），不新增载荷维度。
    """
    # 集合字段的归一排序表：(字段名, 排序键) 对，排序键 None 为纯字典序。
    set_field_sort_keys: tuple[tuple[str, Callable[[str], Any] | None], ...] = (
        ("allowed_presets", preset_numeric_sort_key),
        ("allowed_background_modes", None),
        ("allowed_background_forms", None),
    )
    payloads: list[dict[str, object]] = []
    for alias, model_id in MODEL_ALIASES.items():
        caps = get_model_capabilities(model_id)
        capabilities = asdict(caps)
        del capabilities["background_forms_by_mode"]
        capabilities["allowed_background_forms"] = sorted(caps.allowed_background_forms)
        for field, sort_key in set_field_sort_keys:
            value = capabilities.get(field)
            if isinstance(value, (set, frozenset, list)):
                capabilities[field] = (
                    sorted(value) if sort_key is None else sorted(value, key=sort_key)
                )
        payloads.append({"alias": alias, "model_id": model_id, **capabilities})
    return payloads
