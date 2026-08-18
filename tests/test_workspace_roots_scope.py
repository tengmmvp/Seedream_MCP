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
    def __init__(self, roots: list[Path]) -> None:
        self.session = _FakeSession(roots)


class _FailingSession:
    async def list_roots(self) -> ListRootsResult:
        raise RuntimeError("list_roots failed")


class _FailingContext:
    def __init__(self) -> None:
        self.session = _FailingSession()


class _NoBackChannelSession:
    """list_roots 抛 NoBackChannelError，模拟无服务端反向通道的 2026 协议会话。"""

    async def list_roots(self) -> ListRootsResult:
        raise NoBackChannelError("roots/list")


class _NoBackChannelContext:
    def __init__(self) -> None:
        self.session = _NoBackChannelSession()


class _MalformedResponseSession:
    """list_roots 抛普通 ValueError，代表瞬时失败或替身异常。"""

    async def list_roots(self) -> ListRootsResult:
        raise ValueError("malformed roots payload")


class _MalformedResponseContext:
    def __init__(self) -> None:
        self.session = _MalformedResponseSession()


class _LevelCaptureLogger:
    """替身 logger，分别收集 error 与 warning 消息，供断言日志级别。"""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str, *args: object) -> None:
        self.errors.append(message.format(*args) if args else message)

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message.format(*args) if args else message)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_workspace_roots_scope_falls_back_to_env_when_list_roots_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    async with workspace_roots_scope(_FailingContext()):
        assert get_workspace_root() == env_root.resolve()


@pytest.mark.asyncio
async def test_workspace_roots_scope_fails_closed_on_no_back_channel_without_env_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无反向通道且无环境变量根时 fail-closed 抛错，不放宽边界到进程 CWD。"""
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_workspace_root_provider", lambda: None)

    with pytest.raises(SeedreamMCPError, match="SEEDREAM_WORKSPACE_ROOT"):
        async with workspace_roots_scope(_NoBackChannelContext()):
            raise AssertionError("无反向通道且无环境变量根时不得进入作用域")


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_workspace_roots_scope_warns_and_falls_back_on_generic_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NoBackChannelError 之外的普通异常维持 warning 级回退，不 fail-closed。"""
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    capture = _LevelCaptureLogger()
    monkeypatch.setattr(io_path_module, "logger", capture)

    async with workspace_roots_scope(_MalformedResponseContext()):
        assert get_workspace_root() == env_root.resolve()

    assert any("读取 MCP Roots 失败" in message for message in capture.warnings)
    assert capture.errors == []


@pytest.mark.asyncio
async def test_workspace_roots_scope_skips_list_roots_without_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端未声明 roots capability 时跳过 roots/list 往返，直接回退环境变量边界。

    未声明 roots 的客户端对 roots/list 必然报方法不支持，逐请求发起往返只会引入
    失败等待与告警噪音；以会话内存中的 capability 声明即可短路。
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_workspace_roots_resource_reports_client_roots_not_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 客户端通过 MCP Roots 授权目录时，seedream://workspace/roots 须报告客户端 roots，
    # 而非服务器 env/cwd，与浏览工具访问边界一致。
    env_root = tmp_path / "env"
    env_root.mkdir()
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await workspace_roots_resource(_FakeContext([mcp_root]))
    data = json.loads(result)

    # server 资源输出统一正斜杠，比对时归一化路径分隔符
    assert str(mcp_root.resolve()).replace("\\", "/") in data["roots"]
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]


@pytest.mark.asyncio
async def test_workspace_roots_resource_empty_roots_does_not_leak_server_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 客户端明确不授权任何目录即 list_roots 返回空时，资源须返回空列表，
    # 不得回退到 env/cwd 暴露服务器本地目录。
    env_root = tmp_path / "env"
    env_root.mkdir()

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    result = await workspace_roots_resource(_FakeContext([]))
    data = json.loads(result)

    assert data["roots"] == []
    assert str(env_root.resolve()) not in data["roots"]


@pytest.mark.asyncio
async def test_workspace_roots_resource_capability_missing_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端未声明 roots capability 时资源输出空列表，不回退暴露 env 根。

    未声明 capability 时 scope 跳过 roots/list，边界回退环境变量根；回退根属
    服务器环境而非客户端授权声明，其绝对路径不得进入面向调用方的输出。
    """
    env_root = tmp_path / "env"
    env_root.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))
    session = _CapabilityDeclaringSession([], declared=False)

    result = await workspace_roots_resource(_SpyContext(session))
    data = json.loads(result)

    assert data["roots"] == []
    assert str(env_root.resolve()).replace("\\", "/") not in data["roots"]


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_workspace_roots_resource_modern_session_first_round_requests_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026 会话首轮返回 InputRequiredResult 携带 roots 请求，不经直连取回。

    SEP-2577 下 2026 会话无服务端反向通道，直连 roots/list 必抛 NoBackChannelError
    且触发废弃告警；资源侧改为返回多轮请求，由客户端应答后重试。首轮无应答时
    返回 InputRequiredResult，input_requests 按约定键携带 ListRootsRequest。
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_workspace_roots_resource_legacy_version_keeps_direct_fetch(
    tmp_path: Path,
) -> None:
    """旧修订版本即使声明 capability 也保持 roots/list 直连，不走多轮形态。

    InputRequiredResult 仅存在于 2026-07-28 及以后，旧修订会话返回该类型客户端
    会收到 -32603；legacy 直连是旧修订上唯一取回途径。
    """
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    ctx = _ModernProtocolContext([mcp_root], protocol_version="2025-11-25")

    result = await workspace_roots_resource(ctx)

    assert isinstance(result, str)
    data = json.loads(result)
    assert data["roots"] == [str(mcp_root.resolve()).replace("\\", "/")]
    assert ctx.session.list_roots_calls == 1
