"""validate_size_for_model 各模型尺寸规则与 Seedream 5.0 Pro 尺寸回归守护。"""

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamConfigError
from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.core.validators import validate_size_for_model


def test_validate_size_for_model_accepts_seedream_45_pixel_size() -> None:
    """4.5 模型的像素尺寸在区间内接受。"""
    assert validate_size_for_model("2560x1440", "doubao-seedream-4-5-251128") == "2560x1440"


def test_validate_size_for_model_accepts_seedream_50_3k_preset() -> None:
    """5.0 的 3K 档位接受。"""
    assert validate_size_for_model("3K", "doubao-seedream-5-0-260128") == "3K"


def test_validate_size_for_model_normalizes_uppercase_pixel_separator() -> None:
    """大写 X 分隔符归一化为小写后接受。"""
    assert validate_size_for_model("2560X1440", "doubao-seedream-4-5-251128") == "2560x1440"


def test_validate_size_for_model_accepts_seedream_50_4k_preset() -> None:
    """5.0 的 4K 档位接受。"""
    assert validate_size_for_model("4K", "doubao-seedream-5-0-260128") == "4K"


def test_validate_size_for_model_rejects_seedream_45_small_pixel_size() -> None:
    """4.5 的低于像素下限尺寸拒绝。"""
    with pytest.raises(SeedreamValidationError, match="总像素需在"):
        validate_size_for_model("1500x1500", "doubao-seedream-4-5-251128")


def test_validate_size_for_model_accepts_seedream_40_pixel_size() -> None:
    """4.0 的像素尺寸在区间内接受。"""
    assert validate_size_for_model("1280x720", "doubao-seedream-4-0-250828") == "1280x720"


def test_validate_size_for_model_rejects_seedream_40_small_pixel_size() -> None:
    """4.0 的低于像素下限尺寸拒绝。"""
    with pytest.raises(SeedreamValidationError, match="总像素需在"):
        validate_size_for_model("800x800", "doubao-seedream-4-0-250828")


def test_validate_size_for_model_rejects_seedream_50_oversized_pixel_size() -> None:
    """5.0 的超上限像素尺寸拒绝。"""
    with pytest.raises(SeedreamValidationError, match="doubao-seedream-5.0 模型下"):
        validate_size_for_model("4097x4097", "doubao-seedream-5-0-260128")


def test_validate_size_for_model_rejects_invalid_pixel_format() -> None:
    """非法尺寸格式拒绝。"""
    with pytest.raises(SeedreamValidationError, match="图像尺寸必须为"):
        validate_size_for_model("abc", "doubao-seedream-4-5-251128")


def test_validate_size_for_model_leading_zero_pixels_report_range_error() -> None:
    """前导零与零宽高输入报范围类错误而非格式类错误。"""
    # 前导零像素串按数值 1x1 进入像素区间校验。
    with pytest.raises(SeedreamValidationError, match="总像素需在"):
        validate_size_for_model("01x01", "doubao-seedream-4-5-251128")
    # 宽高为零的输入在宽高比计算前按数值拦截。
    with pytest.raises(SeedreamValidationError, match="必须为正整数"):
        validate_size_for_model("00x00", "doubao-seedream-4-5-251128")


def test_config_accepts_pixel_default_size() -> None:
    """像素形态 default_size 经配置校验接受。"""
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-4-5-251128",
        default_size="2560x1440",
    )
    assert config.default_size == "2560x1440"


def test_config_normalizes_seedream_50_alias_on_direct_init() -> None:
    """直接构造时 5.0 别名归一化为完整模型标识。"""
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5.0",
        default_size="3K",
    )

    assert config.model_id == "doubao-seedream-5-0-260128"
    assert config.default_size == "3K"


def test_config_rejects_pixel_default_size_out_of_model_range() -> None:
    """default_size 超出模型尺寸区间时配置构造拒绝。"""
    with pytest.raises(SeedreamConfigError, match="default_size无效"):
        SeedreamConfig(
            api_key="test_key",
            model_id="doubao-seedream-4-5-251128",
            default_size="100x100",
        )


# ==================== Seedream 5.0 Pro 尺寸校验 ====================


def test_validate_size_for_model_accepts_seedream_50_pro_1k_preset() -> None:
    """5.0 Pro 的 1K 档位接受。"""
    assert validate_size_for_model("1K", "doubao-seedream-5-0-pro-260628") == "1K"


