"""工作区 Roots 作用域测试：MCP Roots 优先于 env，list_roots 失败回退 env。"""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.server.mcpserver import Context
from mcp.shared.exceptions import NoBackChannelError
from mcp.types import InputRequiredResult, ListRootsRequest, ListRootsResult
from PIL import Image

import seedream_mcp.utils.io.io_path as io_path_module
import seedream_mcp.utils.io.io_roots as io_roots_module
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.server import workspace_roots_resource
from seedream_mcp.tools.core.schemas import BrowseImagesInput
from seedream_mcp.tools.runners import run_browse_images
from seedream_mcp.utils.io.io_path import get_workspace_root
from seedream_mcp.utils.io.io_roots import (
    read_session_roots_result,
    workspace_roots_scope_from_result,
)

from _log_fakes import RecordingLogger
from _roots_session_fakes import (
    CapabilityDeclaringSession as _CapabilityDeclaringSession,
)
from _roots_session_fakes import FakeSession as _FakeSession
from _roots_session_fakes import ProbingErrorSession as _ProbingErrorSession
from _roots_session_fakes import roots_result as _roots_result


class _SpyContext:
    """仅暴露 session 的最小上下文替身。"""

    def __init__(self, session: object) -> None:
        self.session = session


class _FakeContext:
    """组合固定 roots 会话的上下文替身。"""

    def __init__(self, roots: list[Path]) -> None:
        self.session = _FakeSession(roots)


class _FailingSession:
    """list_roots 抛 RuntimeError 的会话替身。"""

    async def list_roots(self) -> ListRootsResult:
        raise RuntimeError("list_roots failed")


class _FailingContext:
    """组合 list_roots 失败会话的上下文替身。"""

    def __init__(self) -> None:
        self.session = _FailingSession()


class _NoBackChannelSession:
    """list_roots 抛 NoBackChannelError，模拟无服务端反向通道的 2026 协议会话。"""

    async def list_roots(self) -> ListRootsResult:
        raise NoBackChannelError("roots/list")


class _NoBackChannelContext:
    """组合无反向通道会话的上下文替身。"""

    def __init__(self) -> None:
        self.session = _NoBackChannelSession()


class _MalformedResponseSession:
    """list_roots 抛普通 ValueError，代表瞬时失败或替身异常。"""

    async def list_roots(self) -> ListRootsResult:
        raise ValueError("malformed roots payload")


class _MalformedResponseContext:
    """组合畸形应答会话的上下文替身。"""

    def __init__(self) -> None:
        self.session = _MalformedResponseSession()


class _TimeoutSession:
    """list_roots 抛 TimeoutError 的会话替身，模拟 roots/list 瞬时超时。"""

    async def list_roots(self) -> ListRootsResult:
        raise TimeoutError("roots/list timed out")


class _TimeoutContext:
    """组合瞬时超时会话的上下文替身。"""

    def __init__(self) -> None:
        self.session = _TimeoutSession()


async def test_workspace_roots_scope_prioritizes_mcp_roots_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP Roots 在作用域内优先于环境变量根，退出后恢复。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    assert get_workspace_root() == env_root.resolve()

    async with workspace_roots_scope_from_result(_roots_result([mcp_root])):
        assert get_workspace_root() == mcp_root.resolve()

    assert get_workspace_root() == env_root.resolve()


