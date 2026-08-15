"""setup_logging 的 force_standard_logging 透传测试与日志控制字符 patcher 测试。"""

import asyncio
import logging
from collections import namedtuple
from typing import Any

import pytest

from seedream_mcp.utils.core.logs import (
    _strip_message_control_chars,
    log_unretrieved_task_exception,
    setup_logging,
)

# 模拟 loguru record["exception"] 的 RecordException 结构（type, value, traceback）
_RecordException = namedtuple("_RecordException", "type value traceback")


class _FakeLogger:
    """替身 loguru logger，吸收 remove/add/info 调用并记录 add 的关键字参数。

    真实 setup_logging 会调用 logger.remove() 清空全局 loguru handler 并 logger.add()
    注册新 handler，污染跨测试的全局日志状态。测试期间以替身替换模块级 logger，使其
    不动真实全局，杜绝 handler 泄漏。
    """

    def __init__(self) -> None:
        self.add_kwargs: list[dict] = []

    def remove(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def add(self, *args: object, **kwargs: object) -> int:
        del args
        self.add_kwargs.append(dict(kwargs))
        return 0

    def configure(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def info(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


@pytest.fixture
def _isolate_loguru(monkeypatch: pytest.MonkeyPatch) -> None:
    """以替身替换 setup_logging 模块内的 loguru 全局，防止 remove/add 改写真实全局 handler。"""
    monkeypatch.setattr("seedream_mcp.utils.core.logs.logger", _FakeLogger())


def test_setup_logging_respects_force_standard_logging_false(
    monkeypatch: pytest.MonkeyPatch, _isolate_loguru: None
) -> None:
    captured_kwargs = {}

    def fake_basic_config(*args, **kwargs) -> None:
        del args
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)

    setup_logging(
        log_level="INFO",
        enable_console=False,
        enable_file=False,
        force_standard_logging=False,
    )

    assert captured_kwargs["force"] is False


def test_setup_logging_respects_force_standard_logging_true(
    monkeypatch: pytest.MonkeyPatch, _isolate_loguru: None
) -> None:
    captured_kwargs = {}

    def fake_basic_config(*args, **kwargs) -> None:
        del args
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)

    setup_logging(
        log_level="INFO",
        enable_console=False,
        enable_file=False,
        force_standard_logging=True,
    )

    assert captured_kwargs["force"] is True


def test_console_sink_colorize_follows_tty_autodetection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """控制台 sink 的 colorize 保持 None，由 loguru 按流是否 TTY 自动决定。

    强制 True 会使重定向到文件或管道的非终端 sink 输出 ANSI 转义序列，污染采集日志。
    """
    fake = _FakeLogger()
    monkeypatch.setattr("seedream_mcp.utils.core.logs.logger", fake)

    setup_logging(log_level="INFO", enable_console=True, enable_file=False)

    assert len(fake.add_kwargs) == 1
    assert fake.add_kwargs[0]["colorize"] is None


# ==================== 控制字符 patcher ====================


def test_patcher_strips_message_control_chars() -> None:
    """日志消息中的 CR/LF 被逐字符替换为空格，防日志注入伪造行。"""
    record: dict = {"message": "a\r\nb"}

    _strip_message_control_chars(record)

    assert record["message"] == "a  b"


def test_patcher_strips_exception_message_control_chars() -> None:
    """exc_info 渲染的异常消息文本同样清洗，换行不落入日志伪造额外行。"""
    exc = ValueError("line1\nFAKE-INFO token=leaked\r\nline3")
    record: dict = {
        "message": "boom",
        "exception": _RecordException(ValueError, exc, None),
    }

    _strip_message_control_chars(record)

    rendered = str(exc)
    assert "\n" not in rendered
    assert "\r" not in rendered
    assert "FAKE-INFO" in rendered


def test_patcher_leaves_clean_exception_untouched() -> None:
    """无控制字符的异常消息保持原样，不做多余改写。"""
    exc = ValueError("clean message")
    args_before = exc.args
    record: dict = {
        "message": "boom",
        "exception": _RecordException(ValueError, exc, None),
    }

    _strip_message_control_chars(record)

    assert exc.args == args_before


def test_patcher_handles_record_without_exception() -> None:
    """exception 为 None 的常规记录正常处理，不抛错。"""
    record: dict = {"message": "plain", "exception": None}

    _strip_message_control_chars(record)

    assert record["message"] == "plain"


# ==================== log_unretrieved_task_exception ====================


class _CaptureLogger:
    """捕获 warning 调用的 loguru 替身，格式化 loguru 风格的模板参数。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append(message.format(*args))


async def test_log_unretrieved_task_exception_warns_for_failed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已完成且携带异常的 task 经回调记录 warning，异常文本进入日志。"""
    from seedream_mcp.utils.core import logs

    async def failing() -> None:
        raise RuntimeError("shared task failed")

    task = asyncio.get_running_loop().create_task(failing())
    # 轮询推进事件循环至 task 完成，期间不检索其异常以模拟无等待者场景。
    while not task.done():
        await asyncio.sleep(0)

    capture = _CaptureLogger()
    monkeypatch.setattr(logs, "logger", capture)

    log_unretrieved_task_exception(task)

    assert capture.warnings == ["后台共享任务失败: shared task failed"]


async def test_log_unretrieved_task_exception_silent_for_cancelled_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cancelled task 的回调不记录任何日志，CancelledError 不是失败。"""
    from seedream_mcp.utils.core import logs

    async def pending() -> None:
        await asyncio.sleep(3600)

    task = asyncio.get_running_loop().create_task(pending())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    capture = _CaptureLogger()
    monkeypatch.setattr(logs, "logger", capture)

    log_unretrieved_task_exception(task)

    assert capture.warnings == []
