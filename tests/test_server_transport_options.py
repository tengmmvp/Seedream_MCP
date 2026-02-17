from argparse import Namespace

import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig


def test_build_arg_parser_supports_mount_path() -> None:
    parser = server._build_arg_parser()
    args = parser.parse_args(["--transport", "sse", "--mount-path", "/mcp"])

    assert args.transport == "sse"
    assert args.mount_path == "/mcp"


def test_build_run_options_only_applies_mount_path_for_sse() -> None:
    sse_args = Namespace(transport="sse", mount_path="/mcp")
    stdio_args = Namespace(transport="stdio", mount_path="/ignored")
    streamable_args = Namespace(transport="streamable-http", mount_path="/ignored")

    assert server._build_run_options(sse_args) == ("sse", "/mcp")
    assert server._build_run_options(stdio_args) == ("stdio", None)
    assert server._build_run_options(streamable_args) == ("streamable-http", None)


def test_cli_main_passes_transport_and_mount_path(monkeypatch) -> None:
    args = Namespace(
        api_key=None,
        config_file=None,
        model=None,
        default_size=None,
        watermark=None,
        log_level=None,
        base_url=None,
        transport="sse",
        mount_path="/events",
    )

    class _FakeParser:
        def parse_args(self) -> Namespace:
            return args

    config = SeedreamConfig(api_key="test_key")
    captured: dict[str, object] = {}

    def _fake_run(*, transport: str, mount_path: str | None) -> None:
        captured["transport"] = transport
        captured["mount_path"] = mount_path

    monkeypatch.setattr(server, "_build_arg_parser", lambda: _FakeParser())
    monkeypatch.setattr(server, "_build_config_from_args", lambda _args: config)
    monkeypatch.setattr(server, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(server.mcp, "run", _fake_run)

    result = server.cli_main()

    assert result == 0
    assert captured == {"transport": "sse", "mount_path": "/events"}


def test_cli_main_ignores_mount_path_for_stdio(monkeypatch) -> None:
    args = Namespace(
        api_key=None,
        config_file=None,
        model=None,
        default_size=None,
        watermark=None,
        log_level=None,
        base_url=None,
        transport="stdio",
        mount_path="/ignored",
    )

    class _FakeParser:
        def parse_args(self) -> Namespace:
            return args

    config = SeedreamConfig(api_key="test_key")
    captured: dict[str, object] = {}

    def _fake_run(*, transport: str, mount_path: str | None) -> None:
        captured["transport"] = transport
        captured["mount_path"] = mount_path

    monkeypatch.setattr(server, "_build_arg_parser", lambda: _FakeParser())
    monkeypatch.setattr(server, "_build_config_from_args", lambda _args: config)
    monkeypatch.setattr(server, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(server.mcp, "run", _fake_run)

    result = server.cli_main()

    assert result == 0
    assert captured == {"transport": "stdio", "mount_path": None}


def test_cli_main_ignores_mount_path_for_streamable_http(monkeypatch) -> None:
    args = Namespace(
        api_key=None,
        config_file=None,
        model=None,
        default_size=None,
        watermark=None,
        log_level=None,
        base_url=None,
        transport="streamable-http",
        mount_path="/ignored",
    )

    class _FakeParser:
        def parse_args(self) -> Namespace:
            return args

    config = SeedreamConfig(api_key="test_key")
    captured: dict[str, object] = {}

    def _fake_run(*, transport: str, mount_path: str | None) -> None:
        captured["transport"] = transport
        captured["mount_path"] = mount_path

    monkeypatch.setattr(server, "_build_arg_parser", lambda: _FakeParser())
    monkeypatch.setattr(server, "_build_config_from_args", lambda _args: config)
    monkeypatch.setattr(server, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(server.mcp, "run", _fake_run)

    result = server.cli_main()

    assert result == 0
    assert captured == {"transport": "streamable-http", "mount_path": None}
