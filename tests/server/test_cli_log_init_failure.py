"""cli_main 日志初始化失败的优雅退出测试。

setup_logging 含目录创建等 I/O，在只读容器或受限账号下可能抛 OSError。cli_main
捕获该异常并以退出码 1 结束，向 stderr 输出排查指引而不裸抛堆栈。日志文件路径
的求值测试见 test_io_path_polish 的 resolve_log_file_path 用例；密钥环重绑在
日志系统之后执行、前移漂移告警经导入期缓冲回放落入文件通道的顺序亦在此守护；
finally 清理段拦二次 Ctrl+C 的退出形态守护同在此文件。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import seedream_mcp.server.runtime as runtime_module
from seedream_mcp.server import bootstrap as bootstrap_module
from seedream_mcp.config import _builder as config_module

from _lib.log_fakes import capture_loguru_messages, preserved_loguru_globals


@pytest.fixture
def cli_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """cli_main 启动前导装配，yield 预期日志文件路径。

    注入密钥与临时工作区根、固定 argv、快照活动配置、空运行桩替换 mcp.run。
    用例体在 preserved_loguru_globals 包裹下执行。差异化补丁（含覆盖 run 桩）由用例在调用 cli_main 前施加。
    """
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["seedream-image-mcp"])
    # 活动配置是模块级状态，先记录原值再交由 monkeypatch 在用例结束后恢复
    monkeypatch.setattr(config_module, "_active_config", config_module._active_config)
    monkeypatch.setattr(runtime_module.mcp, "run", lambda transport: None)
    # cli_main 内的 setup_logging 重建进程级 sink 与 root handlers，退出时恢复
    with preserved_loguru_globals():
        yield tmp_path / ".seedream" / "logs" / "seedream_mcp.log"


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
    cli_startup: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """密钥环重绑失败的 ERROR 落入日志文件：重绑在 setup_logging 之后执行。"""
    # boundary 探测恒空使真实 rebind 走 ERROR 分支
    monkeypatch.setattr(runtime_module, "locate_request_state_boundary", lambda: None)

    exit_code = bootstrap_module.cli_main()

    assert exit_code == 0
    assert "requestState 密钥环重绑" in cli_startup.read_text(encoding="utf-8")


def test_cli_main_hoisting_drift_warning_reaches_file_channel(
    cli_startup: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前移漂移告警落入日志文件：导入期告警先进缓冲，drain 在 setup_logging 之后执行。"""
    messages: list[str] = []
    # 空链模拟 SDK 升级后的链形态漂移，cli_main 前直接驱动前移使告警进导入期缓冲；
    # 即时告警恰一条，锁定导入期可见性不因缓冲化而丢失。
    with monkeypatch.context() as drift_shape:
        drift_shape.setattr(runtime_module.mcp._lowlevel_server, "middleware", [])
        with capture_loguru_messages(messages, level="WARNING"):
            runtime_module._move_call_id_scope_outside_boundary(runtime_module.mcp)
    drift_count = sum(1 for m in messages if "调用关联作用域中间件未前移" in m)
    assert drift_count == 1

    exit_code = bootstrap_module.cli_main()

    assert exit_code == 0
    assert "调用关联作用域中间件未前移" in cli_startup.read_text(encoding="utf-8")


def test_cli_main_hoisting_drain_stays_silent_when_chain_intact(cli_startup: Path) -> None:
    """链序完好时导入期缓冲为空，启动不产生前移漂移告警。"""
    exit_code = bootstrap_module.cli_main()

    log_text = cli_startup.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "Seedream MCP 启动" in log_text
    assert "调用关联作用域中间件未前移" not in log_text


def test_cli_main_swallows_second_keyboard_interrupt_during_cleanup(
    cli_startup: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清理期间到达的二次 Ctrl+C 被 sync_cleanup 的池关闭防护拦截，在途退出码 0 不丢。"""

    def _interrupting_run(transport: object) -> None:
        raise KeyboardInterrupt

    def _interrupting_shutdown() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime_module.mcp, "run", _interrupting_run)
    monkeypatch.setattr(runtime_module, "shutdown_cpu_offload_executor", _interrupting_shutdown)

    exit_code = bootstrap_module.cli_main()

    assert exit_code == 0
    assert "关闭 CPU 卸载线程池被中断" in cli_startup.read_text(encoding="utf-8")
