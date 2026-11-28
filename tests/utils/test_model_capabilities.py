"""模型家族解析与能力表测试，锁定数据驱动重构的中心化逻辑。

重点守护 5.0 Pro / Flash 须先于 5.0 Lite 解析的顺序，避免含 "5-0" 子串的 ID 被误判。
"""

import io
import os
from dataclasses import asdict
from typing import cast, get_origin, get_type_hints

import pytest
from PIL import Image

from seedream_mcp.definitions.conventions import LAYER_DECOMPOSITION_MAX_IMAGES
from seedream_mcp.utils.model.capabilities import (
    ALL_BACKGROUND_FORMS,
    CAPABILITY_LABELS,
    MODEL_ALIASES,
    MODEL_CAPABILITIES,
    MODEL_FAMILY_40,
    MODEL_FAMILY_45,
    MODEL_FAMILY_50_FLASH,
    MODEL_FAMILY_50_LITE,
    MODEL_FAMILY_50_PRO,
    MODEL_FAMILY_UNKNOWN,
    SEEDREAM_1K_2K_MAX_SIZE_PIXELS,
    SEEDREAM_4K_MAX_SIZE_PIXELS,
    ModelCapabilities,
    _resolve_model_family,
    get_model_capabilities,
    max_single_image_bytes,
    model_payloads,
    preset_numeric_sort_key,
    supported_family_display_names,
)


def test_background_fields_cross_invariants() -> None:
    """background 跨字段不变量：支持 background 的家族必含 transparent，transparent
    在取值集时形态白名单非空；绑定表键集与取值白名单一致（取值门控放行后校验器
    直接下标的前提）且形态取值落在值域内，表编辑越界即红。
    """
    for family, caps in MODEL_CAPABILITIES.items():
        if caps.allowed_background_modes:
            assert "transparent" in caps.allowed_background_modes, family
        if "transparent" in caps.allowed_background_modes:
            assert caps.allowed_background_forms, family
        assert set(caps.background_forms_by_mode) == caps.allowed_background_modes, family
        assert all(
            forms <= ALL_BACKGROUND_FORMS for forms in caps.background_forms_by_mode.values()
        ), family


def test_all_model_aliases_resolve_to_known_family() -> None:
    """所有 MODEL_ALIASES 解析出的家族必须非 unknown。

    遗漏在 _MODEL_FAMILY_TOKENS 补 token 会静默 unknown 并放行全部能力，校验被跳过。
    """
    for alias, model_id in MODEL_ALIASES.items():
        assert _resolve_model_family(model_id) != MODEL_FAMILY_UNKNOWN, (
            f"别名 {alias} -> {model_id} 未命中任何家族 token，"
            "请在 _MODEL_FAMILY_TOKENS 补充匹配规则"
        )


def test_resolve_model_family_known_and_unknown_ids() -> None:
    """Pro / Flash 的 Model ID 含 5-0 子串仍须解析为各自家族而非 Lite。"""
    assert _resolve_model_family("doubao-seedream-5-0-pro-260628") == MODEL_FAMILY_50_PRO
    assert _resolve_model_family("doubao-seedream-5.0-pro") == MODEL_FAMILY_50_PRO
    assert _resolve_model_family("doubao-seedream-5-0-flash-260915") == MODEL_FAMILY_50_FLASH
    assert _resolve_model_family("doubao-seedream-5.0-flash") == MODEL_FAMILY_50_FLASH
    assert _resolve_model_family("doubao-seedream-5-0-260128") == MODEL_FAMILY_50_LITE
    assert _resolve_model_family("doubao-seedream-5.0") == MODEL_FAMILY_50_LITE
    assert _resolve_model_family("doubao-seedream-4-5-251128") == MODEL_FAMILY_45
    assert _resolve_model_family("doubao-seedream-4.0") == MODEL_FAMILY_40
    assert _resolve_model_family("ep-2024xxxxxxxx-xxxxx") == MODEL_FAMILY_UNKNOWN
    assert _resolve_model_family("") == MODEL_FAMILY_UNKNOWN


