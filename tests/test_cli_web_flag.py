"""Web 操作台开关的 CLI 旗标与配置优先级测试。

--web/--no-web 互斥组经 argparse 解析，配置优先级为 CLI 覆盖 > env 文件 > 默认关闭，
与 watermark 旗标同构；未传旗标时 args.web 为 None 表示不覆盖任何来源。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import seedream_mcp.server as server
from seedream_mcp.config import build_config_from_sources


def _write_env_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_web_flag_group_rejects_both_forms_together() -> None:
    """--web 与 --no-web 同给时互斥组报错退出。"""
    parser = server._build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--web", "--no-web"])


def test_web_flag_defaults_to_none() -> None:
    """未传旗标时 args.web 为 None，表示不覆盖 env 配置。"""
    parser = server._build_arg_parser()

    args = parser.parse_args([])

    assert args.web is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [(["--web"], True), (["--no-web"], False)],
)
def test_web_flag_parses_both_forms(argv: list[str], expected: bool) -> None:
    """--web 解析为 True、--no-web 解析为 False。"""
    parser = server._build_arg_parser()

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

    config = build_config_from_sources(overrides={"web": False}, env_file=str(env_file))

    assert config.web_enabled is False


def test_cli_web_overrides_env_file_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """env 关闭但 CLI 传 --web 时最终开启。"""
    monkeypatch.delenv("SEEDREAM_WEB_ENABLED", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_WEB_ENABLED=false\n")

    config = build_config_from_sources(overrides={"web": True}, env_file=str(env_file))

    assert config.web_enabled is True
