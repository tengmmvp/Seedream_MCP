import pytest

from seedream_mcp.utils.validation import validate_prompt
from seedream_mcp.utils.errors import SeedreamValidationError
from seedream_mcp.tools.core.schemas import TextToImageInput


def test_validate_prompt_chinese_limit_ok():
    text = "你" * 300
    assert validate_prompt(text) == text


def test_validate_prompt_chinese_limit_exceed():
    text = "你" * 301
    with pytest.raises(SeedreamValidationError):
        validate_prompt(text)


def test_validate_prompt_english_limit_ok():
    text = ("word " * 600).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_english_limit_exceed():
    text = ("word " * 601).strip()
    with pytest.raises(SeedreamValidationError):
        validate_prompt(text)


def test_validate_prompt_mixed_limits_ok():
    text = ("你" * 200) + " " + ("word " * 400).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_mixed_limits_exceed():
    text_cn = "你" * 301
    text_en = ("word " * 601).strip()
    with pytest.raises(SeedreamValidationError):
        validate_prompt(text_cn)
    with pytest.raises(SeedreamValidationError):
        validate_prompt(text_en)


def test_pydantic_input_accepts_english_600_words():
    prompt = ("word " * 600).strip()
    obj = TextToImageInput(prompt=prompt)
    assert obj.prompt == prompt


def test_pydantic_input_accepts_english_601_words_for_structure_validation_only():
    prompt = ("word " * 601).strip()
    obj = TextToImageInput(prompt=prompt)
    assert obj.prompt == prompt
