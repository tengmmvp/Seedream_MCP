from pathlib import Path

import pytest
from mcp.types import TextContent

from seedream_mcp.tools.impl import browse_images as browse_images_module
from seedream_mcp.tools.impl.browse_images import handle_browse_images


@pytest.mark.asyncio
async def test_browse_images_returns_structured_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images({"directory": ".", "recursive": False})

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "completed"
    assert result.structuredContent["count"] == 1
    assert any(isinstance(content, TextContent) for content in result.content)


@pytest.mark.asyncio
async def test_browse_images_returns_empty_when_no_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))

    result = await handle_browse_images({"directory": ".", "recursive": False})

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "empty"
    assert result.structuredContent["count"] == 0


@pytest.mark.asyncio
async def test_browse_images_rejects_out_of_workspace_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    outside_dir = tmp_path.parent / "outside_dir_for_test"
    outside_dir.mkdir(exist_ok=True)

    result = await handle_browse_images({"directory": str(outside_dir)})

    assert result.isError is True
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "failed"


@pytest.mark.asyncio
async def test_browse_images_ignores_outside_images_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_img = outside / "outside.png"
    outside_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    def _fake_find_images_in_directory(*args, **kwargs):
        return [outside_img]

    monkeypatch.setattr(
        browse_images_module,
        "find_images_in_directory",
        _fake_find_images_in_directory,
    )

    result = await handle_browse_images({"directory": ".", "recursive": True})

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "empty"
    assert result.structuredContent["count"] == 0
