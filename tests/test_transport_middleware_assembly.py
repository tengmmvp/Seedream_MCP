"""streamable-http 中间件装配与幂等性测试。

以镜像 Starlette user_middleware 语义的替身 app 锁定装配层次与顺序；同一 app
重复装配时检测已有中间件即整体跳过，不叠加。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest
from starlette.middleware.cors import CORSMiddleware
from starlette.types import Message, Receive, Send

from _log_fakes import capture_loguru_messages
import seedream_mcp.transport as transport_module
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.transport import (
    _AppHostGuardMiddleware,
    _BearerTokenAuthMiddleware,
    _ErrorBoundaryMiddleware,
    _HealthCheckMiddleware,
    _LimitRequestBodyMiddleware,
    _LoopbackHostGuardMiddleware,
    _WebOriginGuardMiddleware,
    _attach_streamable_http_middleware,
    _bind_address_allowlist,
    _transport_security_for_host,
    _web_app_host_allowlist,
    warn_remote_exposure,
)


@dataclass
class _MiddlewareRef:
    """替身中间件条目，暴露 cls 属性镜像 starlette.middleware.Middleware 的形态。"""

    cls: type
    kwargs: dict[str, Any]


class _FakeStarletteApp:
    """记录中间件装配的 app 替身，user_middleware 按 Starlette insert(0) 语义维护。"""

    def __init__(self) -> None:
        self.user_middleware: list[_MiddlewareRef] = []

    def add_middleware(self, middleware_class: type, **kwargs: Any) -> None:
        self.user_middleware.insert(0, _MiddlewareRef(middleware_class, dict(kwargs)))

    def attached_classes(self) -> list[type]:
        return [ref.cls for ref in self.user_middleware]

    def bearer_kwargs(self) -> dict[str, Any] | None:
        """返回 Bearer 中间件条目的装配关键字参数，未装配时为 None。"""
        for ref in self.user_middleware:
            if ref.cls is _BearerTokenAuthMiddleware:
                return ref.kwargs
        return None


@pytest.fixture
def active_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入固定活动配置，隔离装配函数对 http_max_body_size 的读取。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)


def test_attach_assembles_full_stack_for_loopback_with_token(active_config: None) -> None:
    """回环绑定且配置令牌时装配四层：Bearer、请求体上限、健康检查、Host 校验。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    # add_middleware 经 insert(0) 使后添加者居前：Host 校验最外，先于健康检查拒掉
    # rebinding 域名请求；健康检查居鉴权之外探针免令牌；Bearer 最内。
    assert app.attached_classes() == [
        _LoopbackHostGuardMiddleware,
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
        _BearerTokenAuthMiddleware,
    ]


def test_attach_skips_auth_and_host_guard_for_remote_without_token(
    active_config: None,
) -> None:
    """非回环且无令牌时仅装配请求体上限与健康检查两层。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "0.0.0.0", "")

    assert app.attached_classes() == [_HealthCheckMiddleware, _LimitRequestBodyMiddleware]


def test_attach_assembles_cors_when_origins_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置 http_allowed_origins 时装配 CORS 层，位于鉴权层之外应答预检。

    健康检查与异常边界同在 CORS 之内：探针与内层 500 的响应体经 CORS 层携带
    放行头，跨源浏览器客户端可读。
    """
    config = SeedreamConfig(
        api_key="test_key",
        http_allowed_hosts=("mcp.example.com",),
        http_allowed_origins=("https://app.example.com",),
    )
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "0.0.0.0", "secret")

    assert app.attached_classes() == [
        CORSMiddleware,
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
        _ErrorBoundaryMiddleware,
        _BearerTokenAuthMiddleware,
    ]
    cors_kwargs = next(ref.kwargs for ref in app.user_middleware if ref.cls is CORSMiddleware)
    assert cors_kwargs["allow_origins"] == ["https://app.example.com"]
    assert cors_kwargs["allow_headers"] == ["*"]
    assert cors_kwargs["expose_headers"] == ["Mcp-Session-Id"]
    assert cors_kwargs["allow_private_network"] is True


def test_attach_without_origins_omits_error_boundary(active_config: None) -> None:
    """未配置 origins 时不装配异常边界，异常交服务器层默认 500 处理。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    assert _ErrorBoundaryMiddleware not in app.attached_classes()


def test_attach_without_origins_skips_cors(active_config: None) -> None:
    """未配置 http_allowed_origins 时不装配 CORS 层。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    assert CORSMiddleware not in app.attached_classes()


