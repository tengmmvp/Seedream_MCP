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
    _attach_streamable_http_middleware,
    _warn_remote_exposure,
)


@dataclass
class _MiddlewareRef:
    """替身中间件条目，暴露 cls 属性镜像 starlette.middleware.Middleware 的形态。"""

    cls: type


class _FakeStarletteApp:
    """记录中间件装配的 app 替身，user_middleware 按 Starlette insert(0) 语义维护。"""

    def __init__(self) -> None:
        self.user_middleware: list[_MiddlewareRef] = []

    def add_middleware(self, middleware_class: type, **kwargs: Any) -> None:
        del kwargs
        self.user_middleware.insert(0, _MiddlewareRef(middleware_class))

    def attached_classes(self) -> list[type]:
        return [ref.cls for ref in self.user_middleware]


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
