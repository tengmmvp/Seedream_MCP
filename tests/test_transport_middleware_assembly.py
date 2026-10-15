"""streamable-http 中间件装配与幂等性测试。

以镜像 Starlette user_middleware 语义的替身 app 锁定装配层次与顺序；同一 app
重复装配时检测已有中间件即整体跳过，不叠加。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

import pytest
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.types import Message, Receive, Send

from _log_fakes import capture_loguru_messages
import seedream_mcp.server.transport as transport_module
from seedream_mcp.config.host_syntax import bind_host_invalid_reason
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.server.transport import (
    ALWAYS_EXEMPT_EXACT_PATHS,
    _AppHostGuardMiddleware,
    _BearerTokenAuthMiddleware,
    _BODYLESS_METHODS,
    _BodyReadAdmission,
    _BodyReadPolicy,
    _ErrorBoundaryMiddleware,
    _HealthCheckMiddleware,
    _LargeBodyAdmissionMiddleware,
    _LimitRequestBodyMiddleware,
    _LoopbackHostGuardMiddleware,
    _WebOriginGuardMiddleware,
    _attach_streamable_http_middleware,
    _bind_address_allowlist,
    _is_dns_rebinding_protected_host,
    _resolve_body_read_policy,
    _transport_security_for_host,
    _web_app_host_allowlist,
    is_loopback_bind_host,
    warn_remote_exposure,
)
from seedream_mcp.utils.core.logs import _sink_filter, get_logger
from seedream_mcp.utils.core.loop_bound import loop_bound_semaphore
from seedream_mcp.webapp.constants import (
    WEB_EXEMPT_EXACT_PATHS,
    WEB_EXEMPT_PATH_PREFIXES,
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
    """回环绑定且配置令牌时装配五层：Bearer、请求体上限、健康检查、Host 校验与大请求体准入。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    # add_middleware 经 insert(0) 使后添加者居前：Host 校验最外，先于健康检查
    # 拒掉 rebinding 域名请求；健康检查居鉴权之外探针免令牌；Bearer 之内是准入闸，
    # 未鉴权请求不占准入许可。
    assert app.attached_classes() == [
        _LoopbackHostGuardMiddleware,
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
        _BearerTokenAuthMiddleware,
        _LargeBodyAdmissionMiddleware,
    ]


def test_attach_skips_auth_and_host_guard_for_remote_without_token(
    active_config: None,
) -> None:
    """非回环且无令牌时仅装配请求体上限、健康检查与大请求体准入三层。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "0.0.0.0", "")

    assert app.attached_classes() == [
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
        _LargeBodyAdmissionMiddleware,
    ]


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
        _LargeBodyAdmissionMiddleware,
    ]
    cors_kwargs = next(ref.kwargs for ref in app.user_middleware if ref.cls is CORSMiddleware)
    assert cors_kwargs["allow_origins"] == ["https://app.example.com"]
    assert cors_kwargs["allow_methods"] == ["GET", "HEAD", "POST", "DELETE", "OPTIONS"]
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


def test_transport_security_loopback_warns_hosts_config_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回环绑定下已配置的 SEEDREAM_HTTP_ALLOWED_HOSTS 不参与放行，security 告警指明该配置不生效。"""
    config = SeedreamConfig(api_key="test_key", http_allowed_hosts=("mcp.example.com",))
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    records: list[str] = []

    with capture_loguru_messages(records):
        security = _transport_security_for_host("127.0.0.1")

    assert "mcp.example.com" not in security.allowed_hosts
    output = "".join(records)
    assert "SEEDREAM_HTTP_ALLOWED_HOSTS" in output
    assert "不生效" in output
    # 告警与通配绑定告警同型携带 security 标记：复刻生产 sink 的 ERROR 级地板仍可见。
    error_floor_records: list[str] = []
    handler_id = get_logger().add(
        lambda message: error_floor_records.append(str(message)),
        level="WARNING",
        filter=_sink_filter("ERROR"),
    )
    try:
        _transport_security_for_host("127.0.0.1")
    finally:
        get_logger().remove(handler_id)
    assert "SEEDREAM_HTTP_ALLOWED_HOSTS" in "".join(error_floor_records)


@pytest.mark.parametrize(
    ("host", "http_allowed_hosts"),
    [("127.0.0.1", ()), ("10.0.0.5", ("mcp.example.com",))],
    ids=["loopback-without-hosts", "non-loopback-with-hosts"],
)
def test_transport_security_hosts_ignored_warning_stays_silent_otherwise(
    monkeypatch: pytest.MonkeyPatch, host: str, http_allowed_hosts: tuple[str, ...]
) -> None:
    """未命中组合时不输出告警：回环未配置 hosts 与非回环按列表放行均静默。"""
    config = SeedreamConfig(api_key="test_key", http_allowed_hosts=http_allowed_hosts)
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    records: list[str] = []

    with capture_loguru_messages(records):
        _transport_security_for_host(host)

    assert not any("SEEDREAM_HTTP_ALLOWED_HOSTS" in record for record in records)


def test_transport_security_loopback_allowlist_covers_default_port_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回环白名单含裸 Host 与无端口 Origin，默认端口部署的无端口头不被 421/403 误拒。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    bare_hosts = {"127.0.0.1", "localhost", "[::1]"}
    bare_origins = {
        "http://127.0.0.1",
        "http://localhost",
        "http://[::1]",
        "https://127.0.0.1",
        "https://localhost",
        "https://[::1]",
    }

    security = _transport_security_for_host("127.0.0.1")

    assert bare_hosts <= set(security.allowed_hosts)
    assert bare_origins <= set(security.allowed_origins)


def test_transport_security_bracketed_ipv6_loopback_matches_bare_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[::1] 与 ::1 绑定同走回环分支，防护口径不因方括号拼写分叉。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)

    bracketed = _transport_security_for_host("[::1]")
    bare = _transport_security_for_host("::1")

    assert bracketed.enable_dns_rebinding_protection == bare.enable_dns_rebinding_protection
    assert bracketed.allowed_hosts == bare.allowed_hosts
    assert bracketed.allowed_origins == bare.allowed_origins


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
    """取自配置的四个可选参数全部显式传入时不读取活动配置，生产调用方取值一次复用。"""

    def _fail_read() -> SeedreamConfig:
        raise AssertionError("显式传入参数时不应读取活动配置")

    monkeypatch.setattr(transport_module, "get_active_config", _fail_read)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(
        app,
        "127.0.0.1",
        "",
        max_body_size=1048576,
        allowed_origins=(),
        large_body_admission_concurrency=3,
        body_read_timeout=120,
    )

    assert app.attached_classes() == [
        _LoopbackHostGuardMiddleware,
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
        _LargeBodyAdmissionMiddleware,
    ]


def test_attach_fallback_reads_active_config_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """可选参数缺省回退只读取一次活动配置：四个可选参数共用同一配置对象。"""
    reads = {"count": 0}

    def _counting_config() -> SeedreamConfig:
        reads["count"] += 1
        return SeedreamConfig(api_key="test_key")

    monkeypatch.setattr(transport_module, "get_active_config", _counting_config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "")

    assert reads["count"] == 1
    assert app.attached_classes() == [
        _LoopbackHostGuardMiddleware,
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
        _LargeBodyAdmissionMiddleware,
    ]


def test_attach_web_host_guard_allowlist_comes_from_active_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """web 非回环装配的 Host 守卫白名单取自活动配置，其余参数显式传入不改变来源。"""
    config = SeedreamConfig(api_key="test_key", http_allowed_hosts=("proxy.example.com",))
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(
        app,
        "10.0.0.5",
        "secret",
        max_body_size=1048576,
        web_enabled=True,
        allowed_origins=(),
    )

    guard = next(ref for ref in app.user_middleware if ref.cls is _AppHostGuardMiddleware)
    assert guard.kwargs["allowed_hosts"] == (
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
        "localhost",
        "localhost:*",
        "proxy.example.com",
    )


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
    """web_enabled 且配置令牌时 Bearer 装配携带 Web 静态页面豁免表并入恒豁免常量。"""
    from seedream_mcp.server.transport import ALWAYS_EXEMPT_EXACT_PATHS
    from seedream_mcp.webapp.constants import (
        WEB_EXEMPT_EXACT_PATHS,
        WEB_EXEMPT_PATH_PREFIXES,
    )

    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret", web_enabled=True)

    kwargs = app.bearer_kwargs()
    assert kwargs is not None
    assert kwargs.get("exempt_exact") == WEB_EXEMPT_EXACT_PATHS | ALWAYS_EXEMPT_EXACT_PATHS
    assert kwargs.get("exempt_prefixes") == WEB_EXEMPT_PATH_PREFIXES


def test_attach_web_disabled_exempts_only_always_exempt_paths(active_config: None) -> None:
    """web 关闭时 Bearer 豁免表恰为恒豁免常量，Web 页面路径不进入豁免参数。"""
    from seedream_mcp.server.transport import ALWAYS_EXEMPT_EXACT_PATHS

    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    kwargs = app.bearer_kwargs()
    assert kwargs is not None
    assert kwargs.get("exempt_exact") == ALWAYS_EXEMPT_EXACT_PATHS
    assert not kwargs.get("exempt_prefixes")


