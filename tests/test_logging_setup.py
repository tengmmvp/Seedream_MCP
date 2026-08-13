"""setup_logging 的 force_standard_logging 透传测试。"""

import logging

from seedream_mcp.utils.logging import setup_logging


def test_setup_logging_respects_force_standard_logging_false(monkeypatch) -> None:
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


def test_setup_logging_respects_force_standard_logging_true(monkeypatch) -> None:
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
