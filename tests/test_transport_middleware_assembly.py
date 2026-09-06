"""streamable-http 中间件装配与幂等性测试。

以镜像 Starlette user_middleware 语义的替身 app 锁定装配层次与顺序；同一 app
重复装配时检测已有中间件即整体跳过，不叠加。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import seedream_mcp.transport as transport_module
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.transport import (
    _BearerTokenAuthMiddleware,
    _HealthCheckMiddleware,
    _LimitRequestBodyMiddleware,
    _LoopbackHostGuardMiddleware,
    _WebOriginGuardMiddleware,
    _attach_streamable_http_middleware,
    _warn_remote_exposure,
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


def test_attach_explicit_max_body_size_skips_config_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式传入 max_body_size 时不读取活动配置，生产路径取值一次直接复用。"""

    def _fail_read() -> SeedreamConfig:
        raise AssertionError("显式传入 max_body_size 时不应读取活动配置")

    monkeypatch.setattr(transport_module, "get_active_config", _fail_read)
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "", max_body_size=1048576)

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


# ==================== Origin 守卫同源判定 ====================

_API_PREFIX = "/web/api/"


def _make_guard_app() -> tuple[list[object], _WebOriginGuardMiddleware]:
    """构造记录到达路径的守卫中间件与下游替身，返回记录列表与守卫实例。"""
    reached: list[object] = []

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        reached.append(scope.get("path"))

    return reached, _WebOriginGuardMiddleware(downstream, api_prefix=_API_PREFIX)


async def _run_guard(guard: _WebOriginGuardMiddleware, scope: dict) -> list[dict]:
    sent: list[dict] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    await guard(scope, None, send)
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

    await guard({"type": "lifespan"}, None, None)

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
        ("127.0.0.1", True, "已启用 Bearer 鉴权", "未启用"),
        ("127.0.0.1", False, "未启用应用层认证", "已启用 Bearer 鉴权"),
        ("0.0.0.0", True, "已启用 Bearer 鉴权", "未启用"),
        ("0.0.0.0", False, "未启用鉴权", "已启用 Bearer 鉴权"),
    ],
)
def test_warn_remote_exposure_reports_truthful_auth_state(
    host: str,
    auth_enabled: bool,
    expected_fragment: str,
    absent_fragment: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """告警文案与传入的鉴权状态一致，任何调用路径不得输出相反状态。

    非回环且未启用时若沿用已启用文案，运维会误判暴露面已受保护。
    """
    _warn_remote_exposure(host, auth_enabled)

    output = capsys.readouterr().err
    assert expected_fragment in output
    assert absent_fragment not in output


def test_warn_remote_exposure_appends_web_notice_when_enabled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """web_enabled 时告警按鉴权状态分支追加 Web 说明，默认形态不出现该文案。"""
    _warn_remote_exposure("127.0.0.1", True)
    assert "Web 操作台已开启" not in capsys.readouterr().err

    _warn_remote_exposure("127.0.0.1", True, web_enabled=True)

    token_output = capsys.readouterr().err
    assert "Web 操作台已开启" in token_output
    assert "/web/api 接口仍要求 Bearer 令牌" in token_output
    assert "未配置令牌" not in token_output

    _warn_remote_exposure("127.0.0.1", False, web_enabled=True)

    no_token_output = capsys.readouterr().err
    assert "Web 操作台已开启且未配置令牌" in no_token_output
    assert "跨源与跨站请求（含兄弟子域图片嵌入）将被拒绝" in no_token_output
    assert "建议配置 --auth-token" in no_token_output
    assert "仍要求 Bearer 令牌" not in no_token_output