def test_attach_assembles_origin_guard_for_web_without_token(active_config: None) -> None:
    """web_enabled 且无令牌时以 Origin 守卫补位 Bearer，占据鉴权层最内侧。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "", web_enabled=True)

    # 守卫与 Bearer 同槽：add_middleware 后添加者居前，守卫先于请求体上限装配，
    # 执行序依次为 LoopbackHostGuard、HealthCheck、LimitRequestBody、Guard、准入闸。
    assert app.attached_classes() == [
        _LoopbackHostGuardMiddleware,
        _HealthCheckMiddleware,
        _LimitRequestBodyMiddleware,
        _WebOriginGuardMiddleware,
        _LargeBodyAdmissionMiddleware,
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
        (("api.example.com:*",), b"api.example.com:9001:9002", False),
        (("api.example.com:*",), b"API.EXAMPLE.COM:8000", False),
        (("[::1]:*",), b"[::1]:8000", True),
        (("[::1]:*",), b"[::1]..", False),
        (("[::ffff:192.0.2.1]:*",), b"[::ffff:192.0.2.1]:8443", True),
        (("api.example.com",), b"evil.example.com", False),
        (("api.example.com",), b"API.EXAMPLE.COM", False),
        (("api.example.com",), None, False),
    ],
)
async def test_app_host_guard_entry_matching_semantics(
    entries: tuple[str, ...], host: bytes | None, permitted: bool
) -> None:
    """裸 host、精确端口与端口通配三形态匹配与 SDK 同语义；多冒号 Host 剥离末端口后不等通配基值，拒绝。"""
    reached, sent = await _run_app_host_guard(entries, host)

    if permitted:
        assert reached == ["/web"]
        assert sent == []
    else:
        assert reached == []
        assert sent[0]["status"] == 403


async def _run_app_host_guard_websocket(
    entries: tuple[str, ...], host: bytes
) -> tuple[list[object], list[Message]]:
    reached: list[object] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope.get("type"))

    guard = _AppHostGuardMiddleware(downstream, allowed_hosts=entries)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await guard(
        {"type": "websocket", "path": "/web", "headers": [(b"host", host)]},
        cast(Receive, None),
        send,
    )
    return reached, sent


async def test_app_host_guard_closes_websocket_with_rejected_host() -> None:
    """websocket 携带未放行 Host 以 1008 关闭，不进入内层应用。"""
    reached, sent = await _run_app_host_guard_websocket(("api.example.com",), b"evil.example.com")

    assert reached == []
    assert sent[0]["type"] == "websocket.close"
    assert sent[0]["code"] == 1008


async def test_app_host_guard_allows_websocket_with_permitted_host() -> None:
    """websocket 携带放行 Host 正常透传到内层应用。"""
    reached, sent = await _run_app_host_guard_websocket(("api.example.com",), b"api.example.com")

    assert reached == ["websocket"]
    assert sent == []


def test_attach_assembles_app_host_guard_for_web_on_non_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置 hosts 时 web 守卫与 SDK 内层同语义按列表放行，回环三地址的裸与通配形态恒并入。"""
    config = SeedreamConfig(api_key="test_key", http_allowed_hosts=("proxy.example.com",))
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "10.0.0.5", "secret", web_enabled=True)

    guard = next(ref for ref in app.user_middleware if ref.cls is _AppHostGuardMiddleware)
    assert guard.kwargs["allowed_hosts"] == (
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
        "localhost",
        "localhost:*",
        "proxy.example.com",
    )
    assert app.attached_classes()[0] is _AppHostGuardMiddleware


def test_app_host_guard_config_replaces_derived_bind_entries() -> None:
    """配置 hosts 后绑定地址派生条目不再放行，堵明文直连地址绕过白名单。"""
    allowlist = _web_app_host_allowlist("10.0.0.5", ("proxy.example.com",))

    assert "10.0.0.5" not in allowlist and "10.0.0.5:*" not in allowlist
    assert allowlist == (
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
        "localhost",
        "localhost:*",
        "proxy.example.com",
    )


def test_app_host_guard_derives_loopback_forms_for_localhost_bind() -> None:
    """localhost 绑定派生回环三地址的裸与通配形态，IP 直连回环地址不被 web 守卫拒绝。"""
    assert _web_app_host_allowlist("localhost", ()) == (
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
        "localhost",
        "localhost:*",
    )


