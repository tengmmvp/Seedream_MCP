"""streamable-http 传输选项与鉴权中间件测试：CLI 启动守卫与 Bearer 校验。"""

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


def test_build_arg_parser_supports_auth_token() -> None:
    """--auth-token 用于 streamable-http 鉴权。"""
    parser = server._build_arg_parser()
    args = parser.parse_args(["--auth-token", "s3cret"])

    assert args.auth_token == "s3cret"


def test_build_arg_parser_supports_tls_options() -> None:
    parser = server._build_arg_parser()
    args = parser.parse_args(["--ssl-certfile", "c.pem", "--ssl-keyfile", "k.pem"])

    assert args.ssl_certfile == "c.pem"
    assert args.ssl_keyfile == "k.pem"
    assert args.insecure_allow_non_tls is False


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
        host="127.0.0.1",
        port=8000,
        stateless=False,
        auth_token=None,
        ssl_certfile=None,
        ssl_keyfile=None,
        insecure_allow_non_tls=False,
    )


def _stub_cli(monkeypatch, args: Namespace, config: SeedreamConfig) -> None:
    class _FakeParser:
        def parse_args(self) -> Namespace:
            return args

    monkeypatch.setattr(server, "_build_arg_parser", lambda: _FakeParser())
    monkeypatch.setattr(server, "_build_config_from_args", lambda _args: config)
    monkeypatch.setattr(server, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(server, "_warn_remote_exposure", lambda *a, **k: None)


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
def test_cli_main_dispatches_to_correct_runner(monkeypatch, transport: str) -> None:
    args = _make_cli_args(transport)
    config = SeedreamConfig(api_key="test_key")
    _stub_cli(monkeypatch, args, config)
    captured: dict[str, object] = {}

    def _fake_run(*, transport: str) -> None:
        captured["stdio_transport"] = transport

    def _fake_http_run(host, port, auth_token, ssl_certfile=None, ssl_keyfile=None):  # type: ignore[no-untyped-def]
        captured["http"] = {"host": host, "port": port, "auth_token": auth_token}

    monkeypatch.setattr(server.mcp, "run", _fake_run)
    monkeypatch.setattr(server, "_run_streamable_http", _fake_http_run)

    result = server.cli_main()

    assert result == 0
    if transport == "stdio":
        assert captured == {"stdio_transport": "stdio"}
    else:
        assert captured == {"http": {"host": "127.0.0.1", "port": 8000, "auth_token": ""}}


def test_cli_main_refuses_non_loopback_http_without_auth_token(monkeypatch) -> None:
    """非回环 + 无鉴权令牌 → fail-closed 拒绝启动。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = None
    _stub_cli(monkeypatch, args, SeedreamConfig(api_key="test_key"))
    monkeypatch.setattr(server, "_run_streamable_http", lambda *a, **k: None)

    assert server.cli_main() == 1


def test_cli_main_refuses_non_loopback_http_without_tls(monkeypatch) -> None:
    """非回环 + 令牌 + 无 TLS + 未显式 opt-in → fail-closed 拒绝启动，防止令牌明文传输。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = "s3cret"
    args.ssl_certfile = None
    args.insecure_allow_non_tls = False
    _stub_cli(monkeypatch, args, SeedreamConfig(api_key="test_key"))
    monkeypatch.setattr(server, "_run_streamable_http", lambda *a, **k: None)

    assert server.cli_main() == 1


def test_cli_main_allows_non_loopback_http_with_tls(monkeypatch) -> None:
    """非回环 + 令牌 + TLS 证书 → 正常分发并透传 SSL。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = "s3cret"
    args.ssl_certfile = "/fake/cert.pem"
    args.ssl_keyfile = "/fake/key.pem"
    _stub_cli(monkeypatch, args, SeedreamConfig(api_key="test_key"))
    captured: dict[str, object] = {}

    def _fake_http_run(host, port, auth_token, ssl_certfile=None, ssl_keyfile=None):  # type: ignore[no-untyped-def]
        captured["http"] = {
            "host": host,
            "port": port,
            "auth_token": auth_token,
            "ssl_certfile": ssl_certfile,
        }

    monkeypatch.setattr(server, "_run_streamable_http", _fake_http_run)

    assert server.cli_main() == 0
    assert captured["http"]["ssl_certfile"] == "/fake/cert.pem"


def test_cli_main_allows_non_loopback_http_with_explicit_non_tls_opt_in(monkeypatch) -> None:
    """非回环 + 令牌 + 显式 --insecure-allow-non-tls → 允许，适用于反代终结 TLS 场景。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = "s3cret"
    args.insecure_allow_non_tls = True
    _stub_cli(monkeypatch, args, SeedreamConfig(api_key="test_key"))
    monkeypatch.setattr(server, "_run_streamable_http", lambda *a, **k: None)

    assert server.cli_main() == 0


async def test_bearer_auth_middleware_accepts_valid_token() -> None:
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer s3cret")]}
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_bearer_auth_middleware_rejects_invalid_token() -> None:
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("无效令牌不应进入下游应用")

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer wrong")]}
    await middleware(scope, None, send)

    assert sent[0]["status"] == 401


async def test_bearer_auth_middleware_unauthorized_response_contract() -> None:
    """401 响应须含 www-authenticate 头与 invalid_token 错误体，符合 RFC 6750。"""
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("鉴权失败不应进入下游应用")

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer wrong")]}
    await middleware(scope, None, send)

    start, body_msg = sent[0], sent[1]
    assert start["type"] == "http.response.start"
    assert start["status"] == 401
    headers = dict(start["headers"])
    assert headers[b"www-authenticate"] == b'Bearer error="invalid_token"'
    assert headers[b"content-type"] == b"application/json"
    assert body_msg["type"] == "http.response.body"
    body = body_msg["body"].decode("utf-8")
    assert "invalid_token" in body


async def test_bearer_auth_middleware_rejects_missing_header() -> None:
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("缺少 Authorization 头不应进入下游应用")

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": []}
    await middleware(scope, None, send)

    assert sent[0]["status"] == 401


async def test_bearer_auth_middleware_passes_lifespan_scope() -> None:
    """lifespan 类型 ASGI 消息应直接透传，不校验鉴权。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "lifespan", "headers": []}
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_bearer_auth_middleware_rejects_websocket_scope() -> None:
    """启用鉴权时 websocket 流量应被拒绝，避免绕过 Bearer 校验。"""
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("websocket 不应进入下游")

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "websocket", "headers": []}
    await middleware(scope, None, send)

    assert sent == [{"type": "websocket.close", "code": 1008}]