def test_transport_security_passes_allowed_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置 hosts 时 allowed_origins 一并透传给 SDK 内层 Origin 校验。"""
    config = SeedreamConfig(
        api_key="test_key",
        http_allowed_hosts=("mcp.example.com",),
        http_allowed_origins=("https://app.example.com",),
    )
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)

    security = _transport_security_for_host("0.0.0.0")

    assert security.allowed_hosts == ["mcp.example.com"]
    assert security.allowed_origins == ["https://app.example.com"]


def test_transport_security_merges_origins_into_loopback_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回环绑定分支并入配置 origins，预检放行的来源在内层校验同样放行。"""
    config = SeedreamConfig(
        api_key="test_key",
        http_allowed_origins=("https://app.example.com",),
    )
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)

    security = _transport_security_for_host("127.0.0.1")

    assert "https://app.example.com" in security.allowed_origins
    assert all(
        loopback in security.allowed_origins
        for loopback in transport_module._LOOPBACK_ALLOWED_ORIGINS
    )


def test_transport_security_merges_origins_into_bind_address_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """具体地址默认分支并入配置 origins，不配 hosts 也可跨源接入。"""
    config = SeedreamConfig(
        api_key="test_key",
        http_allowed_origins=("https://app.example.com",),
    )
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)

    security = _transport_security_for_host("10.0.0.5")

    literal = "10.0.0.5"
    expected = [
        f"http://{literal}",
        f"http://{literal}:*",
        f"https://{literal}",
        f"https://{literal}:*",
        "https://app.example.com",
    ]
    assert security.allowed_origins == expected


def test_attach_explicit_max_body_size_skips_config_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式传入 max_body_size 与 allowed_origins 时不读取活动配置，取值一次复用。"""

    def _fail_read() -> SeedreamConfig:
        raise AssertionError("显式传入参数时不应读取活动配置")

    monkeypatch.setattr(transport_module, "get_active_config", _fail_read)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(
        app, "127.0.0.1", "", max_body_size=1048576, allowed_origins=()
    )

    assert app.attached_classes() == [
        _LoopbackHostGuardMiddleware,
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
    ]


def test_repeated_attach_on_same_app_does_not_stack(active_config: None) -> None:
    """同一 app 实例二次装配跳过 add_middleware，中间件栈不叠加。

    streamable_http_app 每次调用新建 app 与会话管理器，幂等守卫针对的是同一 app
    重复装配时中间件层的叠加，重复层会使每个请求被多次包覆。
    """
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")
    first_pass = app.attached_classes()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    assert app.attached_classes() == first_pass


def test_attach_passes_web_exempt_paths_when_web_enabled(active_config: None) -> None:
    """web_enabled 且配置令牌时 Bearer 装配携带 Web 静态页面豁免表。"""
    from seedream_mcp.webapp.constants import (
        WEB_EXEMPT_EXACT_PATHS,
        WEB_EXEMPT_PATH_PREFIXES,
    )

    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret", web_enabled=True)

    kwargs = app.bearer_kwargs()
    assert kwargs is not None
    assert kwargs.get("exempt_exact") == WEB_EXEMPT_EXACT_PATHS
    assert kwargs.get("exempt_prefixes") == WEB_EXEMPT_PATH_PREFIXES


def test_attach_omits_exempt_kwargs_when_web_disabled(active_config: None) -> None:
    """web 关闭时 Bearer 装配不携带任何豁免参数，全部路径仍要求令牌。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    kwargs = app.bearer_kwargs()
    assert kwargs is not None
    assert "exempt_exact" not in kwargs
    assert "exempt_prefixes" not in kwargs


def test_attach_assembles_origin_guard_for_web_without_token(active_config: None) -> None:
    """web_enabled 且无令牌时以 Origin 守卫补位 Bearer，占据鉴权层最内侧。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "", web_enabled=True)

    # 守卫与 Bearer 同槽：add_middleware 后添加者居前，守卫先于请求体上限装配，
    # 执行序依次为 LoopbackHostGuard、HealthCheck、LimitRequestBody、Guard。
    assert app.attached_classes() == [
        _LoopbackHostGuardMiddleware,
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
        _WebOriginGuardMiddleware,
    ]


def test_attach_omits_origin_guard_when_token_present(active_config: None) -> None:
    """有令牌时 Origin 守卫不装配，Bearer 已挡 drive-by，API 访问由令牌判定。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret", web_enabled=True)

    assert _WebOriginGuardMiddleware not in app.attached_classes()
    assert app.bearer_kwargs() is not None


