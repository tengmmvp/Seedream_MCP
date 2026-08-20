"""streamable-http 传输选项与鉴权中间件测试。

覆盖 CLI 启动守卫、Bearer/Host 校验与传输关闭行为。
"""

import asyncio
from argparse import Namespace

import pytest

import seedream_mcp.server as server
import seedream_mcp.transport as transport_module
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
    """--model 接受 seedream 5.0 别名。"""
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


@pytest.mark.parametrize("level", ["debug", "Debug", "DEBUG"])
def test_build_arg_parser_log_level_case_insensitive(level: str) -> None:
    """--log-level 经 type 预处理转大写，小写/混合大小写均接受，与 env/.env 行为一致。"""
    parser = server._build_arg_parser()
    args = parser.parse_args(["--log-level", level])

    assert args.log_level == "DEBUG"


def test_build_arg_parser_supports_tls_options() -> None:
    """--ssl-certfile 与 --ssl-keyfile 可解析，默认不豁免非 TLS。"""
    parser = server._build_arg_parser()
    args = parser.parse_args(["--ssl-certfile", "c.pem", "--ssl-keyfile", "k.pem"])

    assert args.ssl_certfile == "c.pem"
    assert args.ssl_keyfile == "k.pem"
    assert args.insecure_allow_non_tls is False


def test_tls12_ssl_context_factory_enforces_minimum_version() -> None:
    """TLS 上下文工厂复用 uvicorn 默认构造并强制最低 TLS 1.2，拒绝旧版本协商。"""
    import ssl

    from seedream_mcp.transport import _tls12_ssl_context_factory

    base = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    captured: dict[str, object] = {}

    def default_factory() -> ssl.SSLContext:
        captured["called"] = True
        return base

    result = _tls12_ssl_context_factory(None, default_factory)

    assert captured == {"called": True}
    assert result is base
    assert result.minimum_version == ssl.TLSVersion.TLSv1_2


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
def test_build_run_options_returns_transport(transport: str) -> None:
    """stdio 与 streamable-http 均解析为合法 run 选项。"""
    args = Namespace(transport=transport)

    assert server._build_run_options(args) == transport


