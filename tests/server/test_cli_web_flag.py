"""Web 操作台开关的 CLI 旗标与配置优先级测试。

--web/--no-web 互斥组经 argparse 解析，配置优先级为 CLI 覆盖 > env 文件 > 默认关闭，
与 watermark 旗标同构；未传旗标时 args.web 为 None 表示不覆盖任何来源；
web 开启而传输非 streamable-http 时 cli_main 启动告警的触发与抑制亦在此守护。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import seedream_mcp.server.cli as cli
from seedream_mcp.server import bootstrap as bootstrap_module
from seedream_mcp.config import SeedreamConfig, build_config_from_sources

from _lib.cli_fakes import make_run_args, stub_cli_main


def _write_env_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_web_flag_group_rejects_both_forms_together() -> None:
    """--web 与 --no-web 同给时互斥组报错退出。"""
    parser = cli.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--web", "--no-web"])


def test_web_flag_defaults_to_none() -> None:
    """未传旗标时 args.web 为 None，表示不覆盖 env 配置。"""
    parser = cli.build_arg_parser()

    args = parser.parse_args([])

    assert args.web is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [(["--web"], True), (["--no-web"], False)],
)
def test_web_flag_parses_both_forms(argv: list[str], expected: bool) -> None:
    """--web 解析为 True、--no-web 解析为 False。"""
    parser = cli.build_arg_parser()

    args = parser.parse_args(argv)

    assert args.web is expected


def test_web_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """env 文件未配置该键时 web_enabled 取字段默认值 False。"""
    monkeypatch.delenv("SEEDREAM_WEB_ENABLED", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.web_enabled is False


def test_web_enabled_via_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """env 文件配置 SEEDREAM_WEB_ENABLED=true 时 web_enabled 为 True。"""
    monkeypatch.delenv("SEEDREAM_WEB_ENABLED", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_WEB_ENABLED=true\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.web_enabled is True


def test_cli_no_web_overrides_env_file_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env 开启但 CLI 传 --no-web 时最终关闭。"""
    monkeypatch.delenv("SEEDREAM_WEB_ENABLED", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_WEB_ENABLED=true\n")

    args = make_run_args("stdio")
    args.web = False
    args.config_file = str(env_file)

    config = cli.build_config_from_args(args)

    assert config.web_enabled is False


def test_cli_web_overrides_env_file_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """env 关闭但 CLI 传 --web 时最终开启。"""
    monkeypatch.delenv("SEEDREAM_WEB_ENABLED", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_WEB_ENABLED=false\n")

    args = make_run_args("stdio")
    args.web = True
    args.config_file = str(env_file)

    config = cli.build_config_from_args(args)

    assert config.web_enabled is True


def test_cli_main_warns_when_web_enabled_with_stdio_transport(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """web 开启而传输为 stdio 时启动告警经 stderr 兜底可见。"""
    args = make_run_args("stdio")
    stub_cli_main(monkeypatch, args, SeedreamConfig(api_key="test_key", web_enabled=True))

    assert bootstrap_module.cli_main() == 0
    assert "Web 操作台仅在 streamable-http 传输下生效" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("transport", "web_enabled"),
    [("stdio", False), ("streamable-http", True)],
)
def test_cli_main_suppresses_web_warning_without_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    transport: str,
    web_enabled: bool,
) -> None:
    """web 关闭或传输为 streamable-http 时不输出 stdio 忽略告警。"""
    args = make_run_args(transport)
    stub_cli_main(monkeypatch, args, SeedreamConfig(api_key="test_key", web_enabled=web_enabled))

    assert bootstrap_module.cli_main() == 0
    assert "Web 操作台" not in capsys.readouterr().err
