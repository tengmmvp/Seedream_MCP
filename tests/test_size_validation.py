import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.errors import SeedreamConfigError
from seedream_mcp.utils.errors import SeedreamValidationError
from seedream_mcp.utils.validation import validate_size_for_model


def test_validate_size_for_model_accepts_seedream_45_pixel_size() -> None:
    assert validate_size_for_model("2560x1440", "doubao-seedream-4-5-251128") == "2560x1440"


def test_validate_size_for_model_accepts_seedream_50_3k_preset() -> None:
    assert validate_size_for_model("3K", "doubao-seedream-5-0-260128") == "3K"


def test_validate_size_for_model_normalizes_uppercase_pixel_separator() -> None:
    assert validate_size_for_model("2560X1440", "doubao-seedream-4-5-251128") == "2560x1440"


def test_validate_size_for_model_rejects_seedream_50_4k_preset() -> None:
    with pytest.raises(SeedreamValidationError, match="仅支持 2K/3K"):
        validate_size_for_model("4K", "doubao-seedream-5-0-260128")


def test_validate_size_for_model_rejects_seedream_45_small_pixel_size() -> None:
    with pytest.raises(SeedreamValidationError, match="总像素需在"):
        validate_size_for_model("1500x1500", "doubao-seedream-4-5-251128")


def test_validate_size_for_model_rejects_seedream_50_oversized_pixel_size() -> None:
    with pytest.raises(SeedreamValidationError, match="doubao-seedream-5.0 模型下"):
        validate_size_for_model("5000x2500", "doubao-seedream-5-0-260128")


def test_validate_size_for_model_rejects_invalid_pixel_format() -> None:
    with pytest.raises(SeedreamValidationError, match="图像尺寸必须为"):
        validate_size_for_model("abc", "doubao-seedream-4-5-251128")


def test_config_accepts_pixel_default_size() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-4-5-251128",
        default_size="2560x1440",
    )
    assert config.default_size == "2560x1440"


def test_config_normalizes_seedream_50_alias_on_direct_init() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5.0",
        default_size="3K",
    )

    assert config.model_id == "doubao-seedream-5-0-260128"
    assert config.default_size == "3K"


def test_config_rejects_pixel_default_size_out_of_model_range() -> None:
    with pytest.raises(SeedreamConfigError, match="default_size无效"):
        SeedreamConfig(
            api_key="test_key",
            model_id="doubao-seedream-4-5-251128",
            default_size="100x100",
        )
