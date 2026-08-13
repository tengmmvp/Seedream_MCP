"""validate_prompt 中英文词数建议阈值测试，超限仅警告不阻断。"""

from __future__ import annotations

from typing import Any

import pytest

import seedream_mcp.utils.validation as validation_module
from seedream_mcp.utils.validation import validate_prompt
from seedream_mcp.tools.core.schemas import TextToImageInput


class _WarningCaptureLogger:
    """替身 logger，记录 warning 调用以断言超限建议确实触发。

    loguru 不经标准库 logging 传播，caplog 无法捕获；以替身替换 validation 模块的
    logger，直接收集 warning 文案，断言警告真实发生而非静默通过。
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        del kwargs
        self.warnings.append(message.format(*args) if args else message)


@pytest.fixture
def warning_logger(monkeypatch: pytest.MonkeyPatch) -> _WarningCaptureLogger:
    """以记录型替身替换 validation.logger，返回收集到的 warning 列表。"""
    fake = _WarningCaptureLogger()
    monkeypatch.setattr(validation_module, "logger", fake)
    return fake


def test_validate_prompt_chinese_limit_ok() -> None:
    text = "你" * 300
    assert validate_prompt(text) == text


def test_validate_prompt_chinese_limit_warns_but_returns(
    warning_logger: _WarningCaptureLogger,
) -> None:
    text = "你" * 301
    # 文档为"建议"而非硬限制：超限仅记录警告，不阻断调用
    assert validate_prompt(text) == text
    # 超限须真正触发 warning，且文案携带实际中文计数
    assert len(warning_logger.warnings) == 1
    assert "301" in warning_logger.warnings[0]


def test_validate_prompt_english_limit_ok() -> None:
    text = ("word " * 600).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_english_limit_warns_but_returns(
    warning_logger: _WarningCaptureLogger,
) -> None:
    text = ("word " * 601).strip()
    assert validate_prompt(text) == text
    assert len(warning_logger.warnings) == 1
    assert "601" in warning_logger.warnings[0]


def test_validate_prompt_mixed_limits_ok() -> None:
    text = ("你" * 200) + " " + ("word " * 400).strip()
    assert validate_prompt(text) == text


def test_validate_prompt_mixed_limits_warns_but_returns(
    warning_logger: _WarningCaptureLogger,
) -> None:
    text_cn = "你" * 301
    text_en = ("word " * 601).strip()
    assert validate_prompt(text_cn) == text_cn
    assert validate_prompt(text_en) == text_en
    # 中英文分别超限各触发一次 warning
    assert len(warning_logger.warnings) == 2


def test_validate_prompt_within_limit_emits_no_warning(
    warning_logger: _WarningCaptureLogger,
) -> None:
    """未超限时不应触发任何 warning，验证 warning 不是无条件发出。"""
    validate_prompt("你" * 300)
    assert warning_logger.warnings == []


def test_pydantic_input_accepts_english_600_words() -> None:
    prompt = ("word " * 600).strip()
    obj = TextToImageInput(prompt=prompt)
    assert obj.prompt == prompt


def test_pydantic_input_accepts_english_601_words_for_structure_validation_only() -> None:
    prompt = ("word " * 601).strip()
    obj = TextToImageInput(prompt=prompt)
    assert obj.prompt == prompt
