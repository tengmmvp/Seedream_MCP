from pathlib import Path

import pytest
from mcp.types import ListRootsResult, Root
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.errors import SeedreamAPIError
from seedream_mcp.tools.core.runners import run_browse_images
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
