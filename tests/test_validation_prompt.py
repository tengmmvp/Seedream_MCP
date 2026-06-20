from seedream_mcp.utils.validation import validate_prompt
from seedream_mcp.tools.core.schemas import TextToImageInput


def test_validate_prompt_chinese_limit_ok():
    text = "你" * 300
    assert validate_prompt(text) == text


def test_validate_prompt_chinese_limit_warns_but_returns():
    text = "你" * 301
    # 文档为"建议"而非硬限制：超限仅记录警告，不阻断调用
    assert validate_prompt(text) == text


def test_validate_prompt_english_limit_ok():
    text = ("word " * 600).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_english_limit_warns_but_returns():
    text = ("word " * 601).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_mixed_limits_ok():
    text = ("你" * 200) + " " + ("word " * 400).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_mixed_limits_warns_but_returns():
    text_cn = "你" * 301
    text_en = ("word " * 601).strip()
    assert validate_prompt(text_cn) == text_cn
    assert validate_prompt(text_en) == text_en


def test_pydantic_input_accepts_english_600_words():
    prompt = ("word " * 600).strip()
    obj = TextToImageInput(prompt=prompt)
    assert obj.prompt == prompt


def test_pydantic_input_accepts_english_601_words_for_structure_validation_only():
    prompt = ("word " * 601).strip()
    obj = TextToImageInput(prompt=prompt)
    assert obj.prompt == prompt