def test_resolve_env_workspace_root_reads_global_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活动配置就绪时，resolve_env_workspace_root 读 config.workspace_root。"""
    from seedream_mcp import config as config_module
    from seedream_mcp.utils.io.io_path import resolve_env_workspace_root

    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    config = SeedreamConfig(api_key="k", workspace_root=str(tmp_path))
    monkeypatch.setattr(config_module, "_global_config", config)
    assert resolve_env_workspace_root() == tmp_path.resolve()


async def test_run_browse_images_uses_mcp_roots_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """浏览工具以 MCP Roots 为工作区：图片目录内可浏览，env 根目录被拒绝。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    images_root = mcp_root / ".seedream" / "images"
    images_root.mkdir(parents=True)
    (images_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await run_browse_images(
        BrowseImagesInput(directory=".", recursive=False),
        workspace_roots=_roots_result([mcp_root]),
    )
    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["count"] == 1

    denied = await run_browse_images(
        BrowseImagesInput(directory=str(env_root), recursive=False),
        workspace_roots=_roots_result([mcp_root]),
    )
    assert denied.is_error is True


async def test_client_prepare_image_input_prefers_mcp_roots_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """参考图预处理以 MCP Roots 为读权限，MCP 根内绝对路径可读。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()

    image_path = mcp_root / "local.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    client = SeedreamClient(SeedreamConfig(api_key="test_key"))

    async with workspace_roots_scope_from_result(_roots_result([mcp_root])):
        prepared = await client._prepare_image_input(str(image_path))

    assert prepared.startswith("data:image/")


async def test_client_prepare_image_input_allows_second_mcp_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多个 MCP Roots 全部进入读权限，第二个根内的绝对路径文件同样可读。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    first_root = tmp_path / "root_a"
    first_root.mkdir()
    second_root = tmp_path / "root_b"
    second_root.mkdir()

    image_path = second_root / "target.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    client = SeedreamClient(SeedreamConfig(api_key="test_key"))

    async with workspace_roots_scope_from_result(_roots_result([first_root, second_root])):
        prepared = await client._prepare_image_input(str(image_path))

    assert prepared.startswith("data:image/")


async def test_run_browse_images_falls_back_when_mcp_roots_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空 Roots 声明等同未声明，浏览回退环境配置根的图片目录。"""
    env_root = tmp_path / "env"
    images_root = env_root / ".seedream" / "images"
    images_root.mkdir(parents=True)
    (images_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    result = await run_browse_images(
        BrowseImagesInput(directory=".", recursive=False),
        workspace_roots=_roots_result([]),
    )
    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["count"] == 1
    # 空声明等同未声明，工作区回显按环境回退根的真实路径。
    assert result.structured_content["workspace_roots"] == [
        str(env_root.resolve()).replace("\\", "/")
    ]


async def test_client_prepare_image_input_falls_back_when_mcp_roots_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空 Roots 声明等同未声明，参考图读取回退环境配置根。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    image_path = env_root / "local.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    client = SeedreamClient(SeedreamConfig(api_key="test_key"))

    async with workspace_roots_scope_from_result(_roots_result([])):
        prepared = await client._prepare_image_input(str(image_path))

    assert prepared.startswith("data:image/")


async def test_run_browse_images_relative_directory_resolves_against_images_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相对目录以图片目录为基准解析，命中图片目录内的嵌套目录。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    images_root = mcp_root / ".seedream" / "images"
    nested_dir = images_root / "assets"
    nested_dir.mkdir(parents=True)
    (nested_dir / "from_images_root.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    result = await run_browse_images(
        BrowseImagesInput(directory="assets", recursive=False),
        workspace_roots=_roots_result([mcp_root]),
    )

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["count"] == 1
    entry_path = (nested_dir / "from_images_root.png").resolve().as_posix()
    assert result.structured_content["images"][0]["path"] == entry_path


async def test_run_browse_images_rejects_absolute_path_outside_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """绝对路径目录落在读权限（工作区 ∪ 图片目录）之外时以结构化错误拒绝。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    first_root = tmp_path / "root_a"
    first_root.mkdir()
    second_root = tmp_path / "root_b"
    second_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    result = await run_browse_images(
        BrowseImagesInput(directory=str(tmp_path), recursive=False),
        ctx=cast("Context[Any, Any]", _FakeContext([first_root, second_root])),
    )

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"


async def test_read_session_roots_result_falls_back_to_env_when_list_roots_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 失败时取回返回 None，回退环境变量根。"""
    env_root = tmp_path / "env"
    env_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    roots_result = await read_session_roots_result(_FailingContext())
    assert roots_result is None
    async with workspace_roots_scope_from_result(roots_result):
        assert get_workspace_root() == env_root.resolve()


async def test_read_session_roots_result_falls_back_to_cwd_without_env_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无反向通道且无环境变量根时回退根（进程启动目录）。"""
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_value_providers", {})
    monkeypatch.chdir(tmp_path)

    roots_result = await read_session_roots_result(_NoBackChannelContext())
    assert roots_result is None
    async with workspace_roots_scope_from_result(roots_result):
        assert get_workspace_root() == tmp_path.resolve()


async def test_read_session_roots_result_no_back_channel_logs_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无反向通道时取回返回 None，日志提级为 error 而非 warning。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    capture = RecordingLogger()
    monkeypatch.setattr(io_roots_module, "logger", capture)

    roots_result = await read_session_roots_result(_NoBackChannelContext())

    assert roots_result is None
    assert any("反向通道" in message for message in capture.errors)
    assert capture.warnings == []


async def test_read_session_roots_result_generic_error_logs_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NoBackChannelError 之外的普通异常返回 None，日志提级为 error。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    capture = RecordingLogger()
    monkeypatch.setattr(io_roots_module, "logger", capture)

    roots_result = await read_session_roots_result(_MalformedResponseContext())

    assert roots_result is None
    assert any("读取 MCP Roots 失败" in message for message in capture.errors)
    assert capture.warnings == []


async def test_read_session_roots_result_capability_probe_error_still_fetches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """capability 探测抛异常时按已声明尝试取回，不静默降级。"""
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    session = _ProbingErrorSession([mcp_root])

    roots_result = await read_session_roots_result(_SpyContext(session))
    assert roots_result is not None
    async with workspace_roots_scope_from_result(roots_result):
        assert get_workspace_root() == mcp_root.resolve()


async def test_read_session_roots_result_transient_error_falls_back_to_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 瞬时失败且无环境变量根时回退根（进程启动目录）。"""
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_value_providers", {})
    monkeypatch.chdir(tmp_path)

    roots_result = await read_session_roots_result(_TimeoutContext())
    assert roots_result is None
    async with workspace_roots_scope_from_result(roots_result):
        assert get_workspace_root() == tmp_path.resolve()


async def test_read_session_roots_result_transient_error_logs_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 瞬时失败时返回 None，日志提级为 error。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    capture = RecordingLogger()
    monkeypatch.setattr(io_roots_module, "logger", capture)

    roots_result = await read_session_roots_result(_TimeoutContext())

    assert roots_result is None
    assert any("读取 MCP Roots 失败" in message for message in capture.errors)
    assert capture.warnings == []


async def test_read_session_roots_result_skips_list_roots_without_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端未声明 roots capability 时跳过 roots/list 往返，直接回退环境变量边界。

    未声明的客户端对 roots/list 必然报方法不支持，发起往返只引入失败等待与噪音。
    """
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    session = _CapabilityDeclaringSession([], declared=False)

    async def _explode_list_roots() -> ListRootsResult:
        raise AssertionError("未声明 roots capability 时不得发起 roots/list")

    session.list_roots = _explode_list_roots  # type: ignore[method-assign]

    roots_result = await read_session_roots_result(_SpyContext(session))

    assert roots_result is None
    assert session.capability_probes == 1
    async with workspace_roots_scope_from_result(roots_result):
        assert get_workspace_root() == env_root.resolve()


async def test_read_session_roots_result_calls_list_roots_when_capability_declared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端已声明 roots capability 时照常发起 roots/list 并应用客户端边界。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    session = _CapabilityDeclaringSession([mcp_root], declared=True)

    roots_result = await read_session_roots_result(_SpyContext(session))
    assert roots_result is not None
    async with workspace_roots_scope_from_result(roots_result):
        assert get_workspace_root() == mcp_root.resolve()

    assert session.capability_probes == 1


async def test_run_browse_images_falls_back_to_env_when_list_roots_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 失败时浏览回退 env 根的图片目录，仍可浏览界内图片。"""
    env_root = tmp_path / "env"
    images_root = env_root / ".seedream" / "images"
    images_root.mkdir(parents=True)
    (images_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    result = await run_browse_images(
        BrowseImagesInput(directory=".", recursive=False),
        ctx=cast("Context[Any, Any]", _FailingContext()),
    )

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["count"] == 1


async def test_client_prepare_image_input_falls_back_to_env_when_list_roots_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 失败时参考图预处理回退 env 根，界内文件仍可读。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    image_path = env_root / "local.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    client = SeedreamClient(SeedreamConfig(api_key="test_key"))

    async with workspace_roots_scope_from_result(
        await read_session_roots_result(_FailingContext())
    ):
        prepared = await client._prepare_image_input(str(image_path))

    assert prepared.startswith("data:image/")


async def test_workspace_roots_resource_reports_client_roots_not_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端经 MCP Roots 授权时资源报告客户端 roots，而非服务器 env/cwd。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await workspace_roots_resource(cast("Context[Any, Any]", _FakeContext([mcp_root])))
    data = json.loads(cast(str, result))

    # server 资源输出统一正斜杠，比对时归一化路径分隔符。
    assert str(mcp_root.resolve()).replace("\\", "/") in data["roots"]
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]


async def test_workspace_roots_resource_empty_roots_falls_back_to_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端明确授权空列表等同未声明，资源回显环境回退根。"""
    env_root = tmp_path / "env"
    env_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await workspace_roots_resource(cast("Context[Any, Any]", _FakeContext([])))
    data = json.loads(cast(str, result))

    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]


async def test_workspace_roots_resource_capability_missing_falls_back_to_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端未声明 roots capability 时不发起取回，资源回显环境回退根。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    session = _CapabilityDeclaringSession([], declared=False)

    result = await workspace_roots_resource(cast("Context[Any, Any]", _SpyContext(session)))
    data = json.loads(cast(str, result))

    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]


async def test_workspace_roots_resource_list_roots_failure_falls_back_to_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 失败回退 env 边界时资源回显环境回退根，不带降级标记。

    取回失败时 capability 探测可能误报已声明，标记 fallback 会断言客户端
    声明过且未生效。
    """
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await workspace_roots_resource(cast("Context[Any, Any]", _FailingContext()))
    data = json.loads(cast(str, result))

    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]
    assert "fallback" not in data


class _FixedResultSession:
    """list_roots 返回固定结果的会话替身。"""

    def __init__(self, result: ListRootsResult) -> None:
        self._result = result

    async def list_roots(self) -> ListRootsResult:
        return self._result


async def test_workspace_roots_resource_legacy_unconvertible_marks_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧修订声明非空 roots 但全部不可转换时按降级渲染，与多轮形态同标 fallback。"""
    from mcp.types import Root
    from pydantic import FileUrl

    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    unconvertible = ListRootsResult(
        roots=[Root(uri=cast(Any, FileUrl("file://server/share")), name="share")]
    )

    result = await workspace_roots_resource(
        cast("Context[Any, Any]", _SpyContext(_FixedResultSession(unconvertible)))
    )

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]
    assert data["fallback"] is True


class _ModernProtocolContext:
    """2026-07-28 会话替身：协商版本为多轮形态，可携带重试轮应答。"""

    def __init__(
        self,
        roots: list[Path],
        responses: dict[str, object] | None = None,
        declared: bool = True,
        protocol_version: str = "2026-07-28",
    ) -> None:
        self.session = _CapabilityDeclaringSession(roots, declared=declared)
        self.protocol_version = protocol_version
        self.input_responses = responses


class _VersionlessModernContext(_ModernProtocolContext):
    """缺省 protocol_version 的鸭子类型替身：非 str 形态按旧修订回退直连。"""

    def __init__(self, roots: list[Path], declared: bool = True) -> None:
        super().__init__(roots, declared=declared)
        del self.protocol_version


async def test_workspace_roots_resource_modern_session_first_round_requests_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026 会话首轮返回 InputRequiredResult 携带 roots 请求，不经直连取回。

    2026 会话无反向通道，直连 roots/list 必抛 NoBackChannelError；首轮无应答时
    input_requests 按约定键携带 ListRootsRequest。
    """
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    ctx = _ModernProtocolContext([mcp_root])

    result = await workspace_roots_resource(cast("Context[Any, Any]", ctx))

    assert isinstance(result, InputRequiredResult)
    requests = cast("dict[str, object]", result.input_requests)
    assert set(requests) == {"roots"}
    assert isinstance(requests["roots"], ListRootsRequest)
    # 首轮不得退回 roots/list 直连：多轮形态下直连在该版本会话上必然失败。
    assert ctx.session.list_roots_calls == 0


async def test_workspace_roots_resource_modern_session_retry_round_reports_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026 会话重试轮从 input_responses 取回 roots，输出客户端授权根目录。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    ctx = _ModernProtocolContext([mcp_root], responses={"roots": _roots_result([mcp_root])})

    result = await workspace_roots_resource(cast("Context[Any, Any]", ctx))

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(mcp_root.resolve()).replace("\\", "/")]
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]
    # 应答已就位时不发起多轮请求，也不经直连取回。
    assert ctx.session.list_roots_calls == 0