def test_get_model_capabilities_pro_profile() -> None:
    """Pro 能力声明关闭 tools 与 stream，参考图上限 10。"""
    caps = get_model_capabilities("doubao-seedream-5-0-pro-260628")
    assert caps.supports_output_format is True
    assert caps.supports_tools is False
    assert caps.supports_stream is False
    assert caps.max_reference_images == 10


def test_get_model_capabilities_lite_profile() -> None:
    """Lite 能力声明开启 tools 与 stream，参考图上限 14。"""
    caps = get_model_capabilities("doubao-seedream-5-0-260128")
    assert caps.supports_output_format is True
    assert caps.supports_tools is True
    assert caps.supports_stream is True
    assert caps.max_reference_images == 14


def test_get_model_capabilities_flash_profile() -> None:
    """Flash 与 Pro 同档（图层拆分/透明背景/参考图 10/1K-2K 档），唯一差异是
    Flash 不支持 fast 提示词优化；拆分配置 Pro 与 Flash 均不支持，仅 5.5 支持。
    """
    caps = get_model_capabilities("doubao-seedream-5-0-flash-260915")
    assert caps.supports_output_format is True
    assert caps.supports_tools is False
    assert caps.supports_stream is False
    assert caps.supports_sequential_generation is False
    assert caps.supports_fast_optimize_prompt is False
    assert caps.supports_layer_decomposition is True
    assert caps.supports_layer_decomposition_options is False
    assert caps.allowed_background_modes == frozenset({"opaque", "transparent"})
    assert caps.allowed_background_forms == frozenset({"single"})
    assert caps.max_reference_images == 10
    assert caps.allowed_presets == frozenset({"1K", "1.5K", "2K"})
    assert caps.min_size_pixels == 1280 * 720
    assert caps.max_size_pixels == 4624220


def test_get_model_capabilities_legacy_and_unknown_default_to_permissive() -> None:
    """4.5/4.0 不支持 output_format 与 tools，未知模型放行全部能力。"""
    assert get_model_capabilities("doubao-seedream-4-5-251128").supports_output_format is False
    assert get_model_capabilities("doubao-seedream-4-0-250828").supports_output_format is False

    unknown = get_model_capabilities("ep-unknown-endpoint")
    assert unknown.supports_output_format is True
    assert unknown.supports_tools is True
    assert unknown.supports_stream is True
    assert unknown.supports_layer_decomposition is True
    assert unknown.supports_layer_decomposition_options is True
    assert unknown.max_layer_decomposition_images == LAYER_DECOMPOSITION_MAX_IMAGES
    assert unknown.allowed_background_modes == frozenset({"auto", "opaque", "transparent"})
    assert unknown.allowed_background_forms == frozenset({"text", "single", "multi"})
    assert unknown.max_reference_images == 14


def test_options_capable_families_declare_full_layer_max() -> None:
    """支持拆分配置的家族满额须对齐 max_images 值域上界，行声明不得钳低按投影取值的产图数。"""
    offenders = [
        caps.family
        for caps in MODEL_CAPABILITIES.values()
        if caps.supports_layer_decomposition_options
        and caps.max_layer_decomposition_images != LAYER_DECOMPOSITION_MAX_IMAGES
    ]
    assert offenders == [], f"options 放行家族的拆分满额偏离值域上界: {offenders}"


def test_layer_decomposition_support_matches_max_images() -> None:
    """拆分支持为双编码字段（bool 开关与满额产图数），全表行须一致，新家族漏填在表级暴露。"""
    offenders = [
        caps.family
        for caps in MODEL_CAPABILITIES.values()
        if caps.supports_layer_decomposition != (caps.max_layer_decomposition_images > 0)
    ]
    assert offenders == [], f"图层拆分双编码字段不一致的家族: {offenders}"