def test_attach_passes_api_prefix_to_origin_guard(active_config: None) -> None:
    """守卫的 API 前缀取自 webapp 常量单一来源，带尾斜杠避免误守 /web/api 同名前缀。"""
    from seedream_mcp.webapp.constants import WEB_API_PREFIX

    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "", web_enabled=True)

    guard_kwargs = next(
        ref.kwargs for ref in app.user_middleware if ref.cls is _WebOriginGuardMiddleware
    )
    assert guard_kwargs.get("api_prefix") == f"{WEB_API_PREFIX}/"


# ==================== 全 app Host 守卫 ====================


async def _run_app_host_guard(
    entries: tuple[str, ...], host: bytes | None
) -> tuple[list[object], list[Message]]:
    reached: list[object] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope.get("path"))

    guard = _AppHostGuardMiddleware(downstream, allowed_hosts=entries)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    headers = [] if host is None else [(b"host", host)]
    await guard({"type": "http", "path": "/web", "headers": headers}, cast(Receive, None), send)
    return reached, sent


@pytest.mark.parametrize(
    ("entries", "host", "permitted"),
    [
        (("api.example.com",), b"api.example.com", True),
        (("api.example.com",), b"api.example.com:8000", False),
        (("api.example.com:8443",), b"api.example.com:8443", True),
        (("api.example.com:8443",), b"api.example.com:9000", False),
        (("api.example.com:*",), b"api.example.com:8000", True),
        (("api.example.com:*",), b"api.example.com", False),
        (("[::1]:*",), b"[::1]:8000", True),
        (("api.example.com",), b"evil.example.com", False),
        (("api.example.com",), None, False),
    ],
)
async def test_app_host_guard_matches_sdk_entry_semantics(
    entries: tuple[str, ...], host: bytes | None, permitted: bool
) -> None:
    """条目匹配与 SDK 同语义：裸 host 无端口、精确端口全值、通配端口任带端口。"""
    reached, sent = await _run_app_host_guard(entries, host)

    if permitted:
        assert reached == ["/web"]
        assert sent == []
    else:
        assert reached == []
        assert sent[0]["status"] == 403


def test_attach_assembles_app_host_guard_for_web_on_non_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置 hosts 时 web 守卫与 SDK 内层同语义按列表放行，回环三形态恒并入。"""
    config = SeedreamConfig(api_key="test_key", http_allowed_hosts=("proxy.example.com",))
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "10.0.0.5", "secret", web_enabled=True)

    guard = next(ref for ref in app.user_middleware if ref.cls is _AppHostGuardMiddleware)
    assert guard.kwargs["allowed_hosts"] == (
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "proxy.example.com",
    )
    assert app.attached_classes()[0] is _AppHostGuardMiddleware


def test_app_host_guard_config_replaces_derived_bind_entries() -> None:
    """配置 hosts 后绑定地址派生条目不再放行，堵明文直连地址绕过白名单。"""
    allowlist = _web_app_host_allowlist("10.0.0.5", ("proxy.example.com",))

    assert "10.0.0.5" not in allowlist and "10.0.0.5:*" not in allowlist
    assert allowlist == ("127.0.0.1:*", "localhost:*", "[::1]:*", "proxy.example.com")


def test_app_host_guard_derives_loopback_forms_for_localhost_bind() -> None:
    """localhost 绑定派生回环三形态，IP 直连回环地址的 Host 不被 web 守卫拒绝。"""
    assert _web_app_host_allowlist("localhost", ()) == (
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    )


def test_attach_omits_app_host_guard_without_derivable_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通配绑定派生不出条目且未配置 hosts 时不装配，启动告警已提示配置。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "0.0.0.0", "secret", web_enabled=True)

    assert _AppHostGuardMiddleware not in app.attached_classes()


def test_attach_omits_app_host_guard_on_loopback(active_config: None) -> None:
    """回环绑定由 LoopbackHostGuard 覆盖全 app，不叠加 Host 守卫。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret", web_enabled=True)

    assert _AppHostGuardMiddleware not in app.attached_classes()
    assert _LoopbackHostGuardMiddleware in app.attached_classes()


