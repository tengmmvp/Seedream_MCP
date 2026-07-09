from argparse import Namespace

import pytest

import seedream_mcp.server as server
from seedream_mcp.config import MODEL_ALIASES, SeedreamConfig


def test_build_arg_parser_rejects_deprecated_sse_transport() -> None:
    """SSE 传输已被弃用并移除，--transport=sse 应解析失败。"""
    parser = server._build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--transport", "sse"])


def test_build_arg_parser_no_longer_exposes_mount_path() -> None:
    """--mount-path 参数已随 SSE 传输一并移除。"""
    parser = server._build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--mount-path", "/mcp"])


def test_build_arg_parser_supports_seedream_50_model_choice() -> None:
    parser = server._build_arg_parser()
    args = parser.parse_args(["--model", "doubao-seedream-5.0"])

    assert args.model == "doubao-seedream-5.0"


def test_build_arg_parser_supports_all_model_aliases() -> None:
    """CLI --model choices 应覆盖全部 MODEL_ALIASES，避免新增模型时遗漏 choices 同步。"""
    parser = server._build_arg_parser()
    for alias in MODEL_ALIASES:
        args = parser.parse_args(["--model", alias])
        assert args.model == alias


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
def test_build_run_options_returns_transport(transport: str) -> None:
    args = Namespace(transport=transport)

    assert server._build_run_options(args) == transport


def _make_cli_args(transport: str) -> Namespace:
    return Namespace(
        api_key=None,
        config_file=None,
        model=None,
        default_size=None,
        watermark=None,
        log_level=None,
        base_url=None,
        transport=transport,
    )


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
def test_cli_main_passes_transport_to_run(monkeypatch, transport: str) -> None:
    args = _make_cli_args(transport)

    class _FakeParser:
        def parse_args(self) -> Namespace:
            return args

    config = SeedreamConfig(api_key="test_key")
    captured: dict[str, object] = {}

    def _fake_run(*, transport: str) -> None:
        captured["transport"] = transport

    monkeypatch.setattr(server, "_build_arg_parser", lambda: _FakeParser())
    monkeypatch.setattr(server, "_build_config_from_args", lambda _args: config)
    monkeypatch.setattr(server, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(server.mcp, "run", _fake_run)

    result = server.cli_main()

    assert result == 0
    assert captured == {"transport": transport}