async def test_workspace_roots_resource_modern_session_malformed_response_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试轮应答形态异常时丢弃该轮应答并回退环境根，避免重发空转到回合上限。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    ctx = _ModernProtocolContext([mcp_root], responses={"roots": "not-a-roots-result"})

    result = await workspace_roots_resource(cast("Context[Any, Any]", ctx))

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]
    # 输出附降级标记，客户端可感知本工作区为环境回退而非其声明值。
    assert data["fallback"] is True
    assert ctx.session.list_roots_calls == 0


async def test_workspace_roots_resource_modern_session_capability_missing_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026 会话但未声明 roots capability 时不发起多轮请求，回显环境回退根。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    ctx = _ModernProtocolContext([], declared=False)

    result = await workspace_roots_resource(cast("Context[Any, Any]", ctx))

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]


async def test_workspace_roots_resource_versionless_context_keeps_direct_fetch(
    tmp_path: Path,
) -> None:
    """protocol_version 缺省或非 str 时按旧修订回退直连，不误入多轮形态。

    版本判定守卫被移除时 is_version_at_least(None) 抛 TypeError，或令旧修订会话
    收到无法序列化的 InputRequiredResult。
    """
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    ctx = _VersionlessModernContext([mcp_root])

    result = await workspace_roots_resource(cast("Context[Any, Any]", ctx))

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(mcp_root.resolve()).replace("\\", "/")]
    assert ctx.session.list_roots_calls == 1


