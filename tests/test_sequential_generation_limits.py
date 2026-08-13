"""SequentialGenerationInput 的 max_images 与参考图数量联合上限校验。"""

import pytest

from seedream_mcp.tools.core.schemas import SequentialGenerationInput
from seedream_mcp.utils.errors import SeedreamValidationError
from seedream_mcp.utils.validation import validate_sequential_image_limit


def test_sequential_generation_total_limit_ok() -> None:
    images = [f"https://example.com/{i}.png" for i in range(5)]
    obj = SequentialGenerationInput(prompt="test", max_images=10, image=images)
    assert obj.max_images == 10
    assert obj.image == images


def test_sequential_generation_total_limit_exceed() -> None:
    images = [f"https://example.com/{i}.png" for i in range(6)]
    with pytest.raises(ValueError, match="不能超过15"):
        SequentialGenerationInput(prompt="test", max_images=10, image=images)


def test_sequential_generation_without_reference_ok() -> None:
    obj = SequentialGenerationInput(prompt="test", max_images=15)
    assert obj.image is None


def test_sequential_generation_default_max_images_is_15() -> None:
    obj = SequentialGenerationInput(prompt="test")
    assert obj.max_images == 15


def test_sequential_generation_default_max_images_with_reference_images() -> None:
    images = [f"https://example.com/{i}.png" for i in range(3)]
    obj = SequentialGenerationInput(prompt="test", image=images)
    assert obj.max_images == 12
    assert obj.image == images


def test_sequential_generation_reference_images_max_14_ok() -> None:
    images = [f"https://example.com/{i}.png" for i in range(14)]
    obj = SequentialGenerationInput(prompt="test", max_images=1, image=images)
    assert len(obj.image or []) == 14


def test_sequential_generation_reference_images_exceed_14() -> None:
    images = [f"https://example.com/{i}.png" for i in range(15)]
    with pytest.raises(ValueError, match="1-14"):
        SequentialGenerationInput(prompt="test", max_images=1, image=images)


# 以下用例直接验证 validate_sequential_image_limit 的模型能力表驱动上限，
# 守护"参考图上限随模型变化"的数据驱动语义，防止回归为硬编码 14。

_PRO = "doubao-seedream-5.0-pro"
_LITE = "doubao-seedream-5.0-lite"


def test_validate_sequential_image_limit_pro_caps_at_10() -> None:
    """5.0 Pro 参考图上限由能力表驱动为 10，11 张须拒绝。"""
    images = [f"https://example.com/{i}.png" for i in range(11)]
    with pytest.raises(SeedreamValidationError, match="不能超过10"):
        validate_sequential_image_limit(1, images, _PRO)


def test_validate_sequential_image_limit_lite_allows_14() -> None:
    """5.0 Lite 参考图上限为 14，14 张须通过。"""
    images = [f"https://example.com/{i}.png" for i in range(14)]
    validate_sequential_image_limit(1, images, _LITE)


def test_validate_sequential_image_limit_default_caps_at_14() -> None:
    """model_id 缺省时按通用上限 14 校验，供 schema 层无模型上下文的粗校验。"""
    ok = [f"https://example.com/{i}.png" for i in range(14)]
    validate_sequential_image_limit(1, ok)
    over = [f"https://example.com/{i}.png" for i in range(15)]
    with pytest.raises(SeedreamValidationError, match="不能超过14"):
        validate_sequential_image_limit(1, over)
