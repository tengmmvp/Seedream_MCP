"""模型家族解析与能力表测试，锁定 H7 数据驱动重构的中心化逻辑。

重点守护 5.0 Pro 须先于 5.0 Lite 解析的顺序，避免 Pro ID 含 "5-0" 子串被误判。
"""

from seedream_mcp.config import MODEL_ALIASES
from seedream_mcp.utils.validation import (
    MODEL_FAMILY_40,
    MODEL_FAMILY_45,
    MODEL_FAMILY_50_LITE,
    MODEL_FAMILY_50_PRO,
    MODEL_FAMILY_UNKNOWN,
    _resolve_model_family,
    get_model_capabilities,
)


def test_all_model_aliases_resolve_to_known_family() -> None:
    """所有 MODEL_ALIASES 解析出的家族必须非 unknown。

    新增模型时若遗漏在 _MODEL_FAMILY_TOKENS 补充 token，会静默返回 unknown 并放行
    全部能力，导致尺寸/tools/stream 校验跳过。此测试守护该同步点。
    """
    for alias, model_id in MODEL_ALIASES.items():
        assert _resolve_model_family(model_id) != MODEL_FAMILY_UNKNOWN, (
            f"别名 {alias} -> {model_id} 未命中任何家族 token，"
            "请在 _MODEL_FAMILY_TOKENS 补充匹配规则"
        )


def test_resolve_model_family_pro_before_lite() -> None:
    # Pro 的 Model ID 含 "doubao-seedream-5-0" 子串，必须解析为 Pro 而非 Lite
    assert _resolve_model_family("doubao-seedream-5-0-pro-260628") == MODEL_FAMILY_50_PRO
    assert _resolve_model_family("doubao-seedream-5.0-pro") == MODEL_FAMILY_50_PRO
    assert _resolve_model_family("doubao-seedream-5-0-260128") == MODEL_FAMILY_50_LITE
    assert _resolve_model_family("doubao-seedream-5.0") == MODEL_FAMILY_50_LITE
    assert _resolve_model_family("doubao-seedream-4-5-251128") == MODEL_FAMILY_45
    assert _resolve_model_family("doubao-seedream-4.0") == MODEL_FAMILY_40
    assert _resolve_model_family("ep-2024xxxxxxxx-xxxxx") == MODEL_FAMILY_UNKNOWN
    assert _resolve_model_family("") == MODEL_FAMILY_UNKNOWN


def test_get_model_capabilities_pro_profile() -> None:
    caps = get_model_capabilities("doubao-seedream-5-0-pro-260628")
    assert caps.supports_output_format is True
    assert caps.supports_tools is False
    assert caps.supports_stream is False
    assert caps.max_reference_images == 10


def test_get_model_capabilities_lite_profile() -> None:
    caps = get_model_capabilities("doubao-seedream-5-0-260128")
    assert caps.supports_output_format is True
    assert caps.supports_tools is True
    assert caps.supports_stream is True
    assert caps.max_reference_images == 14


def test_get_model_capabilities_legacy_and_unknown_default_to_permissive() -> None:
    # 4.5/4.0 不支持 output_format/tools；未知模型放行全部能力
    assert get_model_capabilities("doubao-seedream-4-5-251128").supports_output_format is False
    assert get_model_capabilities("doubao-seedream-4-0-250828").supports_output_format is False

    unknown = get_model_capabilities("ep-unknown-endpoint")
    assert unknown.supports_output_format is True
    assert unknown.supports_tools is True
    assert unknown.supports_stream is True
    assert unknown.max_reference_images == 14
