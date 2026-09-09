"""工具链 roots 依赖解析器测试：无反向通道时不发起取回，回退环境变量边界。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from mcp.server.mcpserver.resolve import ListRoots

from seedream_mcp.server import _workspace_roots_dependency

from test_workspace_roots_scope import _CapabilityDeclaringSession


class _DependencyContext:
    """仅暴露 session 与可选协商版本的上下文替身。"""

    def __init__(self, session: object, protocol_version: str | None = None) -> None:
        self.session = session
        if protocol_version is not None:
            self.protocol_version = protocol_version


class _BackChannelSession(_CapabilityDeclaringSession):
    """带固定反向通道探测结果的会话替身。"""

    def __init__(self, roots: list[Path], can_send_request: bool) -> None:
        super().__init__(roots, declared=True)
        self.can_send_request = can_send_request


def _declaring_session(declared: bool = True) -> _CapabilityDeclaringSession:
    return _CapabilityDeclaringSession([Path("/workspace")], declared)


def test_resolver_uses_multi_round_over_modern_revision_without_back_channel() -> None:
    """2026-07-28 及以后的取回经 InputRequiredResult 多轮形态，不依赖反向通道。"""
    session = _BackChannelSession([Path("/workspace")], False)
    ctx = _DependencyContext(session, protocol_version="2026-07-28")

    result = _workspace_roots_dependency(cast(Any, ctx))

    assert isinstance(result, ListRoots)


def test_resolver_returns_list_roots_when_back_channel_available() -> None:
    """声明 roots capability 且反向通道可用时发起取回。"""
    session = _BackChannelSession([Path("/workspace")], True)

    result = _workspace_roots_dependency(cast(Any, _DependencyContext(session)))

    assert isinstance(result, ListRoots)


def test_resolver_declines_when_back_channel_unavailable() -> None:
    """无状态传输等无反向通道场景返回 None，SDK 不发起必失败的取回。

    此前该组合下 SDK 抛 NoBackChannelError 序列化为 -32600，五个工具全部不可用。
    """
    session = _BackChannelSession([Path("/workspace")], False)

    result = _workspace_roots_dependency(cast(Any, _DependencyContext(session)))

    assert result is None


def test_resolver_treats_missing_probe_as_available() -> None:
    """会话无反向通道属性时保守视为可用，保持旧版 SDK 与替身下的原有行为。"""
    result = _workspace_roots_dependency(cast(Any, _DependencyContext(_declaring_session())))

    assert isinstance(result, ListRoots)


@pytest.mark.parametrize("session", [None, _declaring_session(declared=False)])
def test_resolver_declines_without_capability_or_session(session: object | None) -> None:
    """无会话或未声明 roots capability 时维持既有 None 行为。"""
    assert _workspace_roots_dependency(cast(Any, _DependencyContext(session))) is None