def test_app_host_guard_derived_allowlist_merges_loopback_forms() -> None:
    """未配置 hosts 时具体非回环绑定的派生白名单并入回环基底，本机回环 Host 头访问不因未配置被拒。"""
    assert _web_app_host_allowlist("10.0.0.5", ()) == (
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
        "localhost",
        "localhost:*",
        "10.0.0.5",
        "10.0.0.5:*",
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
    import json

    async def explode(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = _ErrorBoundaryMiddleware(explode)
    await middleware({"type": "http", "path": "/mcp"}, cast(Receive, None), send)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 500
    payload = json.loads(sent[1]["body"])
    assert payload["error"] == "internal_error"
    assert payload["error_description"] == "Internal server error"


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
    """GET 与 HEAD /health 均短路 200；HEAD 响应头与 GET 一致，仅省略内容。"""
    reached, sent = await _run_health_check(method, "/health")

    assert reached == []
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 200
    headers = dict(sent[0]["headers"])
    assert headers[b"content-length"] == b"15"
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


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::", "::0", "0:0:0:0:0:0:0:0", "[::]"])
def test_bind_address_allowlist_rejects_wildcard_binds(wildcard: str) -> None:
    """通配绑定下实际访问地址不可预知，无法推导白名单，返回 None 保持关闭。"""
    assert _bind_address_allowlist(wildcard) is None


def test_bind_address_allowlist_equivalent_for_bracketed_and_bare_ipv6() -> None:
    """方括号与裸 IPv6 绑定派生同一白名单，剥方括号口径与配置侧同源不分叉。"""
    bracketed = _bind_address_allowlist("[::1]")
    bare = _bind_address_allowlist("::1")

    assert bracketed is not None
    assert bare is not None
    assert bracketed == bare
    assert bare[0] == ["[::1]", "[::1]:*"]
    assert "http://[::1]" in bare[1]


def test_bracket_bind_host_judgement_treats_malformed_brackets_as_plain() -> None:
    """未闭合、空方括号与带尾随内容的形态不剥离，按非回环的普通字符串参与判定。"""
    assert is_loopback_bind_host("[::1]")
    assert is_loopback_bind_host("::1")
    assert not is_loopback_bind_host("[::1")
    assert not is_loopback_bind_host("[]")
    assert not is_loopback_bind_host("[::1]x")
    assert not is_loopback_bind_host("[::1]:8000")
    assert not is_loopback_bind_host("[::1]junk]")


@pytest.mark.parametrize(
    "spelling",
    ["::01", "0:0:0:0:0:0:0:1", "127.0.0.2", "[::01]", "[127.0.0.2]", "::ffff:127.0.0.1"],
)
def test_loopback_equivalent_spellings_classify_as_loopback(spelling: str) -> None:
    """getaddrinfo 等价拼写经 IP 解析同判回环，字面量集合判定会误拒纯回环绑定的启动。"""
    assert is_loopback_bind_host(spelling)


@pytest.mark.parametrize("spelling", ["::2", "localhost"])
def test_non_loopback_spellings_stay_non_loopback(spelling: str) -> None:
    """非回环写法不因解析放宽误判，localhost 维持非回环。"""
    assert not is_loopback_bind_host(spelling)


@pytest.mark.parametrize(
    ("host", "protected"),
    [
        ("localhost", True),
        ("[localhost]", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("[::1]", True),
        ("::ffff:127.0.0.1", True),
        ("10.0.0.5", False),
        ("mcp.example.com", False),
    ],
)
def test_dns_rebinding_protection_host_classification(host: str, protected: bool) -> None:
    """解析为回环或等于 localhost 即判 DNS rebinding 防护地址，方括号形态同口径；具体非回环地址与非 IP 主机名不属其列。"""
    assert _is_dns_rebinding_protected_host(host) is protected


def test_loopback_guard_merges_equivalent_spelling_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """等价写法回环绑定把字面量并入守卫白名单，按该写法访问不被拒。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.2", "", web_enabled=False)

    guard = next(ref for ref in app.user_middleware if ref.cls is _LoopbackHostGuardMiddleware)
    assert b"127.0.0.2" in guard.kwargs["allowed_hosts"]
    assert b"127.0.0.1" in guard.kwargs["allowed_hosts"]


def test_loopback_allowlists_cover_port_wildcard_origin_forms() -> None:
    """回环 Origin 白名单按三地址派生双 scheme 端口通配形态，漏派生即误拒。"""
    expected_wildcard_origins = {
        f"{scheme}://{literal}:*"
        for literal in ("127.0.0.1", "localhost", "[::1]")
        for scheme in ("http", "https")
    }

    assert expected_wildcard_origins <= set(transport_module._LOOPBACK_ALLOWED_ORIGINS)


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
    """通配绑定未配置允许列表时保持校验关闭，告警声明防护由 Bearer 鉴权承担并指引配置。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    records: list[str] = []

    with capture_loguru_messages(records):
        settings = _transport_security_for_host("0.0.0.0")

    assert settings.enable_dns_rebinding_protection is False
    output = "".join(records)
    assert "SEEDREAM_HTTP_ALLOWED_HOSTS" in output
    assert "Bearer" in output


@pytest.mark.parametrize("wildcard", ["::0", "0:0:0:0:0:0:0:0"])
def test_transport_security_equivalent_wildcard_forms_stay_off(
    wildcard: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """::0 与全零展开形态与 0.0.0.0/:: 同判通配，不派生 Host 白名单并走关闭告警路径。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    records: list[str] = []

    with capture_loguru_messages(records):
        settings = _transport_security_for_host(wildcard)

    assert settings.enable_dns_rebinding_protection is False
    assert "SEEDREAM_HTTP_ALLOWED_HOSTS" in "".join(records)


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


@pytest.mark.parametrize(
    "bad_host",
    ["", "服务器.本地", "127.0.0.1 ", "127.0.0.1\x01"],
    ids=["empty", "non-ascii", "space", "control"],
)
def test_assembly_entries_reject_non_printable_ascii_host(
    bad_host: str, active_config: None
) -> None:
    """嵌入式宿主直调传输配置与中间件装配入口时，空串与非可打印 ASCII 绑定地址以共用谓词文案抛配置错误。"""
    from seedream_mcp.utils.core.errors import SeedreamConfigError

    reason = bind_host_invalid_reason(bad_host)
    assert reason is not None
    expected = f"streamable-http 绑定地址{reason}。"

    with pytest.raises(SeedreamConfigError, match=re.escape(expected)):
        _transport_security_for_host(bad_host)

    with pytest.raises(SeedreamConfigError, match=re.escape(expected)):
        _attach_streamable_http_middleware(_FakeStarletteApp(), bad_host, "")


# ==================== 大请求体并发准入中间件 ====================


def _admission_downstream(bodies: list[bytes]) -> Any:
    """构造读体记录型下游替身，返回以 Request 语义消费请求体的 ASGI app。"""

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del send
        bodies.append(await Request(scope, receive).body())

    return downstream


async def _silence_send(message: Message) -> None:
    del message


def _large_body_scope(
    headers: list[tuple[bytes, bytes]], path: str = "", method: str = "POST"
) -> dict[str, Any]:
    return {"type": "http", "method": method, "path": path, "headers": headers}


_LARGE_CL_HEADERS = [(b"content-length", str(5 * 1024 * 1024).encode("ascii"))]

# 与生产 64MB 上限派生结果一致的准入阈值：4MB 档。
_ADMISSION_THRESHOLD = 4 * 1024 * 1024


class _SpySemaphore:
    """包装真实信号量并记录 acquire/release 调用的测试替身，可选用事件序列记录相对顺序。"""

    def __init__(self, inner: asyncio.Semaphore, events: list[str] | None = None) -> None:
        self._inner = inner
        self._events = events
        self.acquire_calls = 0
        self.release_calls = 0

    async def acquire(self) -> None:
        self.acquire_calls += 1
        if self._events is not None:
            self._events.append("acquire")
        await self._inner.acquire()

    def release(self) -> None:
        self.release_calls += 1
        if self._events is not None:
            self._events.append("release")
        self._inner.release()


def _install_spy_gate(monkeypatch: pytest.MonkeyPatch, limit: int) -> _SpySemaphore:
    """以记录调用的信号量替身替换准入闸工厂，返回替身实例。"""
    spy = _SpySemaphore(asyncio.Semaphore(limit))
    monkeypatch.setattr(transport_module, "loop_bound_semaphore", lambda limit, *, key: spy)
    return spy


def _admission_stack(
    downstream: Any, concurrency: int, read_timeout: float, threshold: int
) -> _LimitRequestBodyMiddleware:
    """按生产装配序叠加外层读体计数与内层准入，两层经 scope 键交接策略。"""
    admission = _LargeBodyAdmissionMiddleware(downstream, concurrency, read_timeout, threshold)
    return _LimitRequestBodyMiddleware(admission, 64 * 1024 * 1024)


def _frame_recording_downstream(frames: list[Message]) -> Any:
    """构造逐帧记录型下游替身，按接收序记录每个 receive 帧直至终帧。"""

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        while True:
            message = await receive()
            frames.append(message)
            if message.get("type") != "http.request" or not message.get("more_body", False):
                break

    return downstream


async def test_large_body_admission_small_request_skips_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """小申报请求不取准入许可：读体照常受窗口监督，receive 帧原样交下游。"""

    def _forbidden(limit: int, *, key: str) -> Any:
        raise AssertionError(f"小申报请求不应取准入许可: {key}")

    monkeypatch.setattr(transport_module, "loop_bound_semaphore", _forbidden)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b'{"prompt": "a cat"}', "more_body": False}

    await middleware(
        _large_body_scope([(b"content-length", b"1024")]),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert bodies == [b'{"prompt": "a cat"}']


async def test_large_body_admission_drives_downstream_in_child_task() -> None:
    """监督请求的下游在子任务中执行：监督任务只做监督，与下游任务分离。"""
    downstream_tasks: list[asyncio.Task[None] | None] = []
    bodies: list[bytes] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del send
        downstream_tasks.append(asyncio.current_task())
        bodies.append(await Request(scope, receive).body())

    middleware = _admission_stack(downstream, 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b'{"jsonrpc": "2.0"}', "more_body": False}

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope([(b"content-length", b"18")]),
            cast(Receive, receive),
            cast(Send, _silence_send),
        )
    )
    await request

    assert downstream_tasks[0] is not None
    assert downstream_tasks[0] is not request
    assert bodies == [b'{"jsonrpc": "2.0"}']


async def test_large_body_admission_watchdog_cancel_keeps_supervisor_accounting_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """看门狗超时只取消下游子任务：监督任务正常返回、取消计数归零，408 照发许可照还。"""
    spy = _install_spy_gate(monkeypatch, 1)
    downstream_finished = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, receive, send
        try:
            # 读体停滞：唯有看门狗能收尾。
            await asyncio.Event().wait()
        finally:
            downstream_finished.set()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, None),
            cast(Send, send),
        )
    )
    await asyncio.wait_for(request, timeout=5)

    # 子任务先于监督任务落定，监督任务未被取消也无残余取消计数。
    assert downstream_finished.is_set()
    assert not request.cancelled()
    assert request.cancelling() == 0
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408
    assert spy.release_calls == 1


# 令牌部署生产豁免表的镜像：从 transport 与 webapp 常量直接求值，生产侧调整时随动。
_EXEMPT_EXACT_FOR_TEST = ALWAYS_EXEMPT_EXACT_PATHS | WEB_EXEMPT_EXACT_PATHS
_EXEMPT_PREFIXES_FOR_TEST: tuple[str, ...] = WEB_EXEMPT_PATH_PREFIXES


@pytest.mark.parametrize(
    "path",
    [
        *sorted(_EXEMPT_EXACT_FOR_TEST),
        "/.well-known/mcp/",
        *[f"{prefix}app.js" for prefix in _EXEMPT_PREFIXES_FOR_TEST],
    ],
)
async def test_large_body_admission_exempt_path_get_without_declaration_skips_permit(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """免鉴权路径均为 GET/HEAD：无申报形态经无体方法免监督直通，不取准入许可。"""

    def _forbidden(limit: int, *, key: str) -> Any:
        raise AssertionError(f"免鉴权路径的 GET 不应取准入许可: {key}")

    monkeypatch.setattr(transport_module, "loop_bound_semaphore", _forbidden)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    for method in ("GET", "HEAD"):
        await middleware(
            _large_body_scope([], path=path, method=method),
            cast(Receive, receive),
            cast(Send, _silence_send),
        )

    assert bodies == [b"", b""]


@pytest.mark.parametrize(
    "path",
    [
        *sorted(_EXEMPT_EXACT_FOR_TEST),
        "/.well-known/mcp/",
        *[f"{prefix}app.js" for prefix in _EXEMPT_PREFIXES_FOR_TEST],
    ],
)
async def test_large_body_admission_exempt_path_large_declaration_takes_permit(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """免鉴权路径不设准入豁免：大申报照常进闸取许可，谎报大请求体钉不死许可。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    await middleware(
        _large_body_scope(_LARGE_CL_HEADERS, path=path),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert spy.acquire_calls == 1
    assert spy.release_calls == 1
    assert bodies == [b""]


async def test_large_body_admission_non_exempt_path_large_declaration_still_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非豁免路径（/mcp 与 /web/api）大申报照常进闸取许可。"""
    keys: list[str] = []
    real_factory = loop_bound_semaphore

    def _recording(limit: int, *, key: str) -> Any:
        keys.append(key)
        return real_factory(limit, key=key)

    monkeypatch.setattr(transport_module, "loop_bound_semaphore", _recording)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"payload", "more_body": False}

    for path in ("/mcp", "/web/api/generate/text-to-image"):
        await middleware(
            _large_body_scope(_LARGE_CL_HEADERS, path=path),
            cast(Receive, receive),
            cast(Send, _silence_send),
        )

    assert keys == ["large_body_admission", "large_body_admission"]
    assert bodies == [b"payload", b"payload"]


@pytest.mark.parametrize(
    "headers",
    [[], [(b"content-length", b"abc")], [(b"transfer-encoding", b"chunked")]],
    ids=["missing", "invalid", "chunked"],
)
async def test_large_body_admission_undeclared_small_body_skips_permit(
    monkeypatch: pytest.MonkeyPatch, headers: list[tuple[bytes, bytes]]
) -> None:
    """申报缺失或非法的小请求体经惰性闸直通：未越阈不取许可，无申报不得绕过计数。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"chunked-body", "more_body": False}

    await middleware(_large_body_scope(headers), cast(Receive, receive), cast(Send, _silence_send))

    assert spy.acquire_calls == 0
    assert bodies == [b"chunked-body"]


async def test_large_body_admission_small_chunked_streams_frames_without_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """小 chunked 请求逐帧直通下游：tee 只计数不缓冲，零许可占用。"""
    spy = _install_spy_gate(monkeypatch, 1)
    forwarded_frames: list[Message] = []
    middleware = _admission_stack(
        _frame_recording_downstream(forwarded_frames), 1, 5, _ADMISSION_THRESHOLD
    )
    frames: list[Message] = [
        {"type": "http.request", "body": b"a" * 256, "more_body": True},
        {"type": "http.request", "body": b"b" * 256, "more_body": True},
        {"type": "http.request", "body": b"c" * 256, "more_body": True},
        {"type": "http.request", "body": b"d" * 256, "more_body": False},
    ]

    async def receive() -> Message:
        return frames.pop(0)

    await middleware(
        _large_body_scope([(b"transfer-encoding", b"chunked")]),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert forwarded_frames == [
        {"type": "http.request", "body": b"a" * 256, "more_body": True},
        {"type": "http.request", "body": b"b" * 256, "more_body": True},
        {"type": "http.request", "body": b"c" * 256, "more_body": True},
        {"type": "http.request", "body": b"d" * 256, "more_body": False},
    ]
    assert spy.acquire_calls == 0


async def test_large_body_admission_large_chunked_acquires_permit_after_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chunked 大行累计越过阈值才取许可，许可取后下游仍收到完整分帧流。"""
    events: list[str] = []
    spy = _SpySemaphore(asyncio.Semaphore(1), events)
    monkeypatch.setattr(transport_module, "loop_bound_semaphore", lambda limit, *, key: spy)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)
    # 首帧 3MB 未越阈直通，第二帧累计 5MB 越过 4MB 阈值触发取许可。
    chunks = [b"a" * (3 * 1024 * 1024), b"b" * (2 * 1024 * 1024), b"c" * 1024]
    pending_frames: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    receive_calls = {"count": 0}

    async def receive() -> Message:
        receive_calls["count"] += 1
        events.append(f"frame:{receive_calls['count']}")
        return pending_frames.pop(0)

    await middleware(
        _large_body_scope([(b"transfer-encoding", b"chunked")]),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert events == ["frame:1", "frame:2", "acquire", "frame:3", "release"]
    assert spy.acquire_calls == 1
    assert spy.release_calls == 1
    assert bodies == [b"".join(chunks)]


@dataclass
class _SaturatedAdmission:
    """饱和装配的主体侧句柄：待测中间件与共享的许可替身、下游记录器与响应记录。"""

    spy: _SpySemaphore
    bodies: list[bytes]
    reached: list[bool]
    middleware: _LimitRequestBodyMiddleware
    sent: list[Message]

    async def send(self, message: Message) -> None:
        self.sent.append(message)


@asynccontextmanager
async def _permits_saturated_by_three_holders(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_SaturatedAdmission]:
    """三个申报大请求驻留读体占满许可的饱和装配，退出时放行并回收持有者。"""
    spy = _install_spy_gate(monkeypatch, 3)
    bodies: list[bytes] = []
    reached: list[bool] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del send
        reached.append(True)
        bodies.append(await Request(scope, receive).body())

    fixture = _SaturatedAdmission(
        spy, bodies, reached, _admission_stack(downstream, 3, 0.05, _ADMISSION_THRESHOLD), []
    )
    # 持闸实例取长读体超时：三个申报大上传驻留读体窗口占满许可，不自行超时。
    holding = _admission_stack(downstream, 3, 5, _ADMISSION_THRESHOLD)
    finish_read = asyncio.Event()
    holders_entered = asyncio.Event()
    entered = {"count": 0}

    async def _holder_receive() -> Message:
        entered["count"] += 1
        if entered["count"] == 3:
            holders_entered.set()
        # 驻留读体窗口直至 finish_read，模拟慢速大上传。
        await finish_read.wait()
        return {"type": "http.request", "body": b"holder", "more_body": False}

    holders = [
        asyncio.ensure_future(
            holding(
                _large_body_scope(_LARGE_CL_HEADERS),
                cast(Receive, _holder_receive),
                cast(Send, _silence_send),
            )
        )
        for _ in range(3)
    ]
    try:
        assert await asyncio.wait_for(holders_entered.wait(), timeout=5), "三个申报大请求未占满许可"
        yield fixture
    finally:
        finish_read.set()
        await asyncio.gather(*holders)


async def test_large_body_admission_chunked_large_times_out_waiting_for_exhausted_permits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """许可被申报大请求占满时 chunked 大行越阈后等待，超时回 408 断连且许可不泄漏。"""
    async with _permits_saturated_by_three_holders(monkeypatch) as saturated:

        async def _chunked_receive() -> Message:
            return {"type": "http.request", "body": b"x" * (5 * 1024 * 1024), "more_body": True}

        # 越阈帧在许可等待中被扣住，读体窗口超时后以 408 与断连收尾。
        await asyncio.wait_for(
            saturated.middleware(
                _large_body_scope([(b"transfer-encoding", b"chunked")]),
                cast(Receive, _chunked_receive),
                cast(Send, saturated.send),
            ),
            timeout=5,
        )

        # 流式准入下四个下游都已启动，但全部挂在读体窗口，无一完成。
        assert saturated.reached == [True, True, True, True]
        assert saturated.bodies == []
        assert saturated.sent[0]["type"] == "http.response.start"
        assert saturated.sent[0]["status"] == 408
        payload = json.loads(saturated.sent[1]["body"])
        assert payload["error"] == "large_body_admission_timeout"
        assert "SEEDREAM_LARGE_BODY_ADMISSION_CONCURRENCY" in payload["error_description"]
        assert saturated.spy.acquire_calls == 4
        assert saturated.spy.release_calls == 0

    assert saturated.spy.release_calls == 3

    # 许可不泄漏：后续申报大请求立即可读体进入下游。
    async def _follow_receive() -> Message:
        return {"type": "http.request", "body": b"follow", "more_body": False}

    await asyncio.wait_for(
        saturated.middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _follow_receive),
            cast(Send, _silence_send),
        ),
        timeout=5,
    )

    assert saturated.reached == [True, True, True, True, True]
    assert saturated.bodies == [b"holder", b"holder", b"holder", b"follow"]
    assert saturated.spy.acquire_calls == 5


async def test_large_body_admission_declared_large_times_out_waiting_for_exhausted_permits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """许可被占满时申报大请求的排队等待同样限时，超时回 408 而非无限期挂起。"""
    async with _permits_saturated_by_three_holders(monkeypatch) as saturated:

        async def _queued_receive() -> Message:
            return {"type": "http.request", "body": b"queued", "more_body": False}

        await asyncio.wait_for(
            saturated.middleware(
                _large_body_scope(_LARGE_CL_HEADERS),
                cast(Receive, _queued_receive),
                cast(Send, saturated.send),
            ),
            timeout=5,
        )

        # 三个持闸实例的下游已启动并挂在读体；排队请求卡在许可等待，未进入下游；许可不泄漏。
        assert saturated.reached == [True, True, True]
        assert saturated.sent[0]["type"] == "http.response.start"
        assert saturated.sent[0]["status"] == 408
        queued_payload = json.loads(saturated.sent[1]["body"])
        assert queued_payload["error"] == "large_body_admission_timeout"
        assert "SEEDREAM_LARGE_BODY_ADMISSION_CONCURRENCY" in queued_payload["error_description"]
        assert saturated.spy.acquire_calls == 4
        assert saturated.spy.release_calls == 0

    assert saturated.spy.release_calls == 3


async def test_large_body_admission_window_exhausted_before_permit_wait_uses_read_timeout_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """窗口耗尽后才尝试取许可的放弃按读体窗口口径发 408：许可纵然空闲也不误报准入饥饿。"""
    spy = _install_spy_gate(monkeypatch, 1)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("放弃路径不应驱动下游")

    middleware = _LargeBodyAdmissionMiddleware(downstream, 1, 0.05, _ADMISSION_THRESHOLD)
    scope = _large_body_scope(_LARGE_CL_HEADERS)
    session = _BodyReadAdmission(
        middleware, scope, cast(Receive, None), cast(Send, send), _BodyReadPolicy(scope)
    )
    # 读体窗口在进入许可等待前已耗尽，许可完全空闲。
    session._read_deadline = session._loop.time() - 0.01

    assert await session._acquire_gate() is None
    assert session.abandoned is True
    assert spy.acquire_calls == 0, "窗口已耗尽时不得触碰许可信号量"
    assert session.timeout_send is not None
    await session.timeout_send

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408
    payload = json.loads(sent[1]["body"])
    assert payload["error"] == "body_read_timeout"
    assert "SEEDREAM_BODY_READ_TIMEOUT" in payload["error_description"]


async def test_large_body_admission_app_construction_failure_releases_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """下游 app 同步抛异常时许可经 finally 归还，看门狗定时器同步拆除。"""
    spy = _install_spy_gate(monkeypatch, 1)

    def broken_app(scope: Any, receive: Any, send: Any) -> Any:
        del scope, receive, send
        # 模拟 SDK 升级后签名失配：调用即同步抛 TypeError。
        raise TypeError("app signature mismatch")

    middleware = _admission_stack(broken_app, 1, 0.05, _ADMISSION_THRESHOLD)

    with pytest.raises(TypeError, match="app signature mismatch"):
        await middleware(
            _large_body_scope(_LARGE_CL_HEADERS), cast(Receive, None), cast(Send, _silence_send)
        )

    assert spy.acquire_calls == 1
    assert spy.release_calls == 1

    # 许可能用：后续申报大请求照常读体完成。
    bodies: list[bytes] = []

    async def _follow_receive() -> Message:
        return {"type": "http.request", "body": b"follow", "more_body": False}

    await asyncio.wait_for(
        _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _follow_receive),
            cast(Send, _silence_send),
        ),
        timeout=5,
    )
    assert bodies == [b"follow"]
    assert spy.acquire_calls == 2


async def test_large_body_admission_suspended_consumer_releases_permit_via_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """持闸期间下游读体后挂起不退栈：看门狗强制收尾归还许可，不钉死后续大请求。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    consumer_suspended = asyncio.Event()
    release_consumer = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        message = await receive()
        bodies.append(b"partial:" + message.get("body", b"")[:4])
        consumer_suspended.set()
        # 读体后挂起不退栈：读取侧不再被调用，唯有看门狗能收尾。
        await release_consumer.wait()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def _single_large_frame_receive() -> Message:
        return {"type": "http.request", "body": b"x" * (5 * 1024 * 1024), "more_body": True}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    suspended = asyncio.ensure_future(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")]),
            cast(Receive, _single_large_frame_receive),
            cast(Send, send),
        )
    )
    assert await asyncio.wait_for(consumer_suspended.wait(), timeout=5), "下游未进入挂起"

    # 看门狗在累计窗口耗尽时强制收尾并归还许可。
    await asyncio.wait_for(suspended, timeout=5)
    for _ in range(200):
        if sent:
            break
        await asyncio.sleep(0.01)
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408
    assert spy.release_calls == 1

    # 许可不钉死：后续申报大请求立即可用。
    async def _fast_receive() -> Message:
        return {"type": "http.request", "body": b"follow", "more_body": False}

    follow = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)
    await asyncio.wait_for(
        follow(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _fast_receive),
            cast(Send, _silence_send),
        ),
        timeout=5,
    )
    assert b"follow" in bodies


async def test_large_body_admission_limit_verdict_releases_permit_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外层流中 413 裁决即刻归还准入许可：无视 413 的滴流客户端不占闸等读体窗口看门狗。"""
    spy = _install_spy_gate(monkeypatch, 1)
    sent: list[Message] = []
    downstream_finished = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        try:
            while True:
                message = await receive()
                if message.get("type") != "http.request" or not message.get("more_body", False):
                    break
        finally:
            downstream_finished.set()

    # 阈值 4MB、上限 5MB：首帧 4.5MB 越阈持闸，第二帧累计 5.5MB 越限触发外层流中 413 裁决。
    admission = _LargeBodyAdmissionMiddleware(downstream, 1, 30, _ADMISSION_THRESHOLD)
    middleware = _LimitRequestBodyMiddleware(admission, 5 * 1024 * 1024)
    pending: list[Message] = [
        {"type": "http.request", "body": b"a" * (4 * 1024 * 1024 + 512 * 1024), "more_body": True},
        {"type": "http.request", "body": b"b" * (1024 * 1024), "more_body": True},
    ]

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        # 无视已收 413 的滴流客户端：持续发 1 字节帧且永不发终帧。
        await asyncio.sleep(0.01)
        return {"type": "http.request", "body": b"x", "more_body": True}

    async def send(message: Message) -> None:
        sent.append(message)

    await asyncio.wait_for(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")]),
            cast(Receive, receive),
            cast(Send, send),
        ),
        timeout=5,
    )

    # 请求即刻收尾：413 是唯一响应（不叠发 408），许可随断连退栈即刻归还。
    assert downstream_finished.is_set(), "裁决后下游未随断连退栈"
    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    body_msg = next(m for m in sent if m.get("type") == "http.response.body")
    assert json.loads(body_msg["body"])["error"] == "request_too_large"
    assert spy.acquire_calls == 1
    assert spy.release_calls == 1, "413 裁决后许可须即刻归还，不得等读体窗口看门狗"

    # 许可已归还：后续申报大请求立即可读体，合法大上传不被滴流客户端饿死。
    follow_bodies: list[bytes] = []

    async def _follow_receive() -> Message:
        return {"type": "http.request", "body": b"follow", "more_body": False}

    await asyncio.wait_for(
        _admission_stack(_admission_downstream(follow_bodies), 1, 5, _ADMISSION_THRESHOLD)(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _follow_receive),
            cast(Send, _silence_send),
        ),
        timeout=5,
    )
    assert follow_bodies == [b"follow"]


async def test_large_body_admission_limit_verdict_silent_client_releases_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外层 413 发出后客户端即静默停发、下游不随断连退栈：裁决送达准入层取消下游，
    许可不等待读体窗口看门狗即归还。"""
    spy = _install_spy_gate(monkeypatch, 1)
    sent: list[Message] = []
    downstream_cancelled = asyncio.Event()
    silent = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        try:
            while True:
                # 断连不敏感的下游：只认终帧，对合成断连帧持续轮询等待。
                message = await receive()
                if message.get("type") == "http.request" and not message.get("more_body", False):
                    break
        except asyncio.CancelledError:
            downstream_cancelled.set()
            raise

    # 阈值 4MB、上限 5MB、读窗 30 秒：申报 5MB 越阈进入即持闸，实际累计 5.5MB
    # 越限触发外层流中 413 裁决。
    admission = _LargeBodyAdmissionMiddleware(downstream, 1, 30, _ADMISSION_THRESHOLD)
    middleware = _LimitRequestBodyMiddleware(admission, 5 * 1024 * 1024)
    pending: list[Message] = [
        {"type": "http.request", "body": b"a" * (4 * 1024 * 1024 + 512 * 1024), "more_body": True},
        {"type": "http.request", "body": b"b" * (1024 * 1024), "more_body": True},
    ]

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        # 收到 413 即静默停发：receive 不再返回任何帧。
        await silent.wait()
        raise AssertionError("静默客户端不应再返回帧")

    async def send(message: Message) -> None:
        sent.append(message)

    # 不等 30 秒读窗看门狗：裁决取消下游后请求须即刻收尾并归还许可。
    await asyncio.wait_for(
        middleware(
            _large_body_scope([(b"content-length", str(5 * 1024 * 1024).encode("ascii"))]),
            cast(Receive, receive),
            cast(Send, send),
        ),
        timeout=2,
    )

    # 413 已发出且为唯一响应（不叠发 408），下游经裁决取消退栈，许可无孤儿持有。
    assert downstream_cancelled.is_set(), "裁决须取消下游子任务，不等下游自行退栈"
    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    assert spy.acquire_calls == 1
    assert spy.release_calls == 1, "静默客户端不得把许可钉死到读体窗口耗尽"


async def test_large_body_admission_watchdog_expiry_does_not_truncate_verdict_direct_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读窗看门狗到期落在外层 413 直发挂起窗口内时不强收：413 完整送达、许可正常归还。

    直发走真实 send 不触发响应转发开始的监护收尾；挂起期间看门狗若取消下游，
    CancelledError 会击穿直发的尽力发送语义截断响应体。
    """
    spy = _install_spy_gate(monkeypatch, 1)
    sent: list[Message] = []
    start_sent = asyncio.Event()
    release_body_send = asyncio.Event()
    downstream_finished = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        try:
            while True:
                message = await receive()
                if message.get("type") != "http.request" or not message.get("more_body", False):
                    break
        finally:
            downstream_finished.set()

    # 阈值 4MB、上限 5MB、读窗 0.05 秒：首帧 4.5MB 越阈持闸，第二帧累计 5.5MB
    # 越限触发外层流中 413 裁决，直发在响应体帧上挂起至放行。
    admission = _LargeBodyAdmissionMiddleware(downstream, 1, 0.05, _ADMISSION_THRESHOLD)
    middleware = _LimitRequestBodyMiddleware(admission, 5 * 1024 * 1024)
    pending: list[Message] = [
        {"type": "http.request", "body": b"a" * (4 * 1024 * 1024 + 512 * 1024), "more_body": True},
        {"type": "http.request", "body": b"b" * (1024 * 1024), "more_body": True},
    ]

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        # 写背压挂起的客户端不再发帧。
        await asyncio.Event().wait()
        raise AssertionError("直发挂起窗口内客户端不应再返回帧")

    async def send(message: Message) -> None:
        if message.get("type") == "http.response.body":
            # 模拟服务器写背压：响应体帧挂起直至放行，放行前不计为已送达。
            await release_body_send.wait()
        sent.append(message)
        if message.get("type") == "http.response.start":
            start_sent.set()

    suspended = asyncio.ensure_future(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")]),
            cast(Receive, receive),
            cast(Send, send),
        )
    )
    assert await asyncio.wait_for(start_sent.wait(), timeout=5), "413 直发未开始"

    # 看门狗在直发挂起窗口内到期：不强收时中间件仍挂起等待直发完成。
    await asyncio.sleep(0.3)
    assert not suspended.done(), "看门狗到期不得取消直发中的 413 而截断响应体"

    release_body_send.set()
    await asyncio.wait_for(suspended, timeout=5)

    # 413 完整送达且为唯一响应，下游正常退栈，许可随收尾归还。
    assert downstream_finished.is_set()
    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    body_msg = next(m for m in sent if m.get("type") == "http.response.body")
    assert json.loads(body_msg["body"])["error"] == "request_too_large"
    assert spy.acquire_calls == 1
    assert spy.release_calls == 1, "直发收尾后许可须正常归还"


async def test_large_body_admission_abandoned_synthetic_frame_yields_to_loop() -> None:
    """abandoned 分支的合成断连帧返回前让出事件循环：下游断连轮询不给让出点即忙转冻结并发任务。"""
    ticks = {"before": -1, "after": -1}
    tick_count = 0

    async def _ticker() -> None:
        nonlocal tick_count
        while True:
            tick_count += 1
            await asyncio.sleep(0)

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        # 外层 413 裁决置 abandoned 后轮询 receive 模拟断连轮询下游，
        # 裁决送达即取消下游：轮询在合成帧的让出点被取消收尾。
        ticks["before"] = tick_count
        try:
            for _ in range(20):
                await receive()
        except asyncio.CancelledError:
            ticks["after"] = tick_count
            raise

    admission = _LargeBodyAdmissionMiddleware(downstream, 1, 30, _ADMISSION_THRESHOLD)
    middleware = _LimitRequestBodyMiddleware(admission, 2048)
    pending: list[Message] = [
        {"type": "http.request", "body": b"a" * 4096, "more_body": True},
    ]

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    ticker = asyncio.ensure_future(_ticker())
    try:
        await asyncio.wait_for(
            middleware(
                _large_body_scope([(b"transfer-encoding", b"chunked")]),
                cast(Receive, receive),
                cast(Send, send),
            ),
            timeout=5,
        )
    finally:
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass

    # 轮询期间其他任务持续被调度：合成帧路径存在让出点。
    assert ticks["before"] >= 0
    assert ticks["after"] > ticks["before"]
    starts = [message for message in sent if message.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413


async def test_large_body_admission_watchdog_waits_for_408_send_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """写背压挂起的 408 发送未落定前中间件不返回，响应不被服务器提前终结。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    consumer_suspended = asyncio.Event()
    release_consumer = asyncio.Event()
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        message = await receive()
        bodies.append(message.get("body", b"")[:4])
        consumer_suspended.set()
        # 读体后挂起不退栈，唯有看门狗能收尾。
        await release_consumer.wait()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def _single_large_frame_receive() -> Message:
        return {"type": "http.request", "body": b"x" * (5 * 1024 * 1024), "more_body": True}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)
        if not send_started.is_set():
            send_started.set()
            # 模拟服务器写背压：首个响应消息挂起，直至放行。
            await release_send.wait()

    suspended = asyncio.ensure_future(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")]),
            cast(Receive, _single_large_frame_receive),
            cast(Send, send),
        )
    )
    assert await asyncio.wait_for(consumer_suspended.wait(), timeout=5), "下游未进入挂起"

    # 408 发送已开始且挂起：中间件等待发送落定，未返回。
    assert await asyncio.wait_for(send_started.wait(), timeout=5), "看门狗未发出 408"
    assert not suspended.done(), "408 未落定前中间件不得返回"

    release_send.set()
    release_consumer.set()
    await asyncio.wait_for(suspended, timeout=5)
    # 许可已随 finally 归还，后续大请求立即可用。
    assert spy.release_calls == 1
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408