# ==================== 异常边界中间件 ====================


async def test_error_boundary_converts_inner_exception_to_500() -> None:
    """内层未捕获异常在响应未开始时转 500 JSON，状态与响应体跨源可读。"""

    async def explode(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = _ErrorBoundaryMiddleware(explode)
    await middleware({"type": "http", "path": "/mcp"}, cast(Receive, None), send)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 500
    assert b"internal_error" in sent[1]["body"]


async def test_error_boundary_reraises_after_response_started() -> None:
    """响应已开始后内层异常无法补发 500，重新上抛交服务器断连。"""
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial"})
        raise RuntimeError("mid-stream failure")

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = _ErrorBoundaryMiddleware(downstream)
    with pytest.raises(RuntimeError, match="mid-stream failure"):
        await middleware({"type": "http", "path": "/mcp"}, cast(Receive, None), send)

    # 已发出的部分响应原样透传，未追加 500。
    assert [message["type"] for message in sent] == [
        "http.response.start",
        "http.response.body",
    ]


async def test_error_boundary_passes_non_http_scope_through() -> None:
    """lifespan 等非 http 流量直接透传，不经异常边界包装。"""
    reached: list[object] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope.get("type"))

    middleware = _ErrorBoundaryMiddleware(downstream)
    await middleware({"type": "lifespan"}, cast(Receive, None), cast(Send, None))

    assert reached == ["lifespan"]


# ==================== 健康检查中间件 ====================


async def _run_health_check(method: str, path: str) -> tuple[list[object], list[Message]]:
    """以给定方法与路径调用健康检查中间件，返回到达下游的路径与发出的消息。"""
    reached: list[object] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope.get("path"))

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = _HealthCheckMiddleware(downstream)
    await middleware({"type": "http", "method": method, "path": path}, cast(Receive, None), send)
    return reached, sent


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_health_check_short_circuits_probe_methods(method: str) -> None:
    """GET 与 HEAD /health 均短路 200；HEAD 按探活语义返回空 body。"""
    reached, sent = await _run_health_check(method, "/health")

    assert reached == []
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 200
    body_msg = sent[1]
    assert body_msg["type"] == "http.response.body"
    if method == "GET":
        assert body_msg["body"] == b'{"status":"ok"}'
    else:
        assert body_msg["body"] == b""


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
async def test_health_check_passes_through_non_probe_methods(method: str) -> None:
    """非 GET/HEAD 的 /health 请求进入下游，由 MCP 端点按自身规则响应。"""
    reached, sent = await _run_health_check(method, "/health")

    assert reached == ["/health"]
    assert sent == []


@pytest.mark.parametrize("path", ["/healthz", "/mcp", "/web"])
async def test_health_check_only_matches_exact_health_path(path: str) -> None:
    """非 /health 路径不经探活短路，正常进入下游。"""
    reached, sent = await _run_health_check("GET", path)

    assert reached == [path]
    assert sent == []


# ==================== Origin 守卫同源判定 ====================

_API_PREFIX = "/web/api/"


def _make_guard_app() -> tuple[list[object], _WebOriginGuardMiddleware]:
    """构造记录到达路径的守卫中间件与下游替身，返回记录列表与守卫实例。"""
    reached: list[object] = []

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        reached.append(scope.get("path"))

    return reached, _WebOriginGuardMiddleware(downstream, api_prefix=_API_PREFIX)


