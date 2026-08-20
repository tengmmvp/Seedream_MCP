"""validate_prompt 中英文词数建议阈值测试，超限仅警告不阻断。"""

from __future__ import annotations

from typing import Any

import pytest

import seedream_mcp.utils.core.validators as validation_module
from seedream_mcp.utils.core.validators import validate_prompt
from seedream_mcp.tools.core.schemas import TextToImageInput


class _WarningCaptureLogger:
    """替身 logger，记录 warning 调用以断言超限建议确实触发。

    loguru 不经标准库 logging 传播，caplog 无法捕获，故以替身直接收集 warning 文案。
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        del kwargs
        self.warnings.append(message.format(*args) if args else message)


@pytest.fixture
def warning_logger(monkeypatch: pytest.MonkeyPatch) -> _WarningCaptureLogger:
    """以记录型替身替换 validation.logger，返回替身供断言读取 warnings。"""
    fake = _WarningCaptureLogger()
    monkeypatch.setattr(validation_module, "logger", fake)
    return fake


def test_validate_prompt_chinese_limit_ok() -> None:
    """中文计数未超阈值时原样返回。"""
    text = "你" * 300
    assert validate_prompt(text) == text


def test_validate_prompt_chinese_limit_warns_but_returns(
    warning_logger: _WarningCaptureLogger,
) -> None:
    """中文计数超阈值仅警告不阻断，文案携带实际计数。"""
    text = "你" * 301
    # 文档为「建议」而非硬限制：超限仅记录警告，不阻断调用。
    assert validate_prompt(text) == text
    assert len(warning_logger.warnings) == 1
    assert "301" in warning_logger.warnings[0]


def test_validate_prompt_english_limit_ok() -> None:
    """英文词数未超阈值时原样返回。"""
    text = ("word " * 600).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_english_limit_warns_but_returns(
    warning_logger: _WarningCaptureLogger,
) -> None:
    """英文词数超阈值仅警告不阻断，文案携带实际词数。"""
    text = ("word " * 601).strip()
    assert validate_prompt(text) == text
    assert len(warning_logger.warnings) == 1
    assert "601" in warning_logger.warnings[0]


def test_validate_prompt_mixed_limits_ok() -> None:
    """中英文混合且均未超阈值时原样返回。"""
    text = ("你" * 200) + " " + ("word " * 400).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_mixed_limits_warns_but_returns(
    warning_logger: _WarningCaptureLogger,
) -> None:
    """中英文分别超限各触发一次警告。"""
    text_cn = "你" * 301
    text_en = ("word " * 601).strip()
    assert validate_prompt(text_cn) == text_cn
    assert validate_prompt(text_en) == text_en
    assert len(warning_logger.warnings) == 2


def test_validate_prompt_within_limit_emits_no_warning(
    warning_logger: _WarningCaptureLogger,
) -> None:
    """未超限时不应触发任何 warning，验证 warning 不是无条件发出。"""
    validate_prompt("你" * 300)
    assert warning_logger.warnings == []


def test_pydantic_input_accepts_english_600_words() -> None:
    """pydantic 输入模型接受阈值内的英文提示词。"""
    prompt = ("word " * 600).strip()
    obj = TextToImageInput(prompt=prompt)
    assert obj.prompt == prompt


def test_pydantic_input_accepts_english_601_words_for_structure_validation_only() -> None:
    """pydantic 输入模型仅做结构校验，超建议词数不拒绝。"""
    prompt = ("word " * 601).strip()
    obj = TextToImageInput(prompt=prompt)
    assert obj.prompt == prompt


def _reference_cjk_count(text: str) -> int:
    """独立基准计数：按字符直接遍历判定 CJK 区间，不依赖被测的正则实现。"""
    return sum(1 for c in text if "㐀" <= c <= "䶿" or "一" <= c <= "鿿" or "豈" <= c <= "﫿")


def test_validate_prompt_long_prompt_counts_match_reference(
    warning_logger: _WarningCaptureLogger,
) -> None:
    """100KB 级长提示词的中英文计数与独立基准一致，警告文案携带真实计数。

    长提示词计数路径是性能敏感点，任何后续重排或下沉不得改变计数结果。
    """
    long_cjk = "春" * 100_000
    long_en = ("word " * 20_001).strip()
    # validate_prompt 返回 strip 后文本，构造时先去除首尾空白保持断言可比。
    mixed = (("春天来了 " + "word " * 10) * 2_000).strip()

    for text in (long_cjk, long_en, mixed):
        assert validate_prompt(text) == text

    # 三条超限提示各触发一次警告，文案中的计数与独立基准一致。
    assert len(warning_logger.warnings) == 3
    assert f"中文{_reference_cjk_count(long_cjk)}" in warning_logger.warnings[0]
    assert f"英文{20_001}" in warning_logger.warnings[1]
    assert f"中文{_reference_cjk_count(mixed)}" in warning_logger.warnings[2]
    assert f"英文{20_000}" in warning_logger.warnings[2]


def test_validate_prompt_long_prompt_without_cjk_or_words_emits_no_warning(
    warning_logger: _WarningCaptureLogger,
) -> None:
    """超长但不含 CJK 与英文单词的提示词计数均为零，不触发警告。"""
    text = "！" * 100_000
    assert validate_prompt(text) == text
    assert warning_logger.warnings == []


def test_cjk_pattern_counts_extension_planes_and_kana() -> None:
    """扩展 B 及以后平面与假名纳入中文计数，覆盖生僻字与日文避免计数偏低。

    仅影响超限告警计数：拉丁字母、全角标点与谚文不在计数范围。
    """
    from seedream_mcp.utils.core.validators import CJK_CHAR_PATTERN

    text = "春𠀀𪚥あアｱ一㐀"
    assert CJK_CHAR_PATTERN.subn("", text)[1] == 8

    assert CJK_CHAR_PATTERN.subn("", "a！한")[1] == 0