async def test_large_body_admission_exception_path_settles_408_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """看门狗收尾后下游以普通异常完成时，408 仍在返回前落定，不与服务器 500 竞发。"""
    spy = _install_spy_gate(monkeypatch, 1)
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # 看门狗取消被转写为普通异常：await 下游子任务逃出两个 except 子句。
            raise RuntimeError("下游以普通异常收尾")

    async def send(message: Message) -> None:
        sent.append(message)
        if not send_started.is_set():
            send_started.set()
            # 模拟服务器写背压：408 首帧挂起，直至放行。
            await release_send.wait()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def _never_receive() -> Message:
        await asyncio.Event().wait()
        raise AssertionError("下游不读体，receive 不应被调用")

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _never_receive),
            cast(Send, send),
        )
    )
    assert await asyncio.wait_for(send_started.wait(), timeout=5), "看门狗未发出 408"
    await asyncio.sleep(0.05)
    # 408 未落定前中间件不得返回，普通异常也不得先逃逸。
    assert not request.done()

    release_send.set()
    with pytest.raises(RuntimeError, match="下游以普通异常收尾"):
        await request
    starts = [message for message in sent if message.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 408
    assert spy.release_calls == 1


async def test_large_body_admission_abandon_releases_permit_while_408_send_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读体窗口耗尽的收尾路径 408 经脱离任务发送：阻塞 send 不在持许可时内联钉死。"""
    spy = _install_spy_gate(monkeypatch, 1)
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    downstream_finished = asyncio.Event()
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        try:
            while True:
                message = await receive()
                if message.get("type") != "http.request" or not message.get("more_body", False):
                    break
        finally:
            downstream_finished.set()

    async def send(message: Message) -> None:
        sent.append(message)
        if not send_started.is_set():
            send_started.set()
            # 模拟服务器写背压：408 首帧挂起，直至放行。
            await release_send.wait()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)
    receive_calls = {"count": 0}

    async def receive() -> Message:
        receive_calls["count"] += 1
        # 首帧越阈即持闸；第二帧驻留至读体窗口耗尽触发收尾。
        await asyncio.sleep(1.0 if receive_calls["count"] > 1 else 0.01)
        return {"type": "http.request", "body": b"x" * (5 * 1024 * 1024), "more_body": True}

    suspended = asyncio.ensure_future(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")]),
            cast(Receive, receive),
            cast(Send, send),
        )
    )
    assert await asyncio.wait_for(send_started.wait(), timeout=5), "408 未开始发送"
    # 408 发送挂起期间下游已被看门狗取消收尾、许可已归还，但中间件按 ASGI 语义等发送落定。
    assert await asyncio.wait_for(downstream_finished.wait(), timeout=5), "下游未随断连收尾"
    for _ in range(200):
        if spy.release_calls:
            break
        await asyncio.sleep(0.01)
    assert spy.release_calls == 1, "阻塞的 408 发送不得钉死许可"
    assert not suspended.done(), "408 未落定前中间件不得返回"

    follow_bodies: list[bytes] = []

    async def _follow_receive() -> Message:
        return {"type": "http.request", "body": b"follow", "more_body": False}

    await asyncio.wait_for(
        _admission_stack(_admission_downstream(follow_bodies), 1, 5, _ADMISSION_THRESHOLD)(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _follow_receive),
            cast(Send, _silence_send),
        ),
        timeout=5,
    )
    assert follow_bodies == [b"follow"]

    release_send.set()
    await asyncio.wait_for(suspended, timeout=5)
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408


async def test_large_body_admission_external_cancel_after_abandon_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """看门狗收尾后到达的外部取消照样传播，不因 abandoned 置位被吞成正常返回。"""
    spy = _install_spy_gate(monkeypatch, 1)
    watchdog_delivered = asyncio.Event()
    release_downstream = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # 吞下看门狗取消模拟收尾缓慢的下游，驻留等待外部放行。
            watchdog_delivered.set()
        await release_downstream.wait()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"payload", "more_body": False}

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, receive),
            cast(Send, _silence_send),
        )
    )
    assert await asyncio.wait_for(watchdog_delivered.wait(), timeout=5), "看门狗未取消下游"

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert request.cancelled(), "外部取消必须传播，不得被吞成正常完成"
    assert spy.release_calls == 1


async def test_large_body_admission_external_cancel_settles_downstream_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部取消重抛前先取消并等待下游任务收尾：不留孤儿继续读写，取消照样传播。"""
    spy = _install_spy_gate(monkeypatch, 1)
    downstream_started = asyncio.Event()
    downstream_finished = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        downstream_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            downstream_finished.set()

    middleware = _admission_stack(downstream, 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"payload", "more_body": False}

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, receive),
            cast(Send, _silence_send),
        )
    )
    assert await asyncio.wait_for(downstream_started.wait(), timeout=5), "下游未启动"

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert request.cancelled(), "外部取消必须传播"
    # 重抛前下游任务已被取消并等待终结，不留孤儿，异常也经吞没取回。
    assert downstream_finished.is_set(), "取消路径未等待下游任务收尾"
    assert spy.release_calls == 1