def test_validate_size_for_model_accepts_seedream_50_pro_2k_preset() -> None:
    """5.0 Pro 的 2K 档位接受。"""
    assert validate_size_for_model("2K", "doubao-seedream-5-0-pro-260628") == "2K"


def test_validate_size_for_model_rejects_seedream_50_pro_3k_preset() -> None:
    """5.0 Pro 不支持 3K 档位，不得因子串匹配误判为 5.0 Lite 放行。"""
    # 关键回归：5.0 Pro 的 id 含 "doubao-seedream-5-0" 子串，误判为 5.0 Lite 时 3K 会通过；
    # 档位串接按数值序排列，1K 排在 1.5K 之前。
    with pytest.raises(SeedreamValidationError, match=r"仅支持 1K/1\.5K/2K"):
        validate_size_for_model("3K", "doubao-seedream-5-0-pro-260628")


def test_validate_size_for_model_accepts_seedream_50_pro_1_5k_preset() -> None:
    """5.0 Pro 的 1.5K 档位接受。"""
    assert validate_size_for_model("1.5K", "doubao-seedream-5-0-pro-260628") == "1.5K"


def test_validate_size_for_model_rejects_seedream_50_pro_1_5k_for_lite() -> None:
    """5.0 Lite 不支持 1.5K 档位。"""
    with pytest.raises(SeedreamValidationError, match="仅支持 2K/3K/4K"):
        validate_size_for_model("1.5K", "doubao-seedream-5-0-260128")


def test_validate_size_for_model_accepts_seedream_50_pro_upper_pixel_bound() -> None:
    """邻界像素值不超官方上限且宽高为 16 倍数时接受。"""
    # 官方像素上限 2048x2048x1.1025=4624220；邻界值 2048x2256=4620288 不超限。
    assert validate_size_for_model("2048x2256", "doubao-seedream-5-0-pro-260628") == "2048x2256"


def test_validate_size_for_model_rejects_seedream_50_pro_above_pixel_bound() -> None:
    """超出官方像素上限的尺寸拒绝。"""
    # 2080x2224=4625920 超出官方上限 4624220。
    with pytest.raises(SeedreamValidationError, match="5.0-pro 模型下"):
        validate_size_for_model("2080x2224", "doubao-seedream-5-0-pro-260628")


def test_validate_size_for_model_accepts_seedream_50_pro_pixel_size() -> None:
    """5.0 Pro 的像素尺寸在区间内接受。"""
    assert validate_size_for_model("1024x1024", "doubao-seedream-5-0-pro-260628") == "1024x1024"


def test_validate_size_for_model_rejects_seedream_50_pro_small_pixel() -> None:
    """5.0 Pro 的低于像素下限尺寸拒绝。"""
    with pytest.raises(SeedreamValidationError, match="5.0-pro 模型下"):
        validate_size_for_model("512x512", "doubao-seedream-5-0-pro-260628")


def test_validate_size_for_model_rejects_seedream_50_pro_oversized_pixel() -> None:
    """5.0 Pro 的超上限像素尺寸拒绝。"""
    with pytest.raises(SeedreamValidationError, match="5.0-pro 模型下"):
        validate_size_for_model("2048x4096", "doubao-seedream-5-0-pro-260628")


def test_validate_size_for_model_rejects_seedream_50_pro_non_multiple_of_16() -> None:
    """总像素与宽高比合规但宽高非 16 倍数时仅触发倍数约束。"""
    # 1300x732 总像素 951600 落在 [921600, 4624220] 内且宽高比合规。
    with pytest.raises(SeedreamValidationError, match="16 的倍数"):
        validate_size_for_model("1300x732", "doubao-seedream-5-0-pro-260628")


def test_validate_image_input_rejects_oversized_data_uri_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """巨型 base64 在解码前按文本长度估算拒绝，避免先解码触发内存放大。"""
    import seedream_mcp.utils.images.image_validation as image_validation_module

    monkeypatch.setattr(image_validation_module, "MAX_IMAGE_FILE_SIZE", 1024)
    huge_b64 = "A" * (1024 * 4 // 3 + 100)
    with pytest.raises(SeedreamValidationError, match="数据过大"):
        image_validation_module.validate_image_input(f"data:image/png;base64,{huge_b64}")
