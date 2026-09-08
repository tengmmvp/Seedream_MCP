"""cli_main 日志初始化失败的优雅退出测试。

setup_logging 含目录创建等 I/O，在只读容器或受限账号下可能抛 OSError。cli_main
捕获该异常并以退出码 1 结束，向 stderr 输出排查指引而不裸抛堆栈。
"""

from __future__ import annotations

import sys

import pytest

from seedream_mcp import config as config_module
from seedream_mcp import server as server_module


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

    monkeypatch.setattr(server_module, "setup_logging", _raise_oserror)

    exit_code = server_module.cli_main()

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "日志系统初始化失败" in stderr


@pytest.mark.parametrize(
    "auto_save_base_dir,workspace_root,expected_root",
    [
        ("E:/data", None, "E:/data"),
        ("E:/data", "D:/ws", "E:/data"),
        (None, "D:/ws", "D:/ws"),
        (None, None, None),
    ],
    ids=["explicit-save-dir", "explicit-wins", "workspace-root", "fallback"],
)
def test_default_log_file_follows_data_root_declaration(
    auto_save_base_dir: str | None, workspace_root: str | None, expected_root: str | None
) -> None:
    """日志文件未显式配置时跟随数据基础目录声明，无声明时交由 setup_logging 兜底。"""
    from pathlib import Path

    config = config_module.SeedreamConfig(
        api_key="k",
        auto_save_base_dir=auto_save_base_dir,
        workspace_root=workspace_root,
    )
    result = server_module._default_log_file(config)
    if expected_root is None:
        assert result is None
    else:
        assert result == str(Path(expected_root) / ".seedream" / "logs" / "seedream_mcp.log")