async def test_large_body_admission_external_cancel_terminal_frame_never_double_releases_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部取消后下游终帧不得二次归还许可：取消收尾与终帧分支合计恰归还一次。"""
    spy = _install_spy_gate(monkeypatch, 1)
    first_frame_read = asyncio.Event()
    cancel_caught = asyncio.Event()
    release_downstream = asyncio.Event()
    terminal_read = asyncio.Event()
    received: list[bytes] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        try:
            await receive()
            first_frame_read.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # 吞下取消驻留，模拟收尾缓慢的下游，放行后读完终帧再随取消收尾。
            cancel_caught.set()
            await release_downstream.wait()
            received.append((await receive()).get("body", b""))
            terminal_read.set()
            raise

    middleware = _admission_stack(downstream, 1, 5, _ADMISSION_THRESHOLD)
    frames: list[Message] = [
        {"type": "http.request", "body": b"first", "more_body": True},
        {"type": "http.request", "body": b"last", "more_body": False},
    ]

    async def receive() -> Message:
        return frames.pop(0)

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, receive),
            cast(Send, _silence_send),
        )
    )
    assert await asyncio.wait_for(first_frame_read.wait(), timeout=5), "下游未读到首帧"

    request.cancel()
    assert await asyncio.wait_for(cancel_caught.wait(), timeout=5), "下游未收到取消"
    release_downstream.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert request.cancelled(), "外部取消必须传播"
    assert terminal_read.is_set(), "下游须真实读到终帧以暴露二次释放窗口"
    assert received == [b"last"]
    assert spy.acquire_calls == 1
    assert spy.release_calls == 1, "取消收尾与终帧分支合计不得二次释放同一许可"

    release_downstream.set()
    await asyncio.sleep(0)


async def test_large_body_admission_repeated_host_cancel_releases_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """落定等待只吸收窗口内首个取消：重复取消立即放行监督者并经 finally 归还许可，
    吞取消驻留的下游不钉死监督者，脱离子任务短暂孤儿收尾。"""
    spy = _install_spy_gate(monkeypatch, 1)
    downstream_started = asyncio.Event()
    downstream_cancelled = asyncio.Event()
    release_downstream = asyncio.Event()
    downstream_finished = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, receive, send
        downstream_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # 吞下转发的取消驻留收尾，暴露落定等待期间重复宿主取消的窗口。
            downstream_cancelled.set()
            await release_downstream.wait()
            raise
        finally:
            downstream_finished.set()

    middleware = _admission_stack(downstream, 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"payload", "more_body": False}

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, receive),
            cast(Send, _silence_send),
        )
    )
    assert await asyncio.wait_for(downstream_started.wait(), timeout=5), "下游未启动"

    request.cancel()
    assert await asyncio.wait_for(downstream_cancelled.wait(), timeout=5), "取消未转发到下游"
    for _ in range(10):
        await asyncio.sleep(0)
    assert not request.done(), "首个取消应被落定等待吸收，子任务收尾前不放行"

    # 窗口内第二个取消仍被吸收：落定等待维持至子任务落定或预算耗尽。
    request.cancel()
    for _ in range(10):
        await asyncio.sleep(0)
    assert not request.done(), "落定等待须吸收窗口内首个重复取消"

    # 窗口内第三个取消耗尽有界档单次吸收预算：监督者立即放行并经 finally 归还许可，
    # 吞取消无界驻留的下游由此不再钉死监督者与准入许可，脱离子任务自行孤儿收尾。
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert request.cancelled(), "重复取消须打断落定等待放行，任务保持可取消"
    assert spy.release_calls == 1, "放行须伴随许可归还"
    assert not downstream_finished.is_set(), "前置：子任务仍驻留收尾，监督者已先行退出"

    release_downstream.set()
    assert await asyncio.wait_for(downstream_finished.wait(), timeout=5), "孤儿子任务须仍能自行收尾"


async def test_large_body_admission_slow_chunked_stream_times_out_on_cumulative_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """匀速慢传无法逐帧续命：持闸读体按累计读体窗口超时，408 断连收尾。"""
    _install_spy_gate(monkeypatch, 3)
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del send
        await Request(scope, receive).body()

    middleware = _admission_stack(downstream, 3, 0.05, _ADMISSION_THRESHOLD)
    frames_sent = {"count": 0}

    async def receive() -> Message:
        frames_sent["count"] += 1
        # 帧间隔远小于单帧窗口，旧口径逐帧刷新永不超时，累计口径第 3 帧越窗。
        await asyncio.sleep(0.02)
        return {
            "type": "http.request",
            "body": b"x" * (5 * 1024 * 1024),
            "more_body": True,
        }

    async def send(message: Message) -> None:
        sent.append(message)

    await asyncio.wait_for(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")]),
            cast(Receive, receive),
            cast(Send, send),
        ),
        timeout=5,
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408
    assert json.loads(sent[1]["body"])["error"] == "body_read_timeout"
    assert frames_sent["count"] < 10


async def test_large_body_admission_subthreshold_undeclared_stall_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """亚阈值未申报慢速挂起同受读体窗口约束：超窗回 408 断连，不取许可不钉连接。"""
    spy = _install_spy_gate(monkeypatch, 1)
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del send
        await Request(scope, receive).body()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        # 未申报且永不发终帧：总量不及阈值，旧口径无闸无看门狗即无限挂起。
        await asyncio.Event().wait()
        raise AssertionError("挂起的上传不再返回任何帧")

    async def send(message: Message) -> None:
        sent.append(message)

    await asyncio.wait_for(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")]),
            cast(Receive, receive),
            cast(Send, send),
        ),
        timeout=5,
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408
    assert json.loads(sent[1]["body"])["error"] == "body_read_timeout"
    assert spy.acquire_calls == 0


async def test_large_body_admission_consumer_stall_without_gate_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未取闸读体同受看门狗监护：下游读一帧后停滞不再读，窗口耗尽强制收尾回 408。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    consumer_suspended = asyncio.Event()

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        message = await receive()
        bodies.append(message.get("body", b""))
        consumer_suspended.set()
        # 读一帧后停滞不再读：无闸路径若无看门狗监护即永久挂起。
        await asyncio.Event().wait()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"part", "more_body": True}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")]),
            cast(Receive, receive),
            cast(Send, send),
        )
    )
    assert await asyncio.wait_for(consumer_suspended.wait(), timeout=5), "下游未进入停滞"

    await asyncio.wait_for(request, timeout=5)
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408
    assert json.loads(sent[1]["body"])["error"] == "body_read_timeout"
    assert spy.acquire_calls == 0


async def test_large_body_admission_te_get_never_ending_survives_window_after_response_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """携 Transfer-Encoding 永不终结的 GET 响应转发开始后拆看门狗：会话越窗存活、许可即归还。"""
    spy = _install_spy_gate(monkeypatch, 1)
    read_timeout = 0.05
    response_started = asyncio.Event()
    downstream_cancelled = asyncio.Event()
    disconnect_released = asyncio.Event()
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope
        # 先读一帧越阈持闸再开始转发响应，SSE 形态随后仅轮询断连。
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"data", "more_body": True})
        response_started.set()
        try:
            while (await receive()).get("type") != "http.disconnect":
                pass
        except asyncio.CancelledError:
            downstream_cancelled.set()
            raise

    # 阈值 16 使首帧 32 字节即越阈持闸，免构造兆级字节串。
    middleware = _admission_stack(downstream, 1, read_timeout, 16)
    receive_calls = {"count": 0}

    async def receive() -> Message:
        receive_calls["count"] += 1
        if receive_calls["count"] == 1:
            return {"type": "http.request", "body": b"x" * 32, "more_body": True}
        # 永不发终结零块：断连前读体侧恒停滞。
        await disconnect_released.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")], method="GET"),
            cast(Receive, receive),
            cast(Send, send),
        )
    )
    assert await asyncio.wait_for(response_started.wait(), timeout=5), "响应未开始转发"

    # 读体窗口耗尽后会话仍存活：看门狗已随首个响应帧拆除，408 不截断已开始的响应流。
    await asyncio.sleep(read_timeout * 4)
    assert not request.done()
    assert not downstream_cancelled.is_set()
    assert not any(
        message.get("type") == "http.response.start" and message.get("status") == 408
        for message in sent
    )
    # 响应已开始而 body 永不终结：许可在响应转发开始即归还，不随长会话钉死。
    assert spy.acquire_calls == 1
    assert spy.release_calls == 1

    disconnect_released.set()
    await asyncio.wait_for(request, timeout=5)
    assert not request.cancelled()
    assert spy.release_calls == 1, "响应期归还后收尾不得二次归还"


async def test_large_body_admission_over_threshold_frame_after_response_start_skips_reacquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """响应转发开始后到达的越阈帧不再补取准入许可：监护已终止，补取会把已归还的许可重新钉死。"""
    spy = _install_spy_gate(monkeypatch, 1)
    frames: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope
        # 首帧越阈持闸后开始转发响应，随后再读一帧：响应期到达的越阈帧不得重新取闸。
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"data", "more_body": True})
        frames.append(await receive())

    # 阈值 16 使 32 字节帧即越阈，免构造兆级字节串。
    middleware = _admission_stack(downstream, 1, 5, 16)
    receive_calls = {"count": 0}

    async def receive() -> Message:
        receive_calls["count"] += 1
        if receive_calls["count"] <= 2:
            return {"type": "http.request", "body": b"x" * 32, "more_body": True}
        return {"type": "http.disconnect"}

    await asyncio.wait_for(
        middleware(
            _large_body_scope([(b"transfer-encoding", b"chunked")], method="GET"),
            cast(Receive, receive),
            cast(Send, _silence_send),
        ),
        timeout=5,
    )

    assert len(frames) == 1, "响应期越阈帧未被读到"
    assert frames[0].get("type") == "http.request"
    assert spy.acquire_calls == 1, "响应转发开始后不得重新补取准入许可"
    assert spy.release_calls == 1, "响应期归还后收尾不得二次归还"


async def test_large_body_admission_terminal_frame_crossing_threshold_skips_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """累计越过阈值的终帧不取许可：请求体随帧收完，占许可无防护意义。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)
    # 首帧 3MB 未越阈，终帧累计 5MB 越阈但 more_body=False，免许可直通。
    chunks = [b"a" * (3 * 1024 * 1024), b"b" * (2 * 1024 * 1024)]
    pending_frames: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive() -> Message:
        return pending_frames.pop(0)

    await middleware(
        _large_body_scope([(b"transfer-encoding", b"chunked")]),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert spy.acquire_calls == 0
    assert bodies == [b"".join(chunks)]


@pytest.mark.parametrize(
    "declared",
    [0, 1, _ADMISSION_THRESHOLD, _ADMISSION_THRESHOLD + 1],
    ids=["zero", "one", "at-threshold", "above-threshold"],
)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
def test_large_body_admission_declared_supervision_bypass_only_for_zero(
    method: str, declared: int
) -> None:
    """申报请求的免监督判定只看有无申报体：零申报直通，申报体不论大小与方法一律进监督。"""
    scope = _large_body_scope([(b"content-length", str(declared).encode("ascii"))], method=method)
    middleware = _LargeBodyAdmissionMiddleware(
        _admission_downstream([]), 1, 5, _ADMISSION_THRESHOLD
    )
    bypasses = middleware._bypasses_supervision(_BodyReadPolicy(scope), scope)

    assert bypasses is (declared == 0)


@pytest.mark.parametrize(
    "declared",
    [b"-1", b"+5", b"1_0", b" 5"],
    ids=["negative", "plus-signed", "underscore", "leading-space"],
)
def test_large_body_admission_malformed_declaration_takes_undeclared_path(
    declared: bytes,
) -> None:
    """畸形申报按无申报解析：POST 不因负值等 int() 可解析形态误判零申报而免监督直通。"""
    scope = _large_body_scope([(b"content-length", declared)], method="POST")
    middleware = _LargeBodyAdmissionMiddleware(
        _admission_downstream([]), 1, 5, _ADMISSION_THRESHOLD
    )

    bypasses = middleware._bypasses_supervision(_BodyReadPolicy(scope), scope)

    assert bypasses is False


async def test_large_body_admission_small_declaration_stall_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """小申报慢速挂起同受读体窗口约束：免并发许可不免窗口，撒谎申报钉不死连接。"""
    spy = _install_spy_gate(monkeypatch, 1)
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del send
        await Request(scope, receive).body()

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        # 申报 1 字节却永不发终帧：每分钟滴一个字节的慢速上传形态。
        await asyncio.Event().wait()
        raise AssertionError("挂起的小申报上传不再返回任何帧")

    async def send(message: Message) -> None:
        sent.append(message)

    await asyncio.wait_for(
        middleware(
            _large_body_scope([(b"content-length", b"1")]),
            cast(Receive, receive),
            cast(Send, send),
        ),
        timeout=5,
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408
    assert json.loads(sent[1]["body"])["error"] == "body_read_timeout"
    assert spy.acquire_calls == 0


async def test_large_body_admission_small_declaration_lying_overflow_takes_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """小申报谎报、实际累计越阈的请求体照常补取许可：免许可不得被撒谎申报套走。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)
    # 申报 1024 字节，实际分帧累计 5MB 越阈，越阈帧未到终帧即补取许可。
    chunks = [b"a" * (3 * 1024 * 1024), b"b" * (2 * 1024 * 1024), b"c" * 1024]
    pending_frames: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive() -> Message:
        return pending_frames.pop(0)

    await middleware(
        _large_body_scope([(b"content-length", b"1024")]),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert spy.acquire_calls == 1
    assert spy.release_calls == 1
    assert bodies == [b"".join(chunks)]


async def test_large_body_admission_zero_declaration_bypass_outlives_read_window() -> None:
    """零申报免监督直通：无体可读的请求不受读体窗口打扰，建流长连接不被 408 打断。"""
    release_receive = asyncio.Event()
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        # SSE 建流形态：驻留等一帧断连，远超读体窗口。
        assert (await receive())["type"] == "http.disconnect"

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        await release_receive.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope([(b"content-length", b"0")], path="/mcp", method="GET"),
            cast(Receive, receive),
            cast(Send, send),
        )
    )
    await asyncio.sleep(0.2)
    # 窗口早已耗尽而零申报建流不收 408、不被断连回放打断。
    assert not request.done()
    assert sent == []

    release_receive.set()
    await asyncio.wait_for(request, timeout=5)


@pytest.mark.parametrize("method", sorted(_BODYLESS_METHODS))
async def test_large_body_admission_bodyless_method_without_declaration_skips_permit(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """无体方法缺失申报属正常形态（SSE 建流、静态页 GET、会话删除），免监督直通、不取准入许可。"""

    def _forbidden(limit: int, *, key: str) -> Any:
        raise AssertionError(f"无体方法无申报不应取准入许可: {key}")

    monkeypatch.setattr(transport_module, "loop_bound_semaphore", _forbidden)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    for path in ("/mcp", "/web", "/web/static/app.js", "/web/api/gallery"):
        await middleware(
            _large_body_scope([], path=path, method=method),
            cast(Receive, receive),
            cast(Send, _silence_send),
        )

    assert bodies == [b"", b"", b"", b""]


async def test_large_body_admission_get_not_queued_when_permits_exhausted() -> None:
    """许可被慢速大上传占满时，无申报 GET 即时免监督直通，SSE 建流与静态页不被冻结。"""
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)
    read_window_held = asyncio.Event()
    finish_read = asyncio.Event()

    async def _slow_upload_receive() -> Message:
        # 驻留读体窗口，模拟占满唯一许可的慢速大上传。
        read_window_held.set()
        await finish_read.wait()
        return {"type": "http.request", "body": b"upload", "more_body": False}

    upload = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _slow_upload_receive),
            cast(Send, _silence_send),
        )
    )
    assert await asyncio.wait_for(read_window_held.wait(), timeout=5), "大上传未驻留读体窗口"

    async def _get_receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    get_request = asyncio.ensure_future(
        middleware(
            _large_body_scope([], path="/mcp", method="GET"),
            cast(Receive, _get_receive),
            cast(Send, _silence_send),
        )
    )
    await asyncio.sleep(0.1)
    # 许可占满不阻塞无申报 GET：直通即完成，无需等待大上传归还许可。
    assert get_request.done(), "许可占满时无申报 GET 不应排队"
    await get_request
    assert bodies == [b""]

    finish_read.set()
    await upload
    assert bodies == [b"", b"upload"]


async def test_large_body_admission_bodyless_large_declaration_still_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无体方法申报大 Content-Length 仍按申报口径进入即取许可。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"declared", "more_body": False}

    await middleware(
        _large_body_scope(_LARGE_CL_HEADERS, method="GET"),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert spy.acquire_calls == 1
    assert bodies == [b"declared"]


@pytest.mark.parametrize(
    "headers",
    [[(b"content-length", b"abc")], [(b"transfer-encoding", b"chunked")]],
    ids=["invalid-cl", "chunked"],
)
async def test_large_body_admission_bodyless_undeclared_small_body_skips_permit(
    monkeypatch: pytest.MonkeyPatch, headers: list[tuple[bytes, bytes]]
) -> None:
    """无体方法的无有效申报小请求体不取许可：非法申报直通，申报 transfer-encoding 走惰性闸。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"declared", "more_body": False}

    await middleware(
        _large_body_scope(headers, method="GET"),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert spy.acquire_calls == 0
    assert bodies == [b"declared"]


async def test_large_body_admission_bodyless_bypass_outlives_read_window() -> None:
    """无体方法免监督直通：读体窗口耗尽后驻留的建流请求不受 408 打扰。"""
    release_receive = asyncio.Event()
    sent: list[Message] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del scope, send
        # SSE 建流形态：驻留等一帧断连，远超读体窗口。
        assert (await receive())["type"] == "http.disconnect"

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def receive() -> Message:
        await release_receive.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    request = asyncio.ensure_future(
        middleware(
            _large_body_scope([], path="/mcp", method="GET"),
            cast(Receive, receive),
            cast(Send, send),
        )
    )
    await asyncio.sleep(0.2)
    # 窗口早已耗尽而 GET 建流不收 408、不被断连回放打断。
    assert not request.done()
    assert sent == []

    release_receive.set()
    await asyncio.wait_for(request, timeout=5)


async def test_large_body_admission_streams_read_body_to_downstream() -> None:
    """分帧请求体经流式准入原样转发，下游 Request.body() 拼得完整请求体。"""
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 3, 5, _ADMISSION_THRESHOLD)
    frames: list[Message] = [
        {"type": "http.request", "body": b'{"prompt": "', "more_body": True},
        {"type": "http.request", "body": "一只猫".encode("utf-8"), "more_body": False},
    ]

    async def receive() -> Message:
        return frames.pop(0)

    await middleware(
        _large_body_scope(_LARGE_CL_HEADERS), cast(Receive, receive), cast(Send, _silence_send)
    )

    assert bodies == ['{"prompt": "一只猫'.encode("utf-8")]


async def test_large_body_admission_queues_concurrent_large_reads() -> None:
    """并发大请求体在许可预算内排队：首个读体未完成时第二个不开始读体。"""
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, _ADMISSION_THRESHOLD)
    read_window_held = asyncio.Event()
    finish_read = asyncio.Event()
    first_receive_calls = {"calls": 0}
    second_receive_calls = {"calls": 0}

    async def _first_receive() -> Message:
        first_receive_calls["calls"] += 1
        if first_receive_calls["calls"] == 1:
            return {"type": "http.request", "body": b'{"prompt": "', "more_body": True}
        # 第二帧起驻留读体窗口，模拟慢速上传的大载荷。
        read_window_held.set()
        await finish_read.wait()
        return {"type": "http.request", "body": '一只猫"}'.encode("utf-8"), "more_body": False}

    async def _second_receive() -> Message:
        second_receive_calls["calls"] += 1
        return {"type": "http.request", "body": b"second", "more_body": False}

    first = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _first_receive),
            cast(Send, _silence_send),
        )
    )
    assert await asyncio.wait_for(read_window_held.wait(), timeout=5), "首个请求未驻留读体窗口"
    second = asyncio.ensure_future(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _second_receive),
            cast(Send, _silence_send),
        )
    )
    await asyncio.sleep(0.1)
    # 许可预算只容一个大载荷：第二个请求在读取前排队，receive 不被调用。
    assert second_receive_calls["calls"] == 0
    assert not bodies

    finish_read.set()
    await asyncio.gather(first, second)

    assert second_receive_calls["calls"] >= 1
    assert set(bodies) == {'{"prompt": "一只猫"}'.encode("utf-8"), b"second"}