def test_model_capabilities_table_is_read_only() -> None:
    """能力表为只读映射视图，调用方原地改写被拒绝，公共数据表不被污染。"""
    with pytest.raises(TypeError):
        MODEL_CAPABILITIES["new-family"] = MODEL_CAPABILITIES[MODEL_FAMILY_50_PRO]  # type: ignore[index]


def test_model_payloads_entries_match_alias_table() -> None:
    """model_payloads 条目数与顺序对齐别名表，字段集为 asdict 展开加别名两键。"""
    payloads = model_payloads()

    assert len(payloads) == len(MODEL_ALIASES)
    assert [entry["alias"] for entry in payloads] == list(MODEL_ALIASES)
    expected_fields = set(ModelCapabilities.__dataclass_fields__)
    for entry in payloads:
        assert set(entry) == {"alias", "model_id"} | expected_fields, entry["alias"]
        assert entry["model_id"] == MODEL_ALIASES[cast(str, entry["alias"])]


def test_model_payloads_allowed_presets_are_sorted_lists() -> None:
    """allowed_presets 归一为数值序列表，能力表的 frozenset 形态不进入载荷。"""
    payloads = model_payloads()

    for entry in payloads:
        presets = entry["allowed_presets"]
        assert isinstance(presets, list), entry["alias"]
        assert presets == sorted(presets, key=preset_numeric_sort_key), entry["alias"]
    by_alias = {entry["alias"]: entry for entry in payloads}
    assert by_alias["doubao-seedream-5.0-pro"]["allowed_presets"] == ["1K", "1.5K", "2K"]
    assert by_alias["doubao-seedream-4.5"]["allowed_presets"] == ["2K", "4K"]


def test_model_payloads_background_forms_by_mode_are_sorted_lists() -> None:
    """background_forms_by_mode 归一为字典，值为排序列表，能力表的 frozenset 形态不进入载荷。"""
    payloads = model_payloads()

    for entry in payloads:
        forms_by_mode = entry["background_forms_by_mode"]
        assert isinstance(forms_by_mode, dict), entry["alias"]
        for forms in forms_by_mode.values():
            assert isinstance(forms, list), entry["alias"]
            assert forms == sorted(forms), entry["alias"]
    by_alias = {entry["alias"]: entry for entry in payloads}
    assert by_alias["doubao-seedream-5.0-pro"]["background_forms_by_mode"] == {
        "opaque": ["multi", "single", "text"],
        "transparent": ["single"],
    }
    assert by_alias["doubao-seedream-5.0"]["background_forms_by_mode"] == {}


def test_model_payloads_capability_values_match_asdict() -> None:
    """条目能力取值与 asdict 全等，集合字段归一为排序列表，各取值的形态白名单同归一。"""
    for entry in model_payloads():
        caps = get_model_capabilities(cast(str, entry["model_id"]))
        expected = asdict(caps)
        expected["allowed_presets"] = sorted(
            expected["allowed_presets"], key=preset_numeric_sort_key
        )
        expected["allowed_background_modes"] = sorted(expected["allowed_background_modes"])
        expected["background_forms_by_mode"] = {
            mode: sorted(forms) for mode, forms in caps.background_forms_by_mode.items()
        }
        assert {key: entry[key] for key in expected} == expected, entry["alias"]


def test_supported_family_display_names_returns_joined_string() -> None:
    """支持家族展示名以顿号拼接返回，文案消费方无需再 join。"""
    assert supported_family_display_names("supports_output_format") == (
        "doubao-seedream-5.5、doubao-seedream-5.0-pro、doubao-seedream-5.0、"
        "doubao-seedream-5.0-flash"
    )
    assert supported_family_display_names("supports_tools") == "doubao-seedream-5.0"


