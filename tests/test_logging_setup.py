"""setup_logging 的 force_standard_logging 透传测试与日志控制字符 patcher 测试。"""

import asyncio
import logging
from collections import namedtuple
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from seedream_mcp.utils.core.logs import (
    _strip_message_control_chars,
    arm_unretrieved_exception_logging,
    log_unretrieved_task_exception,
    setup_logging,
)

# 模拟 loguru record["exception"] 的 RecordException 结构（type, value, traceback）
_RecordException = namedtuple("_RecordException", "type value traceback")


class _FakeLogger:
    """替身 loguru logger，吸收 remove/add/info 调用并记录 add 与 warning 的参数。

    真实 setup_logging 会调用 logger.remove() 清空全局 loguru handler 并 logger.add()
    注册新 handler，污染跨测试的全局日志状态。测试期间以替身替换模块级 logger，使其
    不动真实全局，杜绝 handler 泄漏。
    """

    def __init__(self) -> None:
        self.add_kwargs: list[dict] = []
        self.warnings: list[str] = []

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

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message.format(*args) if args else message)


@pytest.fixture
def _isolate_loguru(monkeypatch: pytest.MonkeyPatch) -> None:
    """以替身替换 setup_logging 模块内的 loguru 全局。

    防止 remove/add 改写真实全局 handler。
    """
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


def test_setup_logging_suppresses_third_party_info_noise(
    monkeypatch: pytest.MonkeyPatch, _isolate_loguru: None
) -> None:
    """第三方噪音压制清单覆盖 httpx 的 INFO 噪音。

    每次 API 调用一条的 INFO "HTTP Request" 不再淹没业务日志。
    """
    setup_logging(log_level="INFO", enable_console=False, enable_file=False)

    for name in ("urllib3", "aiohttp", "asyncio", "httpx"):
        assert logging.getLogger(name).level == logging.WARNING


# ==================== root 已有 handler 且未强制接管时的告警 ====================


def test_setup_logging_warns_when_root_handlers_block_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """root logger 已有 handler 且未强制接管时输出 warning，提示标准库日志未被拦截。

    basicConfig 在该场景整体 no-op，标准库日志绕过 loguru 桥接与控制字符防护；
    force 语义不变，调用参数仍透传 force=False。
    """
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [logging.NullHandler()])
    captured_kwargs: dict = {}

    def fake_basic_config(*args: object, **kwargs: object) -> None:
        del args
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)
    fake = _FakeLogger()
    monkeypatch.setattr("seedream_mcp.utils.core.logs.logger", fake)

    setup_logging(
        log_level="INFO",
        enable_console=False,
        enable_file=False,
        force_standard_logging=False,
    )

    assert len(fake.warnings) == 1
    assert "未被 loguru 拦截" in fake.warnings[0]
    assert captured_kwargs["force"] is False