async def test_large_body_admission_read_timeout_returns_408_and_releases_permit() -> None:
    """读体超时回 408 并归还许可：挂起的慢速读体不再永久钉死许可。"""
    bodies: list[bytes] = []
    downstream_reached: list[bool] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        downstream_reached.append(True)
        bodies.append(await Request(scope, receive).body())

    middleware = _admission_stack(downstream, 1, 0.05, _ADMISSION_THRESHOLD)

    async def _hanging_receive() -> Message:
        # 模拟上传停滞：receive 不返回任何帧，读体窗口直至超时。
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(
        _large_body_scope(_LARGE_CL_HEADERS), cast(Receive, _hanging_receive), cast(Send, send)
    )

    # 流式准入下下游已进入读体挂起，但请求体未完成。
    assert downstream_reached == [True]
    assert bodies == []
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 408
    payload = json.loads(sent[1]["body"])
    assert payload["error"] == "body_read_timeout"
    assert "SEEDREAM_BODY_READ_TIMEOUT" in payload["error_description"]

    # 许可已归还：后续大请求立即可读体进入下游，不被残留持有者阻塞。
    async def _fast_receive() -> Message:
        return {"type": "http.request", "body": b"follow", "more_body": False}

    await asyncio.wait_for(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _fast_receive),
            cast(Send, _silence_send),
        ),
        timeout=5,
    )

    assert bodies == [b"follow"]


