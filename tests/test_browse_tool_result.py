"""browse_images 工具结构化结果、分页元数据与工作区越界拒绝测试。"""

from pathlib import Path
from types import SimpleNamespace

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

    async def _exploding_impl(params, ctx, **kwargs):
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


@pytest.mark.asyncio
async def test_browse_images_deep_page_reuses_resolved_paths(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """深翻页命中扫描缓存时不重复 resolve 图片文件。

    图片路径的 resolve 由扫描缓存层在首次扫描完成时执行并随 (原始, resolved) 对缓存；
    第二次浏览的深页命中完整缓存时免于 O(offset) 次逐文件 resolve，仅剩目录级 resolve
    （工作区根与请求目录）。统计第二次浏览期间后缀为 .png 的 Path.resolve 调用数，
    断言为零。
    """
    for i in range(5):
        (workspace_root / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # 首页 scan_limit = 0+5+1 = 6 > 5，扫到目录末尾缓存完整的 (原始, resolved) 对列表
    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=5, offset=0)
    )
    assert page1.structuredContent["count"] == 5

    resolved_paths: list[Path] = []
    original_resolve = Path.resolve

    def _counting_resolve(self: Path, strict: bool = False) -> Path:
        resolved_paths.append(self)
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", _counting_resolve)

    # 深页 offset=4：scan_limit=4+1+1=6，命中完整缓存，不重扫也不逐文件 resolve
    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=1, offset=4)
    )
    assert page2.structuredContent["count"] == 1
    image_resolves = [p for p in resolved_paths if p.suffix == ".png"]
    assert image_resolves == [], "缓存命中的深页不应再对图片文件逐个 resolve"


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


@pytest.mark.asyncio
async def test_browse_images_full_page_appends_pagination_hint(workspace_root: Path) -> None:
    """满页且仍有更多时文本尾部追加 offset 翻页引导；末页不追加。

    has_more 时未扫完全量，total_count 为 None，引导行省略总数仅给出当前页区间。
    """
    for name in ("a.png", "b.png", "c.png"):
        (workspace_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )
    text1 = "".join(getattr(content, "text", "") for content in page1.content)
    assert "第 1-2 张" in text1
    assert "仍有更多" in text1
    assert "offset=2" in text1

    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=2)
    )
    text2 = "".join(getattr(content, "text", "") for content in page2.content)
    assert "仍有更多" not in text2
    assert "offset=" not in text2


@pytest.mark.asyncio
async def test_browse_images_fallback_preserves_resolved_directories(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """impl 在目录解析完成后抛未预期异常时，兜底 structuredContent 回显已解析目录。

    resolved_directories 列表由外层创建并共享给 impl；兜底分支不再恒为空列表。
    以会话 Roots 场景断言真实路径回显：env/CWD 回退场景的路径回显被占位符遮蔽，
    无法承载本断言。
    """
    from seedream_mcp.utils.io.io_path import _WORKSPACE_ROOTS_VAR

    def _exploding_display_entries(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(browse_images_module, "_build_display_entries", _exploding_display_entries)
    (workspace_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    token = _WORKSPACE_ROOTS_VAR.set((workspace_root.resolve(),))
    try:
        result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)

    assert result.isError is True
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["resolved_directories"] == [str(workspace_root.resolve())]


@pytest.mark.asyncio
async def test_browse_images_fallback_boundary_masks_paths_in_error(
    workspace_root: Path,
) -> None:
    """无会话 Roots 时越界拒绝不回显 env/CWD 绝对路径。

    直接调用 handle_browse_images（未进入 workspace_roots_scope），边界经
    SEEDREAM_WORKSPACE_ROOT 回退取得。越界消息与 structuredContent 的
    workspace_roots 均以占位符替代，不向调用方暴露服务器本地目录结构。
    """
    from seedream_mcp.tools.impl.browse_images import _FALLBACK_BOUNDARY_PLACEHOLDER

    outside_dir = workspace_root.parent / "outside_dir_for_masking_test"
    outside_dir.mkdir(exist_ok=True)

    result = await handle_browse_images(BrowseImagesInput(directory=str(outside_dir)))

    assert result.isError is True
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "服务器配置的工作区目录" in text
    assert str(workspace_root) not in text
    assert str(outside_dir.resolve().parent) not in text
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["workspace_roots"] == [_FALLBACK_BOUNDARY_PLACEHOLDER]
    # 越界场景目录解析未产出任何界内目录，保持空列表而非占位符。
    assert result.structuredContent["resolved_directories"] == []


@pytest.mark.asyncio
async def test_browse_images_fallback_boundary_masks_paths_on_success(
    workspace_root: Path,
) -> None:
    """无会话 Roots 的成功浏览同样遮蔽边界路径，展示层保持相对路径。"""
    from seedream_mcp.tools.impl.browse_images import _FALLBACK_BOUNDARY_PLACEHOLDER

    (workspace_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.isError is False
    sc = result.structuredContent
    assert isinstance(sc, dict)
    assert sc["workspace_roots"] == [_FALLBACK_BOUNDARY_PLACEHOLDER]
    assert sc["resolved_directories"] == [_FALLBACK_BOUNDARY_PLACEHOLDER]
    assert sc["images"][0]["path"] == "demo.png"


@pytest.mark.asyncio
async def test_browse_images_empty_result_distinguishes_unreadable_dirs(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描目录不可读时空结果文案区分「目录不可读」与「无图片文件」。

    经 monkeypatch 使 os.scandir 抛 PermissionError，驱动 io_path 扫描、io_scan
    缓存透传与 browse 空结果分支的完整链路；不可读目录为已 resolve 的请求目录。
    """
    import seedream_mcp.utils.io.io_path as path_module

    def _raise_permission(path):
        raise PermissionError("denied")

    monkeypatch.setattr(path_module.os, "scandir", _raise_permission)

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "empty"
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "目录不可读或无图片文件" in text
    assert "1 个目录（回退边界场景不回显路径）" in text
    assert str(workspace_root.resolve()) not in text


@pytest.mark.asyncio
async def test_browse_images_empty_without_unreadable_keeps_plain_message(
    workspace_root: Path,
) -> None:
    """无不可读目录的空结果保持原有文案，不携带目录不可读表述。"""
    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "未找到图片文件" in text
    assert "目录不可读" not in text


def test_format_file_info_degrades_on_malformed_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """畸形时间戳使 fromtimestamp 抛 ValueError 时降级为“文件信息不可用”，不向调用方抛异常。

    stat 本身成功，降级分支须同时置空 size_mb 与 modified 两键，避免半份详情误导
    调用方。以替身模块替换 browse_images 命名空间内的 datetime 名字，使
    datetime.datetime.fromtimestamp 抛 ValueError；内建 datetime 类为不可变类型，
    无法直接对其打属性补丁。
    """
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    class _ExplodingDatetime:
        @staticmethod
        def fromtimestamp(timestamp: float) -> object:
            raise ValueError("year is out of range")

    fake_datetime_module = SimpleNamespace(datetime=_ExplodingDatetime)
    monkeypatch.setattr(browse_images_module, "datetime", fake_datetime_module)

    text, details = browse_images_module._format_file_info("a.png", image, True)

    assert text == "a.png | 文件信息不可用"
    assert details == {"size_mb": None, "modified": None}