async def _run_guard(guard: _WebOriginGuardMiddleware, scope: dict[str, Any]) -> list[Message]:
    sent: list[Message] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    await guard(scope, cast(Receive, None), send)
    return sent


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        (b"http://127.0.0.1:8000", b"127.0.0.1:8000"),
        (b"http://127.0.0.1", b"127.0.0.1"),
        (b"http://LOCALHOST:8000", b"localhost:8000"),
        (b"http://[::1]:8000", b"[::1]:8000"),
    ],
)
async def test_origin_guard_passes_same_origin(origin: bytes, host: bytes) -> None:
    """同源 Origin 放行进入下游，netloc 比对忽略大小写、IPv6 方括号形态参与比对。"""
    reached, guard = _make_guard_app()

    sent = await _run_guard(
        guard,
        {
            "type": "http",
            "path": "/web/api/config-info",
            "headers": [(b"origin", origin), (b"host", host)],
        },
    )

    assert reached == ["/web/api/config-info"]
    assert sent == []


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        (b"http://evil.example", b"127.0.0.1:8000"),
        (b"http://127.0.0.1:9999", b"127.0.0.1:8000"),
        (b"http://127.0.0.1:8000", b"127.0.0.1"),
        (b"null", b"127.0.0.1:8000"),
        (b"http://[::1", b"127.0.0.1:8000"),
        (b"http://127.0.0.1:8000", None),
    ],
)
async def test_origin_guard_rejects_cross_origin(origin: bytes, host: bytes | None) -> None:
    """跨源 Origin 一律 403：域名不同、端口不一致、null、畸形与 Host 缺失均拒绝。"""
    reached, guard = _make_guard_app()
    headers = [(b"origin", origin)] + ([] if host is None else [(b"host", host)])

    sent = await _run_guard(
        guard, {"type": "http", "path": "/web/api/config-info", "headers": headers}
    )

    assert reached == []
    assert sent[0]["status"] == 403
    body = sent[1]["body"].decode("utf-8")
    assert "invalid_origin" in body


async def test_origin_guard_passes_without_origin_header() -> None:
    """无 Origin 头放行，curl 等非浏览器客户端与本地进程不受影响。"""
    reached, guard = _make_guard_app()

    sent = await _run_guard(
        guard,
        {"type": "http", "path": "/web/api/config-info", "headers": [(b"host", b"127.0.0.1")]},
    )

    assert reached == ["/web/api/config-info"]
    assert sent == []


@pytest.mark.parametrize("path", ["/web", "/web/static/app.js", "/mcp", "/webx/api/x"])
async def test_origin_guard_only_checks_api_prefix(path: str) -> None:
    """非 API 前缀路径不校验 Origin，静态页面与 MCP 端点不受守卫影响。"""
    reached, guard = _make_guard_app()

    sent = await _run_guard(
        guard,
        {
            "type": "http",
            "path": path,
            "headers": [(b"origin", b"http://evil.example"), (b"host", b"127.0.0.1")],
        },
    )

    assert reached == [path]
    assert sent == []


async def test_origin_guard_passes_non_http_scope() -> None:
    """lifespan 等非 http 流量直接透传，不读 headers。"""
    reached, guard = _make_guard_app()

    await guard({"type": "lifespan"}, cast(Receive, None), cast(Send, None))

    assert reached == [None]


@pytest.mark.parametrize(
    ("method", "site", "reaches_downstream"),
    [
        ("GET", b"cross-site", False),
        ("GET", b"CROSS-SITE", False),
        ("GET", b"same-site", False),
        ("GET", b"SAME-SITE", False),
        ("HEAD", b"cross-site", False),
        ("HEAD", b"same-site", False),
        ("POST", b"cross-site", True),
        ("POST", b"same-site", True),
        ("GET", b"same-origin", True),
        ("GET", b"none", True),
        ("HEAD", b"same-origin", True),
        ("HEAD", b"none", True),
    ],
)
async def test_origin_guard_fetch_site_rejection_matrix(
    method: str, site: bytes, reaches_downstream: bool
) -> None:
    """GET 与 HEAD 的 Sec-Fetch-Site 为 same-site 或 cross-site 时 403，其余放行。

    same-site 覆盖同注册域兄弟子域的无 Origin 图片嵌入，HEAD 覆盖 no-cors 探测；
    same-origin 与 none 是合法流量，非 GET/HEAD 请求与无该头的旧客户端放行。
    """
    reached, guard = _make_guard_app()

    sent = await _run_guard(
        guard,
        {
            "type": "http",
            "method": method,
            "path": "/web/api/config-info",
            "headers": [(b"sec-fetch-site", site), (b"host", b"127.0.0.1")],
        },
    )

    if reaches_downstream:
        assert reached == ["/web/api/config-info"]
        assert sent == []
    else:
        assert reached == []
        assert sent[0]["status"] == 403
        assert b"cross_site_fetch" in sent[1]["body"]


# ==================== 暴露风险告警文案测试 ====================