async def test_large_body_admission_client_disconnect_abandons_request() -> None:
    """读体中客户端断开即放弃该请求：下游随断连退栈且不发响应，许可随 finally 释放。"""
    bodies: list[bytes] = []
    downstream_reached: list[bool] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        downstream_reached.append(True)
        bodies.append(await Request(scope, receive).body())

    middleware = _admission_stack(downstream, 1, 5, _ADMISSION_THRESHOLD)

    async def _disconnecting_receive() -> Message:
        return {"type": "http.disconnect"}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    records: list[str] = []
    with capture_loguru_messages(records, level="INFO"):
        await middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _disconnecting_receive),
            cast(Send, send),
        )

    # 流式准入下下游已启动，随即因断连消息退栈；观测性由 INFO 级日志携持闸阶段承担。
    assert downstream_reached == [True]
    assert bodies == []
    assert sent == []
    assert any("客户端断开" in record and "持闸" in record for record in records)

    async def _fast_receive() -> Message:
        return {"type": "http.request", "body": b"follow", "more_body": False}

    await asyncio.wait_for(
        middleware(
            _large_body_scope(_LARGE_CL_HEADERS),
            cast(Receive, _fast_receive),
            cast(Send, _silence_send),
        ),
        timeout=5,
    )

    assert bodies == [b"follow"]


