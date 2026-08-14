"""browse_images 工具结构化结果、分页元数据与工作区越界拒绝测试。"""

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

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "completed"
    assert result.structuredContent["count"] == 1
    assert any(isinstance(content, TextContent) for content in result.content)


@pytest.mark.asyncio
async def test_browse_images_returns_empty_when_no_files(workspace_root: Path) -> None:
    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

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

    result = await handle_browse_images(BrowseImagesInput(directory=str(outside_dir)))

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

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=True))

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "empty"
    assert result.structuredContent["count"] == 0


@pytest.mark.asyncio
async def test_browse_images_empty_format_filter_skips_scan(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空列表 format_filter 与"全部后缀不受支持"语义一致：跳过扫描返回空结果。

    此前空列表因 falsy 判断直接退化为不过滤的全量扫描，与全不支持分支行为不一致。
    """
    (workspace_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _fail_find(*args, **kwargs):
        raise AssertionError("无有效后缀时不应触发目录扫描")

    monkeypatch.setattr(browse_images_module, "find_images_in_directory", _fail_find)

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, format_filter=[])
    )

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "empty"
    assert result.structuredContent["count"] == 0


@pytest.mark.asyncio
async def test_browse_images_fallback_error_preserves_format_filter(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外层兜底错误分支回显经同一规则过滤的 format_filter，不丢失用户原始输入。"""
    from mcp.types import CallToolResult

    async def _exploding_impl(params, ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(browse_images_module, "_handle_browse_images_impl", _exploding_impl)

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, format_filter=[".png", ".exe"])
    )

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["format_filter"] == [".png"]


@pytest.mark.asyncio
async def test_browse_images_pagination_metadata(workspace_root: Path) -> None:
    for name in ("a.png", "b.png", "c.png"):
        (workspace_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )
    sc1 = page1.structuredContent
    assert isinstance(sc1, dict)
    assert sc1["count"] == 2
    assert sc1["total_count"] is None  # has_more 时未扫完，total_count 不精确
    assert sc1["has_more"] is True
    assert sc1["next_offset"] == 2

    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=2)
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
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=4)
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


@pytest.mark.asyncio
async def test_browse_images_format_filter_all_unsupported_echoes_original(
    workspace_root: Path,
) -> None:
    """format_filter 全部为不支持后缀时返回区分消息，structuredContent 回显用户原始输入。

    用 .svg 而非任务示例的 .gif：formats.py 的 SUPPORTED_IMAGE_EXTENSIONS 含 .gif，
    若用 .gif 会落入 supported_only 非空分支而不触发 format_filter_exhausted，无法覆盖
    区分消息。.svg 不在支持集合内，可真正命中 exhausted 分支。断言区分消息含
    "均不在支持列表"与"支持"，status 为 empty 且 isError 为 False；format_filter 保留
    用户原始非空列表 [".svg"] 供回显，不缩减为空列表。
    """
    result = await handle_browse_images(BrowseImagesInput(directory=".", format_filter=[".svg"]))

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "empty"
    assert result.structuredContent["format_filter"] == [".svg"]
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "均不在支持列表" in text
    assert "支持" in text
