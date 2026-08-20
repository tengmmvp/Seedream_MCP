"""工作区 Roots 作用域测试：MCP Roots 优先于 env，list_roots 失败回退 env。"""

import json
from pathlib import Path

import pytest
from mcp.shared.exceptions import NoBackChannelError
from mcp.types import InputRequiredResult, ListRootsRequest, ListRootsResult, Root
from PIL import Image

import seedream_mcp.utils.io.io_path as io_path_module
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.server import workspace_roots_resource
from seedream_mcp.utils.core.errors import SeedreamConfigError, SeedreamMCPError
from seedream_mcp.tools.runners import run_browse_images
from seedream_mcp.tools.core.schemas import BrowseImagesInput
from seedream_mcp.utils.io.io_path import get_workspace_root, workspace_roots_scope


class _FakeSession:
    """以固定根目录应答 list_roots 的会话替身。"""

    def __init__(self, roots: list[Path]) -> None:
        self._roots = roots

    async def list_roots(self) -> ListRootsResult:
        return _roots_result(self._roots)


def _roots_result(roots: list[Path]) -> ListRootsResult:
    """构造工具链 resolver 注入形态的 roots 结果。"""
    return ListRootsResult(roots=[Root(uri=root.as_uri(), name=root.name) for root in roots])


class _CapabilityDeclaringSession(_FakeSession):
    """带 capability 探测的会话替身：check_client_capability 返回固定声明结果。"""

    def __init__(self, roots: list[Path], declared: bool) -> None:
        super().__init__(roots)
        self.declared = declared
        self.capability_probes = 0
        self.list_roots_calls = 0

    def check_client_capability(self, capability: object) -> bool:
        self.capability_probes += 1
        return self.declared

    async def list_roots(self) -> ListRootsResult:
        self.list_roots_calls += 1
        return await super().list_roots()


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


class _LevelCaptureLogger:
    """替身 logger，分别收集 error 与 warning 消息，供断言日志级别。"""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str, *args: object) -> None:
        self.errors.append(message.format(*args) if args else message)

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message.format(*args) if args else message)