async def test_workspace_roots_resource_declared_but_unconvertible_marks_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """声明的 roots 全部不可转换为本地路径时按降级渲染，附 fallback 标记。"""
    from mcp.types import Root
    from pydantic import FileUrl

    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    # UNC 形态的 file URI 被 _file_uri_to_path 拒绝，声明的根全部无法落地。
    unconvertible = ListRootsResult(
        roots=[Root(uri=cast(Any, FileUrl("file://server/share")), name="share")]
    )
    ctx = _ModernProtocolContext([], responses={"roots": unconvertible})

    result = await workspace_roots_resource(cast("Context[Any, Any]", ctx))

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]
    assert data["fallback"] is True


async def test_workspace_roots_resource_modern_round_empty_roots_falls_back_to_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多轮重试轮应答空 roots 时与未授权同语义，回显环境回退根。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    ctx = _ModernProtocolContext([], responses={"roots": _roots_result([])})

    result = await workspace_roots_resource(cast("Context[Any, Any]", ctx))

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]


async def test_workspace_roots_resource_legacy_version_keeps_direct_fetch(
    tmp_path: Path,
) -> None:
    """旧修订版本即使声明 capability 也保持 roots/list 直连，不走多轮形态。

    InputRequiredResult 仅存在于 2026-07-28 及以后，旧修订客户端收到会报 -32603。
    """
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    ctx = _ModernProtocolContext([mcp_root], protocol_version="2025-11-25")

    result = await workspace_roots_resource(cast("Context[Any, Any]", ctx))

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(mcp_root.resolve()).replace("\\", "/")]
    assert ctx.session.list_roots_calls == 1


class _NoSessionContext:
    """无请求上下文的 Context 替身：session 属性抛 ValueError，对齐 SDK 真实形态。"""

    @property
    def session(self) -> object:
        raise ValueError("Context is not available outside of a request")


async def test_workspace_roots_scope_without_request_context_falls_back_to_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无请求上下文的资源读取回退环境变量边界，不被裸 ValueError 击穿。

    Context.session 的 property 在无请求上下文时抛 ValueError，回退分支即为此
    状态而设。
    """
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await workspace_roots_resource(cast("Context[Any, Any]", _NoSessionContext()))

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(env_root.resolve()).replace("\\", "/")]
