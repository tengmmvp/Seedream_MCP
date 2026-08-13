"""工作区 Roots 作用域测试：MCP Roots 优先于 env，list_roots 失败回退 env。"""

import json
from pathlib import Path

import pytest
from mcp.types import ListRootsResult, Root
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.server import mcp, workspace_roots_resource
from seedream_mcp.utils.errors import SeedreamAPIError
from seedream_mcp.tools.runners import run_browse_images
from seedream_mcp.tools.core.schemas import BrowseImagesInput
from seedream_mcp.utils.path_utils import get_workspace_root, workspace_roots_scope


class _FakeSession:
    def __init__(self, roots: list[Path]) -> None:
        self._roots = roots

    async def list_roots(self) -> ListRootsResult:
        return ListRootsResult(
            roots=[Root(uri=root.as_uri(), name=root.name) for root in self._roots]
        )


class _FakeContext:
    def __init__(self, roots: list[Path]) -> None:
        self.session = _FakeSession(roots)


class _FailingSession:
    async def list_roots(self) -> ListRootsResult:
        raise RuntimeError("list_roots failed")


class _FailingContext:
    def __init__(self) -> None:
        self.session = _FailingSession()


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
    from seedream_mcp.utils.path_utils import resolve_env_workspace_root

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
        ctx=_FakeContext([mcp_root]),
    )
    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["count"] == 1

    denied = await run_browse_images(
        BrowseImagesInput(directory=str(env_root), recursive=False),
        ctx=_FakeContext([mcp_root]),
    )
    assert denied.isError is True


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
        ctx=_FakeContext([]),
    )
    assert result.isError is True
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["workspace_roots"] == []


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
        with pytest.raises(SeedreamAPIError, match="未授权任何工作区目录"):
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
        ctx=_FakeContext([first_root, second_root]),
    )

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["count"] == 1
    assert Path(result.structuredContent["images"][0]["path"]) == Path("assets/from_second.png")


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

    assert result.isError is True
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "failed"


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

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["count"] == 1


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
    monkeypatch.setattr(mcp, "get_context", lambda: _FakeContext([mcp_root]))

    result = await workspace_roots_resource()
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
    monkeypatch.setattr(mcp, "get_context", lambda: _FakeContext([]))

    result = await workspace_roots_resource()
    data = json.loads(result)

    assert data["roots"] == []
    assert str(env_root.resolve()) not in data["roots"]