def _make_cli_args(transport: str) -> Namespace:
    return Namespace(
        api_key=None,
        config_file=None,
        model=None,
        default_size=None,
        watermark=None,
        web=False,
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


def _stub_cli(monkeypatch: pytest.MonkeyPatch, args: Namespace, config: SeedreamConfig) -> None:
    class _FakeParser:
        def parse_args(self) -> Namespace:
            return args

    monkeypatch.setattr(server, "_build_arg_parser", lambda: _FakeParser())
    monkeypatch.setattr(server, "_build_config_from_args", lambda _args: config)
    monkeypatch.setattr(server, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(server, "_warn_remote_exposure", lambda *a, **k: None)


@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
def test_cli_main_dispatches_to_correct_runner(
    monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    """cli_main 按传输类型分发到对应 runner 并返回 0。"""
    args = _make_cli_args(transport)
    config = SeedreamConfig(api_key="test_key")
    _stub_cli(monkeypatch, args, config)
    captured: dict[str, object] = {}

    def _fake_run(*, transport: str) -> None:
        captured["stdio_transport"] = transport

    def _fake_http_run(  # type: ignore[no-untyped-def]
        host,
        port,
        auth_token,
        ssl_certfile=None,
        ssl_keyfile=None,
        stateless=False,
        web_enabled=False,
    ):
        captured["http"] = {
            "host": host,
            "port": port,
            "auth_token": auth_token,
            "web_enabled": web_enabled,
        }

    monkeypatch.setattr(server.mcp, "run", _fake_run)
    monkeypatch.setattr(server, "_run_streamable_http", _fake_http_run)

    result = server.cli_main()

    assert result == 0
    if transport == "stdio":
        assert captured == {"stdio_transport": "stdio"}
    else:
        assert captured == {
            "http": {"host": "127.0.0.1", "port": 8000, "auth_token": "", "web_enabled": False}
        }


def test_cli_main_forwards_web_enabled_to_http_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--web 经 _build_config_from_args 进 config.web_enabled 后转发到 http runner。"""
    args = _make_cli_args("streamable-http")
    args.web = True
    config = SeedreamConfig(api_key="test_key", web_enabled=True)
    _stub_cli(monkeypatch, args, config)
    captured: dict[str, object] = {}

    def _fake_http_run(  # type: ignore[no-untyped-def]
        host,
        port,
        auth_token,
        ssl_certfile=None,
        ssl_keyfile=None,
        stateless=False,
        web_enabled=False,
    ):
        captured["web_enabled"] = web_enabled

    monkeypatch.setattr(server, "_run_streamable_http", _fake_http_run)

    assert server.cli_main() == 0
    assert captured["web_enabled"] is True


def test_cli_main_refuses_non_loopback_http_without_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非回环绑定且未提供鉴权令牌时 fail-closed 拒绝启动。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = None
    _stub_cli(monkeypatch, args, SeedreamConfig(api_key="test_key"))
    monkeypatch.setattr(server, "_run_streamable_http", lambda *a, **k: None)

    assert server.cli_main() == 1


def test_cli_main_refuses_non_loopback_http_without_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非回环绑定携带令牌但无 TLS 且未显式豁免时 fail-closed 拒绝启动，防止令牌明文传输。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = "s3cret"
    args.ssl_certfile = None
    args.insecure_allow_non_tls = False
    _stub_cli(monkeypatch, args, SeedreamConfig(api_key="test_key"))
    monkeypatch.setattr(server, "_run_streamable_http", lambda *a, **k: None)

    assert server.cli_main() == 1


def test_cli_main_allows_non_loopback_http_with_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非回环绑定携带令牌与 TLS 证书时正常分发并透传 SSL 选项。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = "s3cret"
    args.ssl_certfile = "/fake/cert.pem"
    args.ssl_keyfile = "/fake/key.pem"
    _stub_cli(monkeypatch, args, SeedreamConfig(api_key="test_key"))
    captured: dict[str, object] = {}

    def _fake_http_run(  # type: ignore[no-untyped-def]
        host,
        port,
        auth_token,
        ssl_certfile=None,
        ssl_keyfile=None,
        stateless=False,
        web_enabled=False,
    ):
        captured["http"] = {
            "host": host,
            "port": port,
            "auth_token": auth_token,
            "ssl_certfile": ssl_certfile,
        }

    monkeypatch.setattr(server, "_run_streamable_http", _fake_http_run)

    assert server.cli_main() == 0
    assert captured["http"]["ssl_certfile"] == "/fake/cert.pem"


def test_cli_main_allows_non_loopback_http_with_explicit_non_tls_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非回环绑定携带令牌并显式 --insecure-allow-non-tls 时允许，适用于反代终结 TLS 场景。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = "s3cret"
    args.insecure_allow_non_tls = True
    _stub_cli(monkeypatch, args, SeedreamConfig(api_key="test_key"))
    monkeypatch.setattr(server, "_run_streamable_http", lambda *a, **k: None)

    assert server.cli_main() == 0


@pytest.mark.parametrize(
    "certfile,keyfile",
    [("c.pem", None), (None, "k.pem")],
)
def test_validate_transport_args_rejects_unpaired_tls_cert_and_key(
    certfile: str | None, keyfile: str | None
) -> None:
    """ssl_certfile 与 ssl_keyfile 必须同时提供或同时省略，仅提供其一无法建立 TLS。"""
    args = _make_cli_args("streamable-http")
    args.ssl_certfile = certfile
    args.ssl_keyfile = keyfile

    message = server._validate_transport_args(args)

    assert message is not None
    assert "--ssl-certfile 与 --ssl-keyfile 必须同时提供或同时省略" in message


def test_validate_transport_args_accepts_paired_tls_options() -> None:
    """证书与私钥成对提供或同时省略时校验通过，返回 None。"""
    paired = _make_cli_args("streamable-http")
    paired.ssl_certfile = "c.pem"
    paired.ssl_keyfile = "k.pem"
    assert server._validate_transport_args(paired) is None

    both_absent = _make_cli_args("streamable-http")
    assert server._validate_transport_args(both_absent) is None


def test_validate_transport_args_skips_stdio_transport() -> None:
    """stdio 传输不涉及 TLS 参数，参数不成对也不构成错误。"""
    args = _make_cli_args("stdio")
    args.ssl_certfile = "c.pem"
    args.ssl_keyfile = None
    assert server._validate_transport_args(args) is None


def test_cli_main_config_error_returns_exit_code_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置构建抛 SeedreamConfigError 时 cli_main 返回退出码 1。"""
    from seedream_mcp.utils.core.errors import SeedreamConfigError

    args = _make_cli_args("stdio")

    class _FakeParser:
        def parse_args(self) -> Namespace:
            return args

    def _raise_config_error(_args: Namespace) -> SeedreamConfig:
        raise SeedreamConfigError("bad config")

    monkeypatch.setattr(server, "_build_arg_parser", lambda: _FakeParser())
    monkeypatch.setattr(server, "_build_config_from_args", _raise_config_error)
    monkeypatch.setattr(server, "setup_logging", lambda *a, **k: None)

    assert server.cli_main() == 1


def test_cli_main_keyboard_interrupt_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """运行期间 KeyboardInterrupt 时 cli_main 捕获并返回退出码 0。"""
    args = _make_cli_args("stdio")
    config = SeedreamConfig(api_key="test_key")
    _stub_cli(monkeypatch, args, config)

    def _raise_interrupt(*, transport: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server.mcp, "run", _raise_interrupt)

    assert server.cli_main() == 0


def test_cli_main_runtime_exception_returns_exit_code_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行期间抛一般 Exception 时 cli_main 捕获并返回退出码 1。"""
    args = _make_cli_args("stdio")
    config = SeedreamConfig(api_key="test_key")
    _stub_cli(monkeypatch, args, config)

    def _raise_runtime(*, transport: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(server.mcp, "run", _raise_runtime)

    assert server.cli_main() == 1


async def test_bearer_auth_middleware_accepts_valid_token() -> None:
    """合法 Bearer 令牌放行进入下游应用。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer s3cret")]}
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_bearer_auth_middleware_rejects_invalid_token() -> None:
    """令牌不符的请求被 401 拒绝，不进入下游应用。"""
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
    """缺少 Authorization 头的请求被 401 拒绝。"""
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("缺少 Authorization 头不应进入下游应用")

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": []}
    await middleware(scope, None, send)

    assert sent[0]["status"] == 401


async def test_bearer_auth_middleware_accepts_case_insensitive_scheme() -> None:
    """scheme 前缀大小写不敏感：小写 bearer 形态同样放行。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": [(b"authorization", b"bearer s3cret")]}
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_bearer_auth_middleware_rejects_non_bearer_scheme() -> None:
    """非 Bearer 授权方案直接拒绝，不回退比较令牌值。"""
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("非 Bearer 方案不应进入下游应用")

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": [(b"authorization", b"Basic czNjcmV0")]}
    await middleware(scope, None, send)

    assert sent[0]["status"] == 401


async def test_bearer_auth_middleware_strips_token_whitespace() -> None:
    """令牌前后空白经 strip 后比较，携带等价令牌的请求放行。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer  s3cret ")]}
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_bearer_auth_middleware_decides_on_first_authorization_header() -> None:
    """多个 authorization 头取首个即判定：首个非 Bearer 或首个令牌不符均拒绝。"""
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("重复头场景不应进入下游应用")

    middleware = server._BearerTokenAuthMiddleware(downstream, "s3cret")
    scope = {
        "type": "http",
        "headers": [
            (b"authorization", b"Basic aaa"),
            (b"authorization", b"Bearer s3cret"),
        ],
    }
    await middleware(scope, None, send)
    assert sent[0]["status"] == 401

    sent.clear()
    scope_second = {
        "type": "http",
        "headers": [
            (b"authorization", b"Bearer wrong"),
            (b"authorization", b"Bearer s3cret"),
        ],
    }
    await middleware(scope_second, None, send)
    assert sent[0]["status"] == 401


async def test_bearer_auth_middleware_passes_lifespan_scope() -> None:
    """lifespan 类型 ASGI 消息直接透传，不校验鉴权。"""
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


# ==================== 令牌经配置注入的鉴权路径 ====================


def test_cli_main_non_loopback_auth_token_from_active_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 --auth-token、令牌经活动配置注入时非回环启动通过并透传该令牌。

    生产部署依赖环境变量在配置构建期汇入 http_auth_token 字段，鉴权不得只认
    CLI 参数。
    """
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = None
    args.insecure_allow_non_tls = True
    config = SeedreamConfig(api_key="test_key", http_auth_token="env-token")
    _stub_cli(monkeypatch, args, config)
    captured: dict[str, object] = {}

    def _fake_http_run(  # type: ignore[no-untyped-def]
        host,
        port,
        auth_token,
        ssl_certfile=None,
        ssl_keyfile=None,
        stateless=False,
        web_enabled=False,
    ):
        captured["auth_token"] = auth_token

    monkeypatch.setattr(server, "_run_streamable_http", _fake_http_run)

    assert server.cli_main() == 0
    assert captured["auth_token"] == "env-token"


def test_cli_main_cli_auth_token_overrides_config_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI 与配置同时提供令牌时 CLI 优先，与配置构建的覆盖优先级一致。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    args = _make_cli_args("streamable-http")
    args.host = "0.0.0.0"
    args.auth_token = "cli-token"
    args.insecure_allow_non_tls = True
    config = SeedreamConfig(api_key="test_key", http_auth_token="env-token")
    _stub_cli(monkeypatch, args, config)
    captured: dict[str, object] = {}

    def _fake_http_run(  # type: ignore[no-untyped-def]
        host,
        port,
        auth_token,
        ssl_certfile=None,
        ssl_keyfile=None,
        stateless=False,
        web_enabled=False,
    ):
        captured["auth_token"] = auth_token

    monkeypatch.setattr(server, "_run_streamable_http", _fake_http_run)

    assert server.cli_main() == 0
    assert captured["auth_token"] == "cli-token"


# ==================== 绑定地址同步 SDK 内层防护 ====================


def _inject_transport_config(monkeypatch: pytest.MonkeyPatch, config: SeedreamConfig) -> None:
    """向 transport 模块注入活动配置替身，隔离 _transport_security_for_host 的配置读取。"""
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)


def test_transport_security_derivation_follows_bind_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """transport_security 按绑定地址派生：非回环默认关闭 SDK Host 白名单，回环保留白名单。

    未配置允许列表时非回环绑定必须整体关闭，否则全部 /mcp 请求会被 SDK 内层
    以 421 拒绝。
    """
    _inject_transport_config(monkeypatch, SeedreamConfig(api_key="test_key"))

    non_loopback = transport_module._transport_security_for_host("0.0.0.0")
    assert non_loopback is not None
    assert non_loopback.enable_dns_rebinding_protection is False

    loopback = transport_module._transport_security_for_host("127.0.0.1")
    assert loopback is not None
    assert loopback.enable_dns_rebinding_protection is True
    assert loopback.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]


def test_transport_security_non_loopback_with_allowed_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非回环绑定配置 SEEDREAM_HTTP_ALLOWED_HOSTS 时启用 SDK Host 校验并按列表放行。"""
    _inject_transport_config(
        monkeypatch,
        SeedreamConfig(
            api_key="test_key",
            http_allowed_hosts=("mcp.example.com", "mcp.example.com:*"),
        ),
    )

    security = transport_module._transport_security_for_host("0.0.0.0")

    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["mcp.example.com", "mcp.example.com:*"]


def test_transport_security_loopback_ignores_allowed_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回环绑定不读允许列表，仍维持回环白名单，不受 SEEDREAM_HTTP_ALLOWED_HOSTS 影响。"""
    _inject_transport_config(
        monkeypatch,
        SeedreamConfig(api_key="test_key", http_allowed_hosts=("mcp.example.com",)),
    )

    security = transport_module._transport_security_for_host("127.0.0.1")

    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]


# ==================== 回环 Host 校验大小写 ====================


@pytest.mark.parametrize(
    "host",
    [
        b"127.0.0.1",
        b"127.0.0.1:8000",
        b"localhost",
        b"localhost:8000",
        b"[::1]",
        b"[::1]:8000",
    ],
)
async def test_loopback_host_guard_allows_lowercase_loopback_host(host: bytes) -> None:
    """小写回环 Host 精确匹配白名单后放行，本地访问不受影响。"""
    inner_called: list[bool] = []

    async def inner(scope, receive, send):  # type: ignore[no-untyped-def]
        inner_called.append(True)

    async def send(message):  # type: ignore[no-untyped-def]
        raise AssertionError("放行路径不应产生短路响应")

    guard = transport_module._LoopbackHostGuardMiddleware(inner)
    await guard({"type": "http", "headers": [(b"host", host)]}, None, send)

    assert inner_called == [True]


@pytest.mark.parametrize(
    "host",
    [b"LOCALHOST", b"LocalHost:8000", b"LOCALHOST:8000"],
)
async def test_loopback_host_guard_matching_is_case_sensitive(host: bytes) -> None:
    """Host 头与回环白名单为大小写敏感精确比较，大写回环 Host 被 403 拒绝。"""
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def inner(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("大写回环 Host 不应进入下游")

    guard = transport_module._LoopbackHostGuardMiddleware(inner)
    await guard({"type": "http", "headers": [(b"host", host)]}, None, send)

    assert sent[0]["status"] == 403


@pytest.mark.parametrize("host", [b"EVIL.EXAMPLE.COM", b"Evil.Example.Com:8000"])
async def test_loopback_host_guard_rejects_uppercase_external_host(host: bytes) -> None:
    """大写外部域名 Host 仍被 403 拒绝，比较大小写敏感不放宽 fail-closed 取向。"""
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    async def inner(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("外部域名 Host 不应进入下游")

    guard = transport_module._LoopbackHostGuardMiddleware(inner)
    await guard({"type": "http", "headers": [(b"host", host)]}, None, send)

    assert sent[0]["status"] == 403


# ==================== 残余任务回收 ====================


async def test_drain_pending_tasks_cancels_and_collects_pending() -> None:
    """常规路径：待处理任务被取消并在回收内退出。"""

    async def _sleeper() -> None:
        await asyncio.sleep(30)

    task = asyncio.ensure_future(_sleeper())
    await asyncio.sleep(0)

    await transport_module._drain_pending_tasks()

    assert task.cancelled()


async def test_drain_pending_tasks_gives_up_when_task_swallows_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """吞掉 CancelledError 的任务在超时后被放弃等待，回收流程不会无限挂起。"""
    monkeypatch.setattr(transport_module, "_DRAIN_PENDING_TIMEOUT_SECONDS", 0.1)
    state = {"cancels": 0}

    async def _stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancels"] += 1
                if state["cancels"] >= 2:
                    return

    task = asyncio.ensure_future(_stubborn())
    await asyncio.sleep(0)

    await asyncio.wait_for(transport_module._drain_pending_tasks(), timeout=5.0)

    assert not task.done()
    assert state["cancels"] == 1

    # 收尾：再次取消使任务退出，避免遗留 pending 任务。
    task.cancel()
    await asyncio.wait_for(task, timeout=5.0)
    assert task.done() and not task.cancelled()
