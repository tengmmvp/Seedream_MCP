import pytest

from seedream_mcp.utils.validation import validate_optimize_prompt_options, SeedreamValidationError


def test_optimize_prompt_options_45_only_standard():
    assert validate_optimize_prompt_options({"mode": "standard"}, "doubao-seedream-4-5-251128") == {"mode": "standard"}
    with pytest.raises(SeedreamValidationError):
        validate_optimize_prompt_options({"mode": "fast"}, "doubao-seedream-4-5-251128")


def test_optimize_prompt_options_40_supports_fast():
    assert validate_optimize_prompt_options({"mode": "fast"}, "doubao-seedream-4-0-250828") == {"mode": "fast"}
    assert validate_optimize_prompt_options({"mode": "standard"}, "doubao-seedream-4-0-250828") == {"mode": "standard"}

