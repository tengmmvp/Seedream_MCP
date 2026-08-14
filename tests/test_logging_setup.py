"""setup_logging 的 force_standard_logging 透传测试。"""

import logging

import pytest

from seedream_mcp.utils.core.logs import setup_logging


class _FakeLogger:
    """替身 loguru logger，吸收 remove/add/info 调用。

    真实 setup_logging 会调用 logger.remove() 清空全局 loguru handler 并 logger.add()
    注册新 handler，污染跨测试的全局日志状态。测试期间以替身替换模块级 logger，使其
    不动真实全局，杜绝 handler 泄漏。
    """

    def remove(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def add(self, *args: object, **kwargs: object) -> int:
        del args, kwargs
        return 0

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
