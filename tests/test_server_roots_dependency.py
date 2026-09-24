"""工具链 roots 依赖解析器测试：旧修订请求作用域直连取回，失败报错不挂起。"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resolve import ListRoots
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT, ListRootsRequest, ListRootsResult

import seedream_mcp.utils.io.io_roots as io_roots_module
from seedream_mcp.server import _workspace_roots_dependency
from seedream_mcp.utils.io.io_roots import read_session_roots_or_raise

from _roots_session_fakes import CapabilityDeclaringSession as _CapabilityDeclaringSession
from _roots_session_fakes import FakeSession as _FakeSession
from _roots_session_fakes import FailingSession as _FailingSession
from _roots_session_fakes import HangingSession as _HangingSession


class _DependencyContext:
    """仅暴露 session、可选协商版本与可选请求 id 的上下文替身。"""

    def __init__(
        self,
        session: object,
        protocol_version: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.session = session
        if protocol_version is not None:
            self.protocol_version = protocol_version
        if request_id is not None:
            self.request_id = request_id


class _BackChannelSession(_CapabilityDeclaringSession):
    """带固定反向通道探测结果的会话替身。"""

    def __init__(self, roots: list[Path], can_send_request: bool) -> None:
        super().__init__(roots, declared=True)
        self.can_send_request = can_send_request


def _declaring_session(declared: bool = True) -> _CapabilityDeclaringSession:
    return _CapabilityDeclaringSession([Path("/workspace")], declared)


async def test_resolver_uses_multi_round_over_modern_revision_without_back_channel() -> None:
    """2026-07-28 及以后的取回经 InputRequiredResult 多轮形态，不依赖反向通道。"""
    session = _BackChannelSession([Path("/workspace")], False)
    ctx = _DependencyContext(session, protocol_version="2026-07-28")

    result = await _workspace_roots_dependency(cast(Any, ctx))

    assert isinstance(result, ListRoots)


async def test_resolver_fetches_directly_when_back_channel_available(tmp_path: Path) -> None:
    """旧修订且反向通道可用时由 resolver 直连取回，返回客户端 roots 结果。

    送达绑定请求自身通道（related_request_id），SSE 客户端无常驻 GET 流时
    roots/list 不再因连接级 outbound 被丢弃；读超时随请求下发给 SDK 承担。
    """
    root = tmp_path / "workspace"
    session = _BackChannelSession([root], True)

    result = await _workspace_roots_dependency(
        cast(Any, _DependencyContext(session, request_id="req-42"))
    )

    assert isinstance(result, ListRootsResult)
    assert [item.name for item in result.roots] == ["workspace"]
    calls = session.send_request_calls
    assert len(calls) == 1
    assert isinstance(calls[0]["request"], ListRootsRequest)
    assert calls[0]["request_read_timeout_seconds"] == 5
    metadata = calls[0]["metadata"]
    assert metadata is not None
    assert metadata.related_request_id == "req-42"


async def test_resolver_declines_when_back_channel_unavailable() -> None:
    """无状态传输等无反向通道场景返回 None，SDK 不发起必失败的取回。

    此前该组合下 SDK 抛 NoBackChannelError 序列化为 -32600，五个工具全部不可用。
    """
    session = _BackChannelSession([Path("/workspace")], False)

    result = await _workspace_roots_dependency(cast(Any, _DependencyContext(session)))

    assert result is None


async def test_resolver_treats_missing_probe_as_available(tmp_path: Path) -> None:
    """会话无反向通道属性时保守视为可用，直连取回客户端 roots。"""
    session = _CapabilityDeclaringSession([tmp_path / "workspace"], True)

    result = await _workspace_roots_dependency(cast(Any, _DependencyContext(session)))

    assert isinstance(result, ListRootsResult)


async def test_resolver_reaches_request_without_list_roots_method(tmp_path: Path) -> None:
    """会话无 list_roots 方法仅 send_request 可用时取回仍可达。"""
    session = _BackChannelSession([tmp_path / "workspace"], True)

    result = await read_session_roots_or_raise(
        cast(Any, _DependencyContext(session, request_id="req-7")), session
    )

    assert isinstance(result, ListRootsResult)
    assert len(session.send_request_calls) == 1


async def test_resolver_omits_metadata_when_request_id_unavailable(tmp_path: Path) -> None:
    """取不到 request_id 时 metadata 按 SDK 缺省不传，读超时保持下发。"""
    session = _BackChannelSession([tmp_path / "workspace"], True)

    await read_session_roots_or_raise(cast(Any, _DependencyContext(session)), session)

    call = session.send_request_calls[0]
    assert call["metadata"] is None
    assert call["request_read_timeout_seconds"] == 5


class _SilentSession(_CapabilityDeclaringSession):
    """声明 roots capability 但永不应答 roots/list 的会话替身。"""

    def __init__(self) -> None:
        super().__init__([Path("/workspace")], declared=True)
        self.can_send_request = True

    async def _conclude_send_request(
        self, request_read_timeout_seconds: float | None
    ) -> ListRootsResult:
        # 模拟 SDK 读超时契约：静默客户端在超时上限后以 REQUEST_TIMEOUT 收尾。
        await asyncio.sleep(request_read_timeout_seconds or 3600)
        raise MCPError(code=REQUEST_TIMEOUT, message="Request 'roots/list' timed out")


async def test_resolver_fails_within_timeout_when_client_never_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """声明 roots 但永不应答的旧修订会话，取回在超时上限内报错而非挂起。"""
    monkeypatch.setattr(io_roots_module, "_ROOTS_LIST_TIMEOUT_SECONDS", 0.1)

    with pytest.raises(ToolError, match="读取 MCP Roots 超时"):
        await asyncio.wait_for(
            _workspace_roots_dependency(cast(Any, _DependencyContext(_SilentSession()))),
            timeout=5.0,
        )


async def test_resolver_fails_within_timeout_when_send_request_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """出站写阶段悬挂时整体超时上界兜底，工具路径在限内报超时而非挂起。"""
    monkeypatch.setattr(io_roots_module, "_ROOTS_LIST_TIMEOUT_SECONDS", 0.1)

    with pytest.raises(ToolError, match="读取 MCP Roots 超时"):
        await asyncio.wait_for(
            _workspace_roots_dependency(cast(Any, _DependencyContext(_HangingSession()))),
            timeout=5.0,
        )


async def test_resolver_reports_error_when_fetch_fails() -> None:
    """旧修订直连取回失败时经 ToolError 向调用方报错，不静默回退。"""
    with pytest.raises(ToolError, match="读取 MCP Roots 失败"):
        await _workspace_roots_dependency(cast(Any, _DependencyContext(_FailingSession())))


def _class_body_binds_send_request(node: ast.ClassDef) -> bool:
    """判定类体是否自行绑定 send_request 名字，方法定义与赋值形态都覆盖。"""
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name == "send_request":
                return True
        if isinstance(item, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "send_request" for target in item.targets
        ):
            return True
        if isinstance(item, ast.AnnAssign):
            target = item.target
            if isinstance(target, ast.Name) and target.id == "send_request":
                return True
    return False


def test_session_stands_inject_outcome_without_redeclaring_send_request() -> None:
    """会话替身的差异只经结局钩子注入，send_request 样板只允许共享基类定义。"""
    assert "send_request" in vars(_FakeSession)
    tests_root = Path(__file__).resolve().parent
    offenders: list[str] = []
    for source_path in sorted(tests_root.glob("*.py")):
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef) or not _class_body_binds_send_request(node):
                continue
            if source_path.name == "_roots_session_fakes.py" and node.name == "FakeSession":
                continue
            offenders.append(f"{source_path.name}:{node.name}")

    assert offenders == [], (
        "替身类自行定义 send_request，改为继承 FakeSession 并覆写 _conclude_send_request: "
        f"{offenders}"
    )


class _DetachedContext:
    """session 属性在脱离请求上下文访问时抛 ValueError 的替身。"""

    @property
    def session(self) -> Any:
        raise ValueError("Attempted to access request context in a detached context.")


@pytest.mark.parametrize("session", [None, _declaring_session(declared=False)])
async def test_resolver_declines_without_capability_or_session(session: object | None) -> None:
    """无会话或未声明 roots capability 时维持既有 None 行为。"""
    assert await _workspace_roots_dependency(cast(Any, _DependencyContext(session))) is None


async def test_resolver_treats_detached_session_value_error_as_no_session() -> None:
    """脱离请求上下文访问 session 抛 ValueError 时按无会话处置返回 None。"""
    assert await _workspace_roots_dependency(cast(Any, _DetachedContext())) is None