async def test_large_body_admission_passes_non_http_scope() -> None:
    """lifespan 等非 http 流量直接透传，不经准入闸。"""
    reached: list[object] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope.get("type"))

    middleware = _LargeBodyAdmissionMiddleware(downstream, 1, 5, _ADMISSION_THRESHOLD)
    await middleware({"type": "lifespan"}, cast(Receive, None), cast(Send, None))

    assert reached == ["lifespan"]


def test_attach_admission_params_come_from_active_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """准入中间件的许可数与读体超时取自活动配置对应字段，阈值按默认上限派生 4MB。"""
    config = SeedreamConfig(
        api_key="test_key", large_body_admission_concurrency=7, body_read_timeout=9
    )
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    admission = next(ref for ref in app.user_middleware if ref.cls is _LargeBodyAdmissionMiddleware)
    assert admission.kwargs["admission_concurrency"] == 7
    assert admission.kwargs["read_timeout"] == 9
    assert admission.kwargs["admission_threshold"] == 4 * 1024 * 1024


@pytest.mark.parametrize(
    ("max_body_size", "expected_threshold"),
    [
        (2 * 1024 * 1024, 2 * 1024 * 1024),
        (4 * 1024 * 1024, 4 * 1024 * 1024),
        (64 * 1024 * 1024, 4 * 1024 * 1024),
    ],
    ids=["below-ceiling", "at-ceiling", "above-ceiling"],
)
def test_large_body_admission_threshold_takes_min_of_ceiling_and_max_body_size(
    max_body_size: int, expected_threshold: int
) -> None:
    """生效阈值取 4MB 档与请求体上限的较小者：上限较小时准入旋钮仍生效。"""
    assert transport_module._large_body_admission_threshold(max_body_size) == expected_threshold


def test_attach_derives_admission_threshold_from_max_body_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求体上限低于 4MB 档时装配出的准入阈值同步降低，旋钮不因上限变小失效。"""
    config = SeedreamConfig(api_key="test_key", http_max_body_size=2 * 1024 * 1024)
    monkeypatch.setattr(transport_module, "get_active_config", lambda: config)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    admission = next(ref for ref in app.user_middleware if ref.cls is _LargeBodyAdmissionMiddleware)
    assert admission.kwargs["admission_threshold"] == 2 * 1024 * 1024


def test_body_read_policy_missing_limit_layer_fails_fast() -> None:
    """仅挂准入层（无配对上限层先行发布策略）时取回抛 RuntimeError 而非 KeyError 冒泡 500。"""
    with pytest.raises(RuntimeError, match="成对挂载"):
        _resolve_body_read_policy(_large_body_scope([]))


async def test_large_body_admission_small_threshold_still_gates_chunked_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """阈值随上限降至 2MB 后，chunked 大行累计越过 2MB 照常补取许可。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    middleware = _admission_stack(_admission_downstream(bodies), 1, 5, 2 * 1024 * 1024)
    chunks = [b"a" * (1024 * 1024), b"b" * (1536 * 1024), b"c" * 1024]
    pending_frames: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive() -> Message:
        return pending_frames.pop(0)

    await middleware(
        _large_body_scope([(b"transfer-encoding", b"chunked")]),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert spy.acquire_calls == 1
    assert spy.release_calls == 1
    assert bodies == [b"".join(chunks)]


async def test_body_read_policy_counted_once_across_limit_and_admission_layers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外层读体计数经 scope 键共享给内层准入：两层叠加字节只计一次，阈值判定同源。"""
    spy = _install_spy_gate(monkeypatch, 1)
    bodies: list[bytes] = []
    scopes: list[Any] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del send
        scopes.append(scope)
        bodies.append(await Request(scope, receive).body())

    admission = _LargeBodyAdmissionMiddleware(downstream, 1, 5, _ADMISSION_THRESHOLD)
    stacked = _LimitRequestBodyMiddleware(admission, 64 * 1024 * 1024)
    chunks = [b"a" * (3 * 1024 * 1024), b"b" * (2 * 1024 * 1024), b"c" * 1024]
    pending_frames: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive() -> Message:
        return pending_frames.pop(0)

    await stacked(
        _large_body_scope([(b"transfer-encoding", b"chunked")]),
        cast(Receive, receive),
        cast(Send, _silence_send),
    )

    assert spy.acquire_calls == 1
    assert spy.release_calls == 1
    assert bodies == [b"".join(chunks)]
    policy = _resolve_body_read_policy(scopes[0])
    assert policy.total_received == sum(len(chunk) for chunk in chunks)


# ==================== 请求体超限 413 直发守护 ====================


async def test_request_body_limit_early_reject_swallows_send_failure() -> None:
    """Content-Length 早拒直发 413 的 send 失败被吞掉，与流中判定点同口径静默。"""

    async def send(message: Message) -> None:
        # 模拟已断开的死连接：任何响应写入都失败。
        del message
        raise RuntimeError("client disconnected")

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("超限请求不应进入下游应用")

    middleware = transport_module._LimitRequestBodyMiddleware(downstream, 1024)
    scope = {"type": "http", "headers": [(b"content-length", b"2048")]}
    records: list[str] = []
    with capture_loguru_messages(records, level="DEBUG"):
        await middleware(scope, cast(Receive, None), send)

    assert any("短路响应直发失败" in record for record in records)


async def test_send_rejection_swallows_dead_connection_send_failure() -> None:
    """拒绝发送共用入口内建尽力语义：死连接上的发送失败降为 debug 静默不冒泡。"""

    async def send(message: Message) -> None:
        del message
        raise RuntimeError("client disconnected")

    records: list[str] = []
    with capture_loguru_messages(records, level="DEBUG"):
        await transport_module._send_rejection(send, 403, "invalid_host", "Host not allowed")

    assert any("短路响应直发失败" in record for record in records)
