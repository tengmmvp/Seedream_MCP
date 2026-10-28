"""SequentialGenerationInput 的 max_images 与参考图数量联合上限校验。"""

from typing import Any

import pytest

from seedream_mcp.tools.schemas import SequentialGenerationInput
from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.core.validators import (
    validate_reference_image_count,
    validate_sequential_image_limit,
)


def test_sequential_generation_total_limit_ok() -> None:
    """参考图与 max_images 均在联合上限内时接受。"""
    images = [f"https://example.com/{i}.png" for i in range(5)]
    obj = SequentialGenerationInput(prompt="test", max_images=10, image=images)
    assert obj.max_images == 10
    assert obj.image == images


def test_sequential_generation_total_limit_exceed() -> None:
    """参考图数量与 max_images 之和超过总上限 15 时拒绝。"""
    images = [f"https://example.com/{i}.png" for i in range(6)]
    with pytest.raises(ValueError, match="不能超过 15"):
        SequentialGenerationInput(prompt="test", max_images=10, image=images)


def test_sequential_generation_without_reference_ok() -> None:
    """无参考图时 max_images=15 恰达上限，接受。"""
    obj = SequentialGenerationInput(prompt="test", max_images=15)
    assert obj.image is None


def test_sequential_generation_default_max_images_is_15() -> None:
    """无参考图时生效 max_images 默认 15。"""
    obj = SequentialGenerationInput(prompt="test")
    assert obj.max_images is None
    assert obj.effective_max_images == 15


def test_sequential_generation_default_max_images_with_reference_images() -> None:
    """带参考图时生效 max_images 默认按 15 减参考图数量推导。"""
    images = [f"https://example.com/{i}.png" for i in range(3)]
    obj = SequentialGenerationInput(prompt="test", image=images)
    assert obj.max_images is None
    assert obj.effective_max_images == 12
    assert obj.image == images


def test_sequential_generation_reference_images_max_14_ok() -> None:
    """参考图恰为上限 14 张时接受。"""
    images = [f"https://example.com/{i}.png" for i in range(14)]
    obj = SequentialGenerationInput(prompt="test", max_images=1, image=images)
    assert len(obj.image or []) == 14


def test_sequential_generation_derived_max_images_not_in_fields_set() -> None:
    """推导不写入 max_images 字段，显式传入与缺省经字段值本身即可区分。

    字段只承载显式输入，缺省与显式 null 恒为 None，校验器不再变异 fields_set，
    exclude_unset 序列化与审计如实反映显式输入。
    """
    images = [f"https://example.com/{i}.png" for i in range(3)]
    obj = SequentialGenerationInput(prompt="test", image=images)

    assert obj.max_images is None
    assert obj.effective_max_images == 12
    assert "max_images" not in obj.model_fields_set

    explicit = SequentialGenerationInput(prompt="test", image=images, max_images=10)
    assert explicit.max_images == 10
    assert explicit.effective_max_images == 10
    assert "max_images" in explicit.model_fields_set


@pytest.mark.parametrize(
    "extra_kwargs", [{}, {"max_images": None}], ids=["omitted", "explicit-null"]
)
def test_sequential_generation_image_assignment_rederives_max_images(
    extra_kwargs: dict[str, Any],
) -> None:
    """构造后赋值 image 重跑验证器时按新参考图数重推导，不因粘性旧值误抛。"""
    obj = SequentialGenerationInput(prompt="test", **extra_kwargs)
    assert obj.max_images is None
    assert obj.effective_max_images == 15

    obj.image = ["https://example.com/a.png"]

    assert obj.max_images is None
    assert obj.effective_max_images == 14


def test_sequential_generation_image_assignment_keeps_explicit_max_images() -> None:
    """显式传入的 max_images 在赋值 image 重跑验证器时保留，不被推导覆盖。"""
    obj = SequentialGenerationInput(prompt="test", max_images=7)

    obj.image = ["https://example.com/a.png"]

    assert obj.max_images == 7
    assert obj.effective_max_images == 7
    assert "max_images" in obj.model_fields_set


def test_sequential_generation_reference_images_exceed_14() -> None:
    """参考图超过 14 张时以中文数量文案拒绝。"""
    images = [f"https://example.com/{i}.png" for i in range(15)]
    with pytest.raises(ValueError, match="参考图片数量需在 1-14 之间"):
        SequentialGenerationInput(prompt="test", max_images=1, image=images)


def test_sequential_generation_rejects_non_list_image() -> None:
    """非字符串非列表的 image 在归一层即以中文文案拒绝。"""
    with pytest.raises(ValueError, match="image 必须是字符串或字符串列表"):
        SequentialGenerationInput.model_validate({"prompt": "test", "image": 5})


def test_sequential_generation_rejects_empty_image_list() -> None:
    """空列表 image 被数量下限以中文数量文案拒绝，不静默归一等价于缺省参考图。"""
    with pytest.raises(ValueError, match="参考图片数量需在 1-14 之间"):
        SequentialGenerationInput(prompt="test", image=[])


# 以下用例直接验证 validate_sequential_image_limit 的模型能力表驱动上限，
# 守护「参考图上限随模型变化」的数据驱动语义，防止回归为硬编码 14。

_PRO = "doubao-seedream-5.0-pro"
_LITE = "doubao-seedream-5.0-lite"


def test_validate_sequential_image_limit_pro_caps_at_10() -> None:
    """5.0 Pro 参考图上限由能力表驱动为 10，11 张须拒绝。"""
    images = [f"https://example.com/{i}.png" for i in range(11)]
    with pytest.raises(SeedreamValidationError, match="image 数量不能超过 10"):
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
    with pytest.raises(SeedreamValidationError, match="image 数量不能超过 14"):
        validate_sequential_image_limit(1, over)


def test_validate_sequential_image_limit_reference_check_matches_shared_validator() -> None:
    """组图入口的参考图超限报错与通用校验入口完全一致，二者共用同一实现。"""
    images = [f"https://example.com/{i}.png" for i in range(11)]

    with pytest.raises(SeedreamValidationError) as sequential_info:
        validate_sequential_image_limit(1, images, _PRO)
    with pytest.raises(SeedreamValidationError) as reference_info:
        validate_reference_image_count(_PRO, images)

    assert str(sequential_info.value) == str(reference_info.value)
    assert sequential_info.value.field == reference_info.value.field
    assert sequential_info.value.value == reference_info.value.value


def test_validate_reference_image_count_returns_capability_limit() -> None:
    """校验通过时返回所用上限，供调用方复用免于重复推导。"""
    images = [f"https://example.com/{i}.png" for i in range(10)]

    assert validate_reference_image_count(_PRO, images) == 10
    assert validate_reference_image_count(_LITE, images) == 14
