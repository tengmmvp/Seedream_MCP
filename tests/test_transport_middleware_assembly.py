"""streamable-http 中间件装配幂等性测试。

_attach_streamable_http_middleware 在同一 app 实例上重复装配会叠加重复中间件层，
装配前检测本模块任一中间件已存在即整体跳过。以镜像 Starlette user_middleware 语义
的替身 app 计数装配结果，锁定二次装配不叠加。
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
    """回环绑定且配置令牌时装配四层：Bearer、请求体上限、Host 校验、健康检查。"""
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    # add_middleware 经 insert(0) 使后添加者居前：健康检查最外、Bearer 最内。
    assert app.attached_classes() == [
        _HealthCheckMiddleware,
        _LoopbackHostGuardMiddleware,
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


def test_repeated_attach_on_same_app_does_not_stack(active_config: None) -> None:
    """同一 app 实例二次装配跳过 add_middleware，中间件栈不叠加。

    streamable_http_app 每次调用新建 Starlette app 但复用缓存的 _session_manager，
    若同一 app 实例重复进入装配，重复层会使每个请求被多次包覆。装配幂等守卫
    保证二次调用不再增加任何中间件。
    """
    app = _FakeStarletteApp()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")
    first_pass = app.attached_classes()

    _attach_streamable_http_middleware(app, "127.0.0.1", "secret")

    assert app.attached_classes() == first_pass
