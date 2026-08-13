"""SequentialGenerationInput 的 max_images 与参考图数量联合上限校验。"""

import pytest

from seedream_mcp.tools.core.schemas import SequentialGenerationInput


def test_sequential_generation_total_limit_ok():
    images = [f"https://example.com/{i}.png" for i in range(5)]
    obj = SequentialGenerationInput(prompt="test", max_images=10, image=images)
    assert obj.max_images == 10
    assert obj.image == images


def test_sequential_generation_total_limit_exceed():
    images = [f"https://example.com/{i}.png" for i in range(6)]
    with pytest.raises(ValueError, match="不能超过15"):
        SequentialGenerationInput(prompt="test", max_images=10, image=images)


def test_sequential_generation_without_reference_ok():
    obj = SequentialGenerationInput(prompt="test", max_images=15)
    assert obj.image is None


def test_sequential_generation_default_max_images_is_15():
    obj = SequentialGenerationInput(prompt="test")
    assert obj.max_images == 15


def test_sequential_generation_default_max_images_with_reference_images():
    images = [f"https://example.com/{i}.png" for i in range(3)]
    obj = SequentialGenerationInput(prompt="test", image=images)
    assert obj.max_images == 12
    assert obj.image == images


def test_sequential_generation_reference_images_max_14_ok():
    images = [f"https://example.com/{i}.png" for i in range(14)]
    obj = SequentialGenerationInput(prompt="test", max_images=1, image=images)
    assert len(obj.image or []) == 14


def test_sequential_generation_reference_images_exceed_14():
    images = [f"https://example.com/{i}.png" for i in range(15)]
    with pytest.raises(ValueError, match="1-14"):
        SequentialGenerationInput(prompt="test", max_images=1, image=images)
