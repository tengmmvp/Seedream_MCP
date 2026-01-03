import pytest

from seedream_mcp.tools.core.schemas import SequentialGenerationInput


def test_sequential_generation_total_limit_ok():
    images = [f"https://example.com/{i}.png" for i in range(5)]
    obj = SequentialGenerationInput(prompt="test", max_images=10, image=images)
    assert obj.max_images == 10
    assert obj.image == images


def test_sequential_generation_total_limit_exceed():
    images = [f"https://example.com/{i}.png" for i in range(6)]
    with pytest.raises(ValueError):
        SequentialGenerationInput(prompt="test", max_images=10, image=images)


def test_sequential_generation_without_reference_ok():
    obj = SequentialGenerationInput(prompt="test", max_images=15)
    assert obj.image is None