@pytest.mark.parametrize(
    ("host", "auth_enabled", "expected_fragment", "absent_fragment"),
    [
        ("127.0.0.1", True, "已启用 Bearer 鉴权", "未启用鉴权"),
        ("127.0.0.1", False, "未启用鉴权", "已启用 Bearer 鉴权"),
        ("0.0.0.0", True, "已启用 Bearer 鉴权", "未启用鉴权"),
        ("0.0.0.0", False, "未启用鉴权", "已启用 Bearer 鉴权"),
    ],
)
def test_warn_remote_exposure_reports_truthful_auth_state(
    host: str,
    auth_enabled: bool,
    expected_fragment: str,
    absent_fragment: str,
) -> None:
    """告警文案与传入的鉴权状态一致，任何调用路径不得输出相反状态。

    非回环且未启用时若沿用已启用文案，运维会误判暴露面已受保护。告警只保留
    鉴权风险一句话，Web 防线细节由中间件装配的 INFO 承担。
    """
    records: list[str] = []
    with capture_loguru_messages(records):
        warn_remote_exposure(host, auth_enabled)

    output = "".join(records)
    assert expected_fragment in output
    assert absent_fragment not in output
    assert "Web 操作台" not in output


def test_attach_web_notice_logged_via_info_per_token_state(
    active_config: None,
) -> None:
    """Web 防线说明由中间件装配的 INFO 按令牌状态分别陈述，不进入启动警告。"""
    records: list[str] = []
    with capture_loguru_messages(records, level="INFO"):
        _attach_streamable_http_middleware(
            _FakeStarletteApp(), "127.0.0.1", "secret", web_enabled=True
        )

    token_output = "".join(records)
    assert "Web 操作台已开启：静态页面免鉴权，/web/api 接口要求 Bearer 令牌" in token_output
    assert "未配置令牌" not in token_output

    without_token_records: list[str] = []
    with capture_loguru_messages(without_token_records, level="INFO"):
        _attach_streamable_http_middleware(_FakeStarletteApp(), "127.0.0.1", "", web_enabled=True)

    without_token_output = "".join(without_token_records)
    assert "Web 操作台未配置令牌，已启用 /web/api 同源 Origin 与跨站 Sec-Fetch 校验" in (
        without_token_output
    )
    assert "要求 Bearer 令牌" not in without_token_output


def test_bind_address_allowlist_covers_host_and_origin_forms() -> None:
    """绑定地址推导的默认白名单覆盖 Host 与 Origin 头的实际可达形态。"""
    allowlist = _bind_address_allowlist("192.168.1.5")
    assert allowlist is not None
    hosts, origins = allowlist

    assert hosts == ["192.168.1.5", "192.168.1.5:*"]
    assert origins == [
        "http://192.168.1.5",
        "http://192.168.1.5:*",
        "https://192.168.1.5",
        "https://192.168.1.5:*",
    ]

    ipv6_allowlist = _bind_address_allowlist("fe80::1")
    assert ipv6_allowlist is not None
    ipv6_hosts, ipv6_origins = ipv6_allowlist
    assert ipv6_hosts == ["[fe80::1]", "[fe80::1]:*"]
    assert "http://[fe80::1]:*" in ipv6_origins


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::", "[::]"])
def test_bind_address_allowlist_rejects_wildcard_binds(wildcard: str) -> None:
    """通配绑定下实际访问地址不可预知，无法推导白名单，返回 None 保持关闭。"""
    assert _bind_address_allowlist(wildcard) is None


def test_transport_security_defaults_to_bind_address_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置允许列表时，具体地址绑定默认启用 SDK Host/Origin 校验（规范 MUST）。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)

    settings = _transport_security_for_host("192.168.1.5")

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["192.168.1.5", "192.168.1.5:*"]
    assert "http://192.168.1.5:*" in settings.allowed_origins


def test_transport_security_wildcard_bind_stays_off_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通配绑定未配置允许列表时保持校验关闭，并输出配置指引告警。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    records: list[str] = []

    with capture_loguru_messages(records):
        settings = _transport_security_for_host("0.0.0.0")

    assert settings.enable_dns_rebinding_protection is False
    output = "".join(records)
    assert "SEEDREAM_HTTP_ALLOWED_HOSTS" in output


def test_transport_security_explicit_hosts_keep_browser_403_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式配置允许列表时按列表放行且不设 Origin 白名单，维持既有取舍。"""
    config = SeedreamConfig(
        api_key="test_key", http_allowed_hosts=("mcp.example.com", "mcp.example.com:*")
    )
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)

    settings = _transport_security_for_host("0.0.0.0")

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["mcp.example.com", "mcp.example.com:*"]
    assert settings.allowed_origins == []