def test_setup_logging_no_warning_when_force_takes_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force=True 强制接管时 basicConfig 重装 root handlers，不输出告警。"""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [logging.NullHandler()])
    monkeypatch.setattr(logging, "basicConfig", lambda *a, **k: None)
    fake = _FakeLogger()
    monkeypatch.setattr("seedream_mcp.utils.core.logs.logger", fake)

    setup_logging(
        log_level="INFO",
        enable_console=False,
        enable_file=False,
        force_standard_logging=True,
    )

    assert fake.warnings == []


def test_setup_logging_no_warning_when_root_has_no_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """root 无 handler 时 basicConfig 正常安装桥接器，无需告警。"""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(logging, "basicConfig", lambda *a, **k: None)
    fake = _FakeLogger()
    monkeypatch.setattr("seedream_mcp.utils.core.logs.logger", fake)

    setup_logging(log_level="INFO", enable_console=False, enable_file=False)

    assert fake.warnings == []


# ==================== 文件日志默认路径与桥接帧定位 ====================


@pytest.fixture
def _real_file_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """在 tmp 工作目录以真实 loguru 初始化文件日志，返回默认日志文件路径。

    结束时移除全局 sink、重置全局 patcher 并恢复标准库 root handlers，
    不向后续用例泄漏全局日志状态。
    """
    monkeypatch.chdir(tmp_path)
    root_handlers = list(logging.getLogger().handlers)
    try:
        # force 强制重装 root handlers 使 InterceptHandler 生效，与生产调用参数一致；
        # 缺省 force=False 在 root 已有 handler 时不会安装桥接器
        setup_logging(
            log_level="INFO",
            enable_console=False,
            enable_file=True,
            force_standard_logging=True,
        )
        yield tmp_path / ".seedream" / "logs" / "seedream_mcp.log"
    finally:
        logger.remove()
        logger.configure(patcher=None)
        logging.getLogger().handlers = root_handlers


def test_setup_logging_default_file_lands_under_seedream_logs(
    _real_file_logging: Path,
) -> None:
    """未显式传入 log_file 时，日志文件落在进程工作目录的 .seedream/logs 下。"""
    logger.info("probe default path")

    logger.complete()

    assert _real_file_logging.is_file()
    assert "probe default path" in _real_file_logging.read_text(encoding="utf-8")


def test_intercept_handler_locates_real_caller_frame(
    _real_file_logging: Path,
) -> None:
    """标准库桥接日志的调用位置是真实调用方模块，而非 logging 内部帧。"""
    logging.getLogger("bridge.probe").warning("via stdlib bridge")

    logger.complete()

    content = _real_file_logging.read_text(encoding="utf-8")
    assert "via stdlib bridge" in content
    assert "logging:callHandlers" not in content
    assert "test_logging_setup:" in content


# ==================== 控制字符 patcher ====================


def test_patcher_strips_message_control_chars() -> None:
    """日志消息中的 CR/LF 被逐字符替换为空格，防日志注入伪造行。"""
    record: dict = {"message": "a\r\nb"}

    _strip_message_control_chars(record)

    assert record["message"] == "a  b"


def test_patcher_strips_nel_and_vertical_tab() -> None:
    """控制字符类与 errors 模块共用单一来源：NEL 与垂直制表符同样压平为空格。"""
    record: dict = {"message": "a\x85b\x0bc\x00d\x7f"}

    _strip_message_control_chars(record)

    assert record["message"] == "a b c d "


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
    """捕获 warning 调用的 loguru 替身，格式化 loguru 风格的模板参数并记录 opt 传参。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.opt_kwargs: list[dict] = []

    def opt(self, *args: Any, **kwargs: Any) -> "_CaptureLogger":
        del args
        if kwargs:
            self.opt_kwargs.append(dict(kwargs))
        return self

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append(message.format(*args))


async def test_log_unretrieved_task_exception_warns_for_failed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已完成且携带异常的 task 经回调记录 warning，异常文本与完整堆栈进入日志。"""
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
    # warning 经 opt(exception=exc) 携带完整异常堆栈，满足错误日志记录完整堆栈的规范
    assert capture.opt_kwargs == [{"exception": task.exception()}]


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


async def test_arm_unretrieved_exception_logging_dedupes_repeated_arming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重复登记同一 task 仅挂一次回调，孤儿失败只记录一条 warning。"""
    from seedream_mcp.utils.core import logs

    async def failing() -> None:
        raise RuntimeError("orphan failed")

    task = asyncio.get_running_loop().create_task(failing())
    arm_unretrieved_exception_logging(task)
    arm_unretrieved_exception_logging(task)

    capture = _CaptureLogger()
    monkeypatch.setattr(logs, "logger", capture)

    # 轮询推进事件循环至 task 完成并跑完排队的 done callback，期间不检索异常
    while not task.done():
        await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert capture.warnings == ["后台共享任务失败: orphan failed"]


async def test_arm_unretrieved_exception_logging_silent_when_task_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """登记后 task 成功完成时回调无异常可检索，不记录任何日志。"""
    from seedream_mcp.utils.core import logs

    async def succeeding() -> None:
        return None

    task = asyncio.get_running_loop().create_task(succeeding())
    arm_unretrieved_exception_logging(task)

    capture = _CaptureLogger()
    monkeypatch.setattr(logs, "logger", capture)

    while not task.done():
        await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert capture.warnings == []
