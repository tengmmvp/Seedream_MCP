"""cli_main 日志初始化失败的优雅退出测试。

setup_logging 含目录创建等 I/O，在只读容器或受限账号下可能抛 OSError。cli_main
捕获该异常并以退出码 1 结束，向 stderr 输出排查指引而不裸抛堆栈。日志文件路径
的求值测试见 test_io_path_polish 的 resolve_log_file_path 用例；密钥环重绑在
日志系统之后执行、失败 ERROR 落入文件通道的顺序亦在此守护。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import seedream_mcp.resources as resources_module
from seedream_mcp import bootstrap as bootstrap_module
from seedream_mcp import config as config_module

from _log_fakes import preserved_loguru_globals


def test_cli_main_exits_gracefully_when_logging_setup_raises_oserror(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """setup_logging 抛 OSError 时返回退出码 1，并在 stderr 输出失败提示。"""
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["seedream-image-mcp"])
    # 活动配置是模块级状态，先记录原值再交由 monkeypatch 在用例结束后恢复
    monkeypatch.setattr(config_module, "_active_config", config_module._active_config)

    def _raise_oserror(*args: object, **kwargs: object) -> None:
        raise OSError("cannot create log directory")

    monkeypatch.setattr(bootstrap_module, "setup_logging", _raise_oserror)

    exit_code = bootstrap_module.cli_main()

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "日志系统初始化失败" in stderr


def test_cli_main_rebind_failure_log_reaches_file_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """密钥环重绑失败的 ERROR 落入日志文件：重绑在 setup_logging 之后执行。"""
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["seedream-image-mcp"])
    # 活动配置是模块级状态，先记录原值再交由 monkeypatch 在用例结束后恢复
    monkeypatch.setattr(config_module, "_active_config", config_module._active_config)
    # boundary 探测恒空使真实 rebind 走 ERROR 分支
    monkeypatch.setattr(resources_module, "locate_request_state_boundary", lambda: None)
    monkeypatch.setattr(resources_module.mcp, "run", lambda transport: None)

    # cli_main 内的 setup_logging 重建进程级 sink 与 root handlers，退出时恢复
    with preserved_loguru_globals():
        exit_code = bootstrap_module.cli_main()

    log_file = tmp_path / ".seedream" / "logs" / "seedream_mcp.log"
    assert exit_code == 0
    assert "requestState 密钥环重绑" in log_file.read_text(encoding="utf-8")