def test_capability_labels_locked_verbatim() -> None:
    """能力开关中文标签与载荷键逐字锁定，改动须同步审视快照文本与门控报错的对外文案。"""
    assert [entry.label for entry in CAPABILITY_LABELS] == [
        "图层拆分（layer_decomposition）",
        "图层拆分配置与富文本 OCR（layer_decomposition_options）",
        "透明背景（background）",
        "透明通道全形态（background）",
        "输出格式（output_format）",
        "流式输出（stream）",
        "联网搜索（tools）",
        "组图生成（sequential_generation）",
        "终端用户标识（safety_identifier）",
    ]
    assert [entry.payload_key for entry in CAPABILITY_LABELS] == [
        "layer_decomposition",
        "layer_decomposition_options",
        "transparent_background",
        "full_form_transparency",
        "output_format",
        "stream",
        "web_search",
        "sequential_generation",
        "safety_identifier",
    ]


def test_capability_labels_map_unique_capability_fields() -> None:
    """标签表的 capability 字段与载荷键互异，capability 均为能力表布尔或集合白名单字段。"""
    type_hints = get_type_hints(ModelCapabilities)
    bool_fields = {name for name, tp in type_hints.items() if tp is bool}
    set_fields = {name for name, tp in type_hints.items() if get_origin(tp) is frozenset}
    # transparent 形态白名单经绑定映射派生为属性，不在类型注解面。
    derived_fields = {"allowed_background_forms"}
    capabilities = [entry.capability for entry in CAPABILITY_LABELS]
    assert len(set(capabilities)) == len(capabilities)
    assert set(capabilities) <= bool_fields | set_fields | derived_fields
    payload_keys = [entry.payload_key for entry in CAPABILITY_LABELS]
    assert len(set(payload_keys)) == len(payload_keys)


# ==================== 单图最坏字节数 ====================


def test_max_single_image_bytes_follows_family_pixels_with_margin() -> None:
    """单图最坏字节数为家族像素上限乘 4 字节再加 1/16 余量，未识别家族回退已知最大像素。"""
    two_k_raw = SEEDREAM_1K_2K_MAX_SIZE_PIXELS * 4
    four_k_raw = SEEDREAM_4K_MAX_SIZE_PIXELS * 4
    two_k_family = ("doubao-seedream-5.5", "doubao-seedream-5.0-pro", "doubao-seedream-5.0-flash")
    four_k_family = (
        "doubao-seedream-5.0",
        "doubao-seedream-4.5",
        "doubao-seedream-4.0",
        "ep-2024xxxxxxxx-xxxxx",
    )
    for model_id in two_k_family:
        assert max_single_image_bytes(model_id) == two_k_raw + two_k_raw // 16, model_id
    for model_id in four_k_family:
        assert max_single_image_bytes(model_id) == four_k_raw + four_k_raw // 16, model_id


@pytest.mark.parametrize(
    ("model_id", "side"),
    [
        pytest.param("doubao-seedream-5.5", 2150, id="2k-family"),
        pytest.param("doubao-seedream-4.0", 4096, marks=pytest.mark.slow, id="4k-family"),
    ],
)
@pytest.mark.parametrize("compress_level", [1, 6])
def test_max_single_image_bytes_covers_incompressible_png(
    model_id: str, side: int, compress_level: int
) -> None:
    """家族最大像素数的不可压缩 RGBA 图编码为 PNG 后不超过单图最坏字节数。

    像素乘 4 不是严格上界，余量须覆盖逐行滤波字节、分块头与 deflate 膨胀。
    随机噪声不可压缩，deflate 取存储块或静态 Huffman 块，后者膨胀约 5.5%，
    低压缩级别下也会出现，故两个级别都取。
    边长取不超过家族像素上限的最大正方形。
    """
    max_pixels = get_model_capabilities(model_id).max_size_pixels
    assert max_pixels is not None
    assert side * side <= max_pixels < (side + 1) * (side + 1)
    image = Image.frombytes("RGBA", (side, side), os.urandom(side * side * 4))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=compress_level)

    png_bytes = len(buffer.getvalue())
    assert png_bytes > side * side * 4
    assert png_bytes <= max_single_image_bytes(model_id)