async def test_workspace_roots_scope_prioritizes_mcp_roots_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    assert get_workspace_root() == env_root.resolve()

    async with workspace_roots_scope(_FakeContext([mcp_root])):
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
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    (mcp_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

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
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()

    image_path = mcp_root / "local.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    client = SeedreamClient(SeedreamConfig(api_key="test_key"))

    async with workspace_roots_scope(_FakeContext([mcp_root])):
        prepared = await client._prepare_image_input("local.png")

    assert prepared.startswith("data:image/")


async def test_client_prepare_image_input_allows_second_mcp_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    async with workspace_roots_scope(_FakeContext([first_root, second_root])):
        prepared = await client._prepare_image_input("target.png")

    assert prepared.startswith("data:image/")


async def test_run_browse_images_denies_when_mcp_roots_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()
    (env_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    result = await run_browse_images(
        BrowseImagesInput(directory=".", recursive=False),
        workspace_roots=_roots_result([]),
    )
    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["workspace_roots"] == []


async def test_client_prepare_image_input_denies_when_mcp_roots_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()
    image_path = env_root / "local.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    client = SeedreamClient(SeedreamConfig(api_key="test_key"))

    async with workspace_roots_scope(_FakeContext([])):
        with pytest.raises(SeedreamConfigError, match="未授权任何工作区目录"):
            await client._prepare_image_input("local.png")


async def test_run_browse_images_relative_directory_resolves_all_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()
    first_root = tmp_path / "root_a"
    first_root.mkdir()
    second_root = tmp_path / "root_b"
    second_root.mkdir()
    nested_dir = second_root / "assets"
    nested_dir.mkdir()
    (nested_dir / "from_second.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    result = await run_browse_images(
        BrowseImagesInput(directory="assets", recursive=False),
        workspace_roots=_roots_result([first_root, second_root]),
    )

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["count"] == 1
    assert Path(result.structured_content["images"][0]["path"]) == Path("assets/from_second.png")


async def test_run_browse_images_rejects_parent_escape_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()
    first_root = tmp_path / "root_a"
    first_root.mkdir()
    second_root = tmp_path / "root_b"
    second_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    result = await run_browse_images(
        BrowseImagesInput(directory="..", recursive=False),
        ctx=_FakeContext([first_root, second_root]),
    )

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"


async def test_workspace_roots_scope_falls_back_to_env_when_list_roots_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    async with workspace_roots_scope(_FailingContext()):
        assert get_workspace_root() == env_root.resolve()


async def test_workspace_roots_scope_fails_closed_on_no_back_channel_without_env_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无反向通道且无环境变量根时 fail-closed 抛错，不放宽边界到进程 CWD。"""
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_workspace_root_provider", lambda: None)

    with pytest.raises(SeedreamMCPError, match="SEEDREAM_WORKSPACE_ROOT"):
        async with workspace_roots_scope(_NoBackChannelContext()):
            raise AssertionError("无反向通道且无环境变量根时不得进入作用域")


async def test_workspace_roots_scope_no_back_channel_falls_back_to_env_root_with_error_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无反向通道但已配置环境变量根时回退该根，且日志提级为 error 而非 warning。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    capture = _LevelCaptureLogger()
    monkeypatch.setattr(io_path_module, "logger", capture)

    async with workspace_roots_scope(_NoBackChannelContext()):
        assert get_workspace_root() == env_root.resolve()

    assert any("反向通道" in message for message in capture.errors)
    assert capture.warnings == []


async def test_workspace_roots_scope_errors_and_falls_back_on_generic_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NoBackChannelError 之外的普通异常在已配置环境变量根时回退该根，日志提级为 error。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    capture = _LevelCaptureLogger()
    monkeypatch.setattr(io_path_module, "logger", capture)

    async with workspace_roots_scope(_MalformedResponseContext()):
        assert get_workspace_root() == env_root.resolve()

    assert any("读取 MCP Roots 失败" in message for message in capture.errors)
    assert capture.warnings == []


async def test_workspace_roots_scope_fails_closed_on_transient_error_without_env_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 瞬时失败且无环境变量根时与无反向通道同判定 fail-closed。

    瞬时失败回退环境变量边界而未配置根时会放宽到进程 CWD，与 NoBackChannelError
    分支同一风险形态，不得因失败可重试而放宽边界。
    """
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_workspace_root_provider", lambda: None)

    with pytest.raises(SeedreamMCPError, match="SEEDREAM_WORKSPACE_ROOT"):
        async with workspace_roots_scope(_TimeoutContext()):
            raise AssertionError("瞬时失败且无环境变量根时不得进入作用域")


async def test_workspace_roots_scope_transient_error_falls_back_to_env_root_with_error_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 瞬时失败但已配置环境变量根时回退该显式边界，日志提级为 error。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    capture = _LevelCaptureLogger()
    monkeypatch.setattr(io_path_module, "logger", capture)

    async with workspace_roots_scope(_TimeoutContext()):
        assert get_workspace_root() == env_root.resolve()

    assert any("读取 MCP Roots 失败" in message for message in capture.errors)
    assert capture.warnings == []


async def test_workspace_roots_scope_skips_list_roots_without_capability(
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

    async with workspace_roots_scope(_SpyContext(session)):
        assert get_workspace_root() == env_root.resolve()

    assert session.capability_probes == 1


async def test_workspace_roots_scope_calls_list_roots_when_capability_declared(
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

    async with workspace_roots_scope(_SpyContext(session)):
        assert get_workspace_root() == mcp_root.resolve()

    assert session.capability_probes == 1


async def test_run_browse_images_falls_back_to_env_when_list_roots_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()
    (env_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    result = await run_browse_images(
        BrowseImagesInput(directory=".", recursive=False),
        ctx=_FailingContext(),
    )

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["count"] == 1


async def test_client_prepare_image_input_falls_back_to_env_when_list_roots_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()
    image_path = env_root / "local.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    client = SeedreamClient(SeedreamConfig(api_key="test_key"))

    async with workspace_roots_scope(_FailingContext()):
        prepared = await client._prepare_image_input("local.png")

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

    result = await workspace_roots_resource(_FakeContext([mcp_root]))
    data = json.loads(result)

    # server 资源输出统一正斜杠，比对时归一化路径分隔符。
    assert str(mcp_root.resolve()).replace("\\", "/") in data["roots"]
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]


async def test_workspace_roots_resource_empty_roots_does_not_leak_server_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端明确授权空列表时资源返回空列表，不回退 env/cwd 暴露服务器目录。"""
    env_root = tmp_path / "env"
    env_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await workspace_roots_resource(_FakeContext([]))
    data = json.loads(result)

    assert data["roots"] == []
    assert str(env_root.resolve()) not in data["roots"]


async def test_workspace_roots_resource_capability_missing_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端未声明 roots capability 时资源输出空列表，不回退暴露 env 根。

    回退根属服务器环境而非客户端授权声明，其绝对路径不得进入面向调用方的输出。
    """
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    session = _CapabilityDeclaringSession([], declared=False)

    result = await workspace_roots_resource(_SpyContext(session))
    data = json.loads(result)

    assert data["roots"] == []
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]


async def test_workspace_roots_resource_list_roots_failure_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """roots/list 失败回退 env 边界时资源输出空列表，不暴露服务器 env 根。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await workspace_roots_resource(_FailingContext())
    data = json.loads(result)

    assert data["roots"] == []
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]


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

    result = await workspace_roots_resource(ctx)

    assert isinstance(result, InputRequiredResult)
    assert set(result.input_requests) == {"roots"}
    assert isinstance(result.input_requests["roots"], ListRootsRequest)
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

    result = await workspace_roots_resource(ctx)

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(mcp_root.resolve()).replace("\\", "/")]
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]
    # 应答已就位时不发起多轮请求，也不经直连取回。
    assert ctx.session.list_roots_calls == 0


async def test_workspace_roots_resource_modern_session_malformed_response_asks_again(
    tmp_path: Path,
) -> None:
    """重试轮应答形态异常时再次返回 InputRequiredResult，不落到直连或环境回退。"""
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    ctx = _ModernProtocolContext([mcp_root], responses={"roots": "not-a-roots-result"})

    result = await workspace_roots_resource(ctx)

    assert isinstance(result, InputRequiredResult)
    assert set(result.input_requests) == {"roots"}
    assert ctx.session.list_roots_calls == 0


async def test_workspace_roots_resource_modern_session_capability_missing_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026 会话但未声明 roots capability 时不发起多轮请求，回退空列表输出。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    ctx = _ModernProtocolContext([], declared=False)

    result = await workspace_roots_resource(ctx)

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == []
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]


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

    result = await workspace_roots_resource(ctx)

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(mcp_root.resolve()).replace("\\", "/")]
    assert ctx.session.list_roots_calls == 1


async def test_workspace_roots_resource_modern_round_empty_roots_not_leak_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多轮重试轮应答空 roots 时输出空列表，与未授权同语义，不回退暴露环境根。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    ctx = _ModernProtocolContext([], responses={"roots": _roots_result([])})

    result = await workspace_roots_resource(ctx)

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == []
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]


async def test_workspace_roots_resource_legacy_version_keeps_direct_fetch(
    tmp_path: Path,
) -> None:
    """旧修订版本即使声明 capability 也保持 roots/list 直连，不走多轮形态。

    InputRequiredResult 仅存在于 2026-07-28 及以后，旧修订客户端收到会报 -32603。
    """
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    ctx = _ModernProtocolContext([mcp_root], protocol_version="2025-11-25")

    result = await workspace_roots_resource(ctx)

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(mcp_root.resolve()).replace("\\", "/")]
    assert ctx.session.list_roots_calls == 1


class _NoSessionContext:
    """无请求上下文的 Context 替身：session 属性抛 ValueError（SDK 真实形态）。"""

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

    result = await workspace_roots_resource(_NoSessionContext())

    assert isinstance(result, str)
    data = json.loads(result)
    # 回退边界属服务器环境，不向调用方回显绝对路径，输出空 roots。
    assert data["roots"] == []
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]
