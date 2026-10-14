"""cli_main 全链路测试共用的 CLI 参数构造与启动替身装配。

Namespace 字段集与 build_arg_parser 的产物一致，新增 CLI 参数时只改本模块。
消费方为 test_cli_web_flag、test_cli_http_security_validation 与
test_server_transport_options。
"""

from __future__ import annotations

from argparse import Namespace

import pytest

import seedream_mcp.bootstrap as bootstrap_module
import seedream_mcp.resources as resources_module
from seedream_mcp.config import SeedreamConfig


def make_run_args(transport: str) -> Namespace:
    """构造 cli_main 全链路读取的 CLI 字段，未测字段取中性值。"""
    return Namespace(
        api_key=None,
        config_file=None,
        model=None,
        default_size=None,
        watermark=None,
        web=None,
        log_level=None,
        base_url=None,
        transport=transport,
        host="127.0.0.1",
        port=8000,
        stateless=False,
        auth_token=None,
        ssl_certfile=None,
        ssl_keyfile=None,
        insecure_allow_non_tls=False,
    )


def stub_cli_main(monkeypatch: pytest.MonkeyPatch, args: Namespace, config: SeedreamConfig) -> None:
    """以替身替换 cli_main 的解析、配置构建、日志初始化、远程暴露告警与传输启动，隔离进程副作用。

    cli_main 经 set_active_config 写入的活动配置由 conftest 的配置重置基线负责还原。
    """

    class _FakeParser:
        def parse_args(self) -> Namespace:
            return args

    monkeypatch.setattr(bootstrap_module, "build_arg_parser", lambda: _FakeParser())
    monkeypatch.setattr(bootstrap_module, "build_config_from_args", lambda _args: config)
    monkeypatch.setattr(bootstrap_module, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap_module, "warn_remote_exposure", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap_module, "run_streamable_http", lambda *a, **k: None)
    monkeypatch.setattr(resources_module.mcp, "run", lambda transport=None: None)
