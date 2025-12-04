import pytest

from seedream_mcp.utils.validation import validate_size_for_model, SeedreamValidationError


def test_size_validation_for_45_model_rejects_1k():
    with pytest.raises(SeedreamValidationError):
        validate_size_for_model("1K", "doubao-seedream-4-5-251128")


def test_size_validation_for_40_model_accepts_1k():
    assert validate_size_for_model("1K", "doubao-seedream-4-0-250828") == "1K"

