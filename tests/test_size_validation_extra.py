"""validate_size_for_model 补充覆盖：宽高比约束与各模型预设档位拒绝分支。"""

import pytest

from seedream_mcp.utils.errors import SeedreamValidationError
from seedream_mcp.utils.validation import validate_size_for_model


def test_validate_size_rejects_extreme_aspect_ratio() -> None:
    # 宽高比超 16 在像素路径被拒，适用于任意模型
    with pytest.raises(SeedreamValidationError, match="宽高比"):
        validate_size_for_model("200x10", "doubao-seedream-5-0-260128")


def test_validate_size_lite_rejects_unsupported_preset() -> None:
    with pytest.raises(SeedreamValidationError, match="5.0 模型下仅支持"):
        validate_size_for_model("1K", "doubao-seedream-5-0-260128")


def test_validate_size_45_rejects_unsupported_preset() -> None:
    with pytest.raises(SeedreamValidationError, match="4.5 模型下仅支持"):
        validate_size_for_model("3K", "doubao-seedream-4-5-251128")


def test_validate_size_40_rejects_unsupported_preset() -> None:
    with pytest.raises(SeedreamValidationError, match="4.0 模型下仅支持"):
        validate_size_for_model("3K", "doubao-seedream-4-0-250828")
