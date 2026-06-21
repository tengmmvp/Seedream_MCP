from pathlib import Path

import pytest
from mcp.types import TextContent
from pydantic import ValidationError

from seedream_mcp.tools import BrowseImagesInput
from seedream_mcp.tools.impl import browse_images as browse_images_module
from seedream_mcp.tools.impl.browse_images import handle_browse_images


@pytest.mark.asyncio
async def test_browse_images_returns_structured_success(workspace_root: Path) -> None:
    (workspace_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images({"directory": ".", "recursive": False})

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "completed"
    assert result.structuredContent["count"] == 1
    assert any(isinstance(content, TextContent) for content in result.content)


@pytest.mark.asyncio
async def test_browse_images_returns_empty_when_no_files(workspace_root: Path) -> None:
    result = await handle_browse_images({"directory": ".", "recursive": False})

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "empty"
    assert result.structuredContent["count"] == 0


@pytest.mark.asyncio
async def test_browse_images_rejects_out_of_workspace_directory(
    workspace_root: Path,
) -> None:
    outside_dir = workspace_root.parent / "outside_dir_for_test"
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


@pytest.mark.asyncio
async def test_browse_images_pagination_metadata(workspace_root: Path) -> None:
    for name in ("a.png", "b.png", "c.png"):
        (workspace_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    page1 = await handle_browse_images(
        {"directory": ".", "recursive": False, "limit": 2, "offset": 0}
    )
    sc1 = page1.structuredContent
    assert isinstance(sc1, dict)
    assert sc1["count"] == 2
    assert sc1["total_count"] is None  # has_more 时未扫完，total_count 不精确
    assert sc1["has_more"] is True
    assert sc1["next_offset"] == 2

    page2 = await handle_browse_images(
        {"directory": ".", "recursive": False, "limit": 2, "offset": 2}
    )
    sc2 = page2.structuredContent
    assert isinstance(sc2, dict)
    assert sc2["count"] == 1
    assert sc2["total_count"] == 3
    assert sc2["has_more"] is False
    assert sc2["next_offset"] is None


@pytest.mark.asyncio
async def test_browse_images_offset_beyond_end_keeps_total_count(
    workspace_root: Path,
) -> None:
    # offset 越过最后一页：当前页为空，但 total_count 必须反映实际匹配数
    for name in ("a.png", "b.png", "c.png"):
        (workspace_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(
        {"directory": ".", "recursive": False, "limit": 2, "offset": 4}
    )
    sc = result.structuredContent
    assert isinstance(sc, dict)
    assert sc["status"] == "empty"
    assert sc["count"] == 0
    assert sc["total_count"] == 3
    assert sc["has_more"] is False
    assert sc["next_offset"] is None


def test_browse_images_input_rejects_oversized_offset() -> None:
    """offset 超上限应被 pydantic 拒绝，防止无界偏移触发全量扫描。"""
    with pytest.raises(ValidationError):
        BrowseImagesInput(offset=100001)
    # 边界值合法
    assert BrowseImagesInput(offset=100000).offset == 100000
    assert BrowseImagesInput(offset=0).offset == 0
