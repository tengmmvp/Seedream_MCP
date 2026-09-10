"""browse_images 工具结构化结果、分页元数据与读权限拒绝测试。

多数用例直连 handle_browse_images，工作区经 workspace_root fixture 注入的
SEEDREAM_WORKSPACE_ROOT 回退取得，图片目录为工作区派生的 .seedream/images，测试
图片统一放图片目录内；走完整 MCPServer 调用链的用例以 _NoRootsContext 提供无会话
的替身上下文。需要越界目录或越界文件的用例将工作区与越界路径同置于 tmp_path
之下，不污染共享 basetemp。
"""

import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from _progress_fakes import RecordingProgressContext
from seedream_mcp.resources import mcp
from seedream_mcp.tools import BrowseImagesInput
from seedream_mcp.tools.core import browse as browse_core_module
from seedream_mcp.tools.impl import browse_images as browse_images_module
from seedream_mcp.tools.impl.browse_images import handle_browse_images
from seedream_mcp.utils.io.io_path import _WORKSPACE_ROOTS_VAR


def _seed_images_root(ws: Path) -> Path:
    """返回 ws 派生的图片目录并确保存在，测试图片统一放图片目录内。"""
    root = ws / ".seedream" / "images"
    root.mkdir(parents=True, exist_ok=True)
    return root


async def test_browse_images_returns_structured_success(workspace_root: Path) -> None:
    """正常浏览返回 completed 结构化结果与文本内容。"""
    (_seed_images_root(workspace_root) / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "completed"
    assert result.structured_content["count"] == 1
    assert any(isinstance(content, TextContent) for content in result.content)


async def test_browse_images_returns_empty_when_no_files(workspace_root: Path) -> None:
    """无图片文件时返回 empty 状态与 count=0。"""
    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "empty"
    assert result.structured_content["count"] == 0


async def test_browse_images_rejects_out_of_workspace_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录越出工作区边界时以 failed 结构化错误拒绝。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside_dir_for_test"
    outside_dir.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    result = await handle_browse_images(BrowseImagesInput(directory=str(outside_dir)))

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"


async def test_browse_directory_error_branches_report_terminal_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录无效与目录越界两个早退分支上报 100% 失败终态，与其余错误分支一致。

    目录形态非法为调用方可自纠的参数错误归 validation_error；越界分支保持
    browse_failed。
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside_dir_for_test"
    outside_dir.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    invalid_ctx = RecordingProgressContext()
    invalid_result = await handle_browse_images(
        BrowseImagesInput(directory="ba\x00d"), ctx=cast("Context[Any, Any]", invalid_ctx)
    )
    assert invalid_result.is_error is True
    assert invalid_ctx.calls[-1][0] == 100.0
    assert invalid_ctx.calls[-1][2] == "浏览图片处理失败"
    assert invalid_result.structured_content["error"]["type"] == "validation_error"

    out_of_scope_ctx = RecordingProgressContext()
    out_of_scope_result = await handle_browse_images(
        BrowseImagesInput(directory=str(outside_dir)),
        ctx=cast("Context[Any, Any]", out_of_scope_ctx),
    )
    assert out_of_scope_result.is_error is True
    assert out_of_scope_ctx.calls[-1][0] == 100.0
    assert out_of_scope_ctx.calls[-1][2] == "浏览图片处理失败"
    assert out_of_scope_result.structured_content["error"]["type"] == "browse_failed"


async def test_browse_images_ignores_outside_images_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描结果混入越界图片时被剔除，不崩溃并返回 empty。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_img = outside / "outside.png"
    outside_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    def _fake_find_images_in_directory(*args: object, **kwargs: object) -> list[Path]:
        return [outside_img]

    monkeypatch.setattr(
        browse_core_module,
        "find_images_in_directory",
        _fake_find_images_in_directory,
    )

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=True))

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "empty"
    assert result.structured_content["count"] == 0


async def test_browse_images_empty_format_filter_skips_scan(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空列表 format_filter 与「全部后缀不受支持」语义一致：跳过扫描并以工具错误返回。

    此前空列表因 falsy 判断直接退化为不过滤的全量扫描，与全不支持分支行为不一致。
    图片目录内放一张图作金丝雀：误触发全量扫描时该目录非空，结果形态随之可辨。
    """
    (_seed_images_root(workspace_root) / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _fail_find(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("无有效后缀时不应触发目录扫描")

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _fail_find)

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, format_filter=[])
    )

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"
    assert result.structured_content["count"] == 0
    assert result.structured_content["error"]["type"] == "validation_error"


async def test_browse_images_fallback_error_preserves_format_filter(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外层兜底错误分支回显经同一规则过滤的 format_filter，不丢失用户原始输入。"""

    async def _exploding_request(params: object, ctx: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr(browse_images_module, "execute_browse_request", _exploding_request)

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, format_filter=[".png", ".exe"])
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["format_filter"] == [".png"]


async def test_browse_images_pagination_metadata(workspace_root: Path) -> None:
    """分页元数据正确：has_more 时 total_count 为 None，末页给出精确总数。"""
    images_root = _seed_images_root(workspace_root)
    for name in ("a.png", "b.png", "c.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )
    sc1 = page1.structured_content
    assert isinstance(sc1, dict)
    assert sc1["count"] == 2
    assert sc1["total_count"] is None  # has_more 时未扫完，total_count 不精确
    assert sc1["has_more"] is True
    assert sc1["next_offset"] == 2

    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=2)
    )
    sc2 = page2.structured_content
    assert isinstance(sc2, dict)
    assert sc2["count"] == 1
    assert sc2["total_count"] == 3
    assert sc2["has_more"] is False
    assert sc2["next_offset"] is None


async def test_browse_images_offset_beyond_end_signals_tool_error(
    workspace_root: Path,
) -> None:
    """offset 越过最后一页为模型可自纠的参数错误，以工具错误信号返回。

    文本与结构化错误两条通道均携带实际总数与有效区间，模型修正 offset 后即可
    重试成功。
    """
    images_root = _seed_images_root(workspace_root)
    for name in ("a.png", "b.png", "c.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=4)
    )
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert result.is_error is True
    assert sc["status"] == "failed"
    assert sc["count"] == 0
    assert sc["error"]["type"] == "validation_error"
    assert "目录共有 3 张图片" in sc["error"]["message"]
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "0 <= offset < 3" in text


async def test_browse_images_deep_page_reuses_resolved_paths(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """深翻页命中扫描缓存时不重复 resolve 图片文件。

    (原始, resolved) 对随首次扫描缓存；深页命中完整缓存时免于 O(offset) 次逐文件
    resolve，仅剩图片目录与请求目录的目录级 resolve。统计第二次浏览期间 .png 的
    resolve 调用数并断言为零。
    """
    images_root = _seed_images_root(workspace_root)
    for i in range(5):
        (images_root / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # 首页 scan_limit = 0+5+1 = 6 > 5，扫到目录末尾缓存完整的 (原始, resolved) 对列表
    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=5, offset=0)
    )
    assert page1.structured_content["count"] == 5

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
    assert page2.structured_content["count"] == 1
    image_resolves = [p for p in resolved_paths if p.suffix == ".png"]
    assert image_resolves == [], "缓存命中的深页不应再对图片文件逐个 resolve"


def test_browse_images_input_rejects_oversized_offset() -> None:
    """offset 超上限应被 pydantic 拒绝，防止无界偏移触发全量扫描。"""
    with pytest.raises(ValidationError):
        BrowseImagesInput(offset=100001)
    # 边界值合法
    assert BrowseImagesInput(offset=100000).offset == 100000
    assert BrowseImagesInput(offset=0).offset == 0


async def test_browse_images_format_filter_all_unsupported_echoes_original(
    workspace_root: Path,
) -> None:
    """format_filter 全部为不支持后缀时返回区分消息并回显原始输入。

    用 .svg 而非 .gif：.gif 属受支持后缀，会落入 supported_only 非空分支而不
    触发 exhausted 分支；.svg 不在支持集合内，可真正命中。
    """
    result = await handle_browse_images(BrowseImagesInput(directory=".", format_filter=[".svg"]))

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"
    assert result.structured_content["format_filter"] == [".svg"]
    assert result.structured_content["error"]["type"] == "validation_error"
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "均不在支持列表" in text
    assert "支持" in text


class _NoRootsContext:
    """无会话的替身上下文：session 为 None 使工作区边界回退环境变量根。

    mcp.call_tool 缺省构造的 Context 无请求上下文，访问 session 属性抛 ValueError
    会使工具体整体失败，故显式传入本替身驱动完整调用链。protocol_version 为
    None 对齐无请求 Context 的形态，工具的 resolver 依赖注入据此选择取回路径。
    """

    session = None
    protocol_version = None

    async def report_progress(self, *args: object, **kwargs: object) -> None:
        """进度上报空实现。"""


async def test_browse_images_offset_error_signal_visible_to_client(
    workspace_root: Path,
) -> None:
    """offset 越界的错误信号经 MCPServer 调用链可被客户端识别。

    经 mcp.call_tool 走完 inputSchema 校验与结果透传，返回的 CallToolResult 携带
    isError 与稳定的结构化错误标记，客户端 UI 无需解析文本即可判定失败。
    """
    images_root = _seed_images_root(workspace_root)
    for name in ("a.png", "b.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await mcp.call_tool(
        "browse_images",
        {"directory": ".", "recursive": False, "limit": 2, "offset": 5},
        context=cast("Context[Any, Any]", _NoRootsContext()),
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["error"]["type"] == "validation_error"


async def test_browse_images_format_filter_error_signal_visible_to_client(
    workspace_root: Path,
) -> None:
    """format_filter 全不支持的错误信号经 MCPServer 调用链可被客户端识别。"""
    result = await mcp.call_tool(
        "browse_images",
        {"directory": ".", "format_filter": [".svg"]},
        context=cast("Context[Any, Any]", _NoRootsContext()),
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["error"]["type"] == "validation_error"


async def test_browse_images_full_page_appends_pagination_hint(workspace_root: Path) -> None:
    """满页且仍有更多时文本尾部追加 offset 翻页引导；末页不追加。

    has_more 时未扫完全量，total_count 为 None，引导行省略总数仅给出当前页区间。
    """
    images_root = _seed_images_root(workspace_root)
    for name in ("a.png", "b.png", "c.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

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


async def test_browse_images_fallback_preserves_resolved_directories(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """impl 在目录解析完成后抛未预期异常时，兜底 structuredContent 回显已解析目录。"""

    from seedream_mcp.utils.io.io_path import _WORKSPACE_ROOTS_VAR

    def _exploding_display_entries(**kwargs: object) -> NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr(browse_core_module, "_build_display_entries", _exploding_display_entries)
    images_root = _seed_images_root(workspace_root)
    (images_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    token = _WORKSPACE_ROOTS_VAR.set((workspace_root.resolve(),))
    try:
        result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["resolved_directories"] == [
        str(images_root.resolve()).replace("\\", "/")
    ]


async def test_browse_images_fallback_boundary_echoes_real_roots_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无会话 Roots 时越界拒绝回显环境回退的真实根，消息为固定配置指引文案。

    直接调用 handle_browse_images，不经 workspace_roots_scope，工作区经
    SEEDREAM_WORKSPACE_ROOT 回退取得。
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside_dir_for_test"
    outside_dir.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    result = await handle_browse_images(BrowseImagesInput(directory=str(outside_dir)))

    assert result.is_error is True
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "目录不在读取范围内" in text
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["workspace_roots"] == [
        str(workspace.resolve()).replace("\\", "/")
    ]
    # 越界场景目录解析未产出任何界内目录，保持空列表。
    assert result.structured_content["resolved_directories"] == []


async def test_browse_images_fallback_boundary_echoes_real_paths_on_success(
    workspace_root: Path,
) -> None:
    """无会话 Roots 的成功浏览回显真实边界与绝对路径条目，与 Roots 会话同形态。"""
    images_root = _seed_images_root(workspace_root)
    (images_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["workspace_roots"] == [str(workspace_root.resolve()).replace("\\", "/")]
    assert sc["resolved_directories"] == [str(images_root.resolve()).replace("\\", "/")]
    assert sc["images"][0]["path"] == (images_root / "demo.png").resolve().as_posix()


async def test_browse_images_empty_result_distinguishes_unreadable_dirs(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描目录不可读时空结果文案区分「目录不可读」与「无图片文件」。

    经 monkeypatch 使 os.scandir 抛 PermissionError，驱动 io_scan 扫描与
    缓存透传与 browse 空结果分支的完整链路；不可读目录为已 resolve 的请求目录。
    """
    import seedream_mcp.utils.io.io_scan as path_module

    images_root = _seed_images_root(workspace_root)

    def _raise_permission(path: object) -> NoReturn:
        raise PermissionError("denied")

    monkeypatch.setattr(path_module.os, "scandir", _raise_permission)

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "empty"
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "目录不可读或无图片文件" in text
    assert str(images_root.resolve()).replace("\\", "/") in text


async def test_browse_images_empty_message_sanitizes_unreadable_dir_paths(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空结果消息对不可读目录路径逐项净化后再拼接。

    路径来自服务器文件系统，控制字符经净化压平、敏感键值脱敏，不原文进入
    用户可见文本。
    """
    from seedream_mcp.utils.io.io_path import _WORKSPACE_ROOTS_VAR

    hostile = workspace_root / "dir\r\napi_key=leak"

    def _fake_find(*args: object, **kwargs: Any) -> list[Path]:
        kwargs["unreadable_dirs"].append(hostile)
        return []

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _fake_find)

    token = _WORKSPACE_ROOTS_VAR.set((workspace_root.resolve(),))
    try:
        result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=True))
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "empty"
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "目录不可读或无图片文件" in text
    assert "\r" not in text
    assert "\n" not in text
    assert "api_key=***" in text
    assert "leak" not in text


async def test_browse_images_empty_without_unreadable_keeps_plain_message(
    workspace_root: Path,
) -> None:
    """无不可读目录的空结果保持原有文案，不携带目录不可读表述。"""
    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "未找到图片文件" in text
    assert "目录不可读" not in text


async def test_browse_images_truncated_empty_result_marks_incompleteness(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """条目预算截断的空页不声称完备：文本携带可见标记，总数与 has_more 置未知。

    截断静默时空页会误报「未找到图片文件」且 total_count=0，模型无从得知目录
    可能仍有图片。
    """
    import seedream_mcp.utils.io.io_scan as path_module

    images_root = _seed_images_root(workspace_root)
    for i in range(8):
        (images_root / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(path_module, "_SCAN_ENTRY_BUDGET", 5)

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "empty"
    assert sc["count"] == 0
    assert sc["total_count"] is None
    assert sc["has_more"] is None
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "未找到图片文件" in text
    assert "目录条目过多，结果可能不完整" in text


async def test_browse_images_truncated_partial_page_marks_incompleteness(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """截断前已收集的部分页携带标记，total_count 不以低报的精确值声称完备。"""
    import seedream_mcp.utils.io.io_scan as path_module

    images_root = _seed_images_root(workspace_root)
    early = images_root / "a"
    early.mkdir()
    (early / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    late = images_root / "b"
    late.mkdir()
    for i in range(20):
        (late / f"note_{i:02d}.txt").write_bytes(b"x")
    # 预算使子目录 a 完整产出 1 图、子目录 b 触发截断，页内条目为部分结果
    monkeypatch.setattr(path_module, "_SCAN_ENTRY_BUDGET", 15)

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=True))

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "completed"
    assert sc["count"] == 1
    assert sc["total_count"] is None
    assert sc["has_more"] is None
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "x.png" in text
    assert "目录条目过多，结果可能不完整" in text


def test_format_file_info_degrades_on_malformed_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """畸形时间戳使 fromtimestamp 抛 ValueError 时降级为「文件信息不可用」。

    降级须同时置空 size_mb 与 modified 两键，避免半份详情误导调用方。以替身
    模块替换 browse 命名空间内的 datetime 名字构造异常；内建 datetime 类为
    不可变类型，无法直接对其打属性补丁。
    """
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    class _ExplodingDatetime:
        @staticmethod
        def fromtimestamp(timestamp: float) -> object:
            raise ValueError("year is out of range")

    fake_datetime_module = SimpleNamespace(datetime=_ExplodingDatetime)
    monkeypatch.setattr(browse_core_module, "datetime", fake_datetime_module)

    text, details = browse_core_module._format_file_info("a.png", image, True)

    assert text == "a.png | 文件信息不可用"
    assert details == {"size_mb": None, "modified": None}


def test_build_display_entries_keeps_file_name_verbatim(
    workspace_root: Path,
) -> None:
    """含凭据样式片段的文件名原样进入文本与结构化两条通道，保证按条目回流可命中。"""
    image = _seed_images_root(workspace_root) / "img api_key=secret.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    lines, entries = browse_core_module._build_display_entries(
        images=[image],
        image_resolved_map={image: image.resolve()},
        show_details=False,
    )

    expected = image.resolve().as_posix()
    assert lines[0] == f"1. {expected}"
    assert entries[0]["path"] == expected


def test_build_display_entries_flattens_newlines_in_text_channel(tmp_path: Path) -> None:
    """含换行文件名的文本行压平换行，防止向清单注入伪造行；结构化路径保持原样。

    POSIX 允许文件名携带换行，文本通道原样输出会伪造额外清单行；结构化通道的
    路径需可回流命中真实文件，不做压平。
    """
    image = tmp_path / "a\nb\r.png"

    lines, entries = browse_core_module._build_display_entries(
        images=[image],
        image_resolved_map={image: image},
        show_details=False,
    )

    assert entries[0]["path"] == image.as_posix()
    assert lines == [f"1. {tmp_path.as_posix()}/a b .png"]


def test_build_display_entries_flattens_unicode_line_separator(tmp_path: Path) -> None:
    """含 U+2028 行分隔符的文件名在文本通道压平为空格，与 errors 净化口径单一来源。

    仅替换 \\n 与 \\r 时 U+2028/U+2029 等 Unicode 行分隔符仍会伪造编号行；控制
    字符口径统一经 CONTROL_CHARS_PATTERN 覆盖 C0、DEL、NEL 与行段分隔符。
    """
    image = tmp_path / "a\u2028b.png"

    lines, entries = browse_core_module._build_display_entries(
        images=[image],
        image_resolved_map={image: image},
        show_details=False,
    )

    assert entries[0]["path"] == image.as_posix()
    assert lines == [f"1. {tmp_path.as_posix()}/a b.png"]


async def test_browse_images_directory_outside_images_root_lists_absolute_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回退边界下浏览图片目录外目录，条目为绝对路径，与 Roots 会话同形态。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    photos = workspace / "photos"
    photos.mkdir()
    (photos / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    result = await handle_browse_images(BrowseImagesInput(directory=str(photos)))

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    images = result.structured_content["images"]
    assert images[0]["path"] == (photos / "cat.png").resolve().as_posix()


async def test_browse_images_outside_images_root_lists_absolute_under_session_roots(
    tmp_path: Path,
) -> None:
    """会话 Roots 边界下图片目录外条目为绝对路径，可直接回流 image 参数。

    与回退边界用例共同锁定条目形态不随会话边界漂移。
    """
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    token = _WORKSPACE_ROOTS_VAR.set((tmp_path,))
    try:
        result = await handle_browse_images(BrowseImagesInput(directory=str(photos)))
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    images = result.structured_content["images"]
    assert images[0]["path"] == (photos / "cat.png").resolve().as_posix()


async def test_browse_images_invalid_directory_error_sanitized_and_truncated(
    workspace_root: Path,
) -> None:
    """无效目录错误消息经净化截断，不整体回显超长输入与凭据样式片段。

    以 // 前缀构造跨平台命中的 UNC 路径，normalize_path 在 resolve 前拒绝；
    错误消息收敛到输出上限内且 api_key 裸值被脱敏。
    """
    directory = "//server/share/api_key=secret" + "a" * 900
    assert len(directory) <= 1024  # schema 侧 max_length 内，进入 handler 触发拒绝

    result = await handle_browse_images(BrowseImagesInput(directory=directory))

    assert result.is_error is True
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "目录路径无效" in text
    assert "secret" not in text
    # 截断保留前 500 字符并附带截断标注，上限按标注开销放宽。
    assert len(text) <= 540
    assert isinstance(result.structured_content, dict)
    structured_message = result.structured_content["error"]["message"]
    assert "secret" not in structured_message


async def test_browse_images_unsupported_format_message_sanitized(
    workspace_root: Path,
) -> None:
    """全不支持后缀的区分消息中，用户 filter 的凭据样式片段被脱敏。

    仅净化用户提交的 filter 串，静态支持列表保持完整可读，消息仍含区分表述。
    """
    result = await handle_browse_images(
        BrowseImagesInput(directory=".", format_filter=["api_key=secret"])
    )

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "均不在支持列表" in text
    assert "secret" not in text
    assert "***" in text


async def test_browse_images_empty_format_filter_message_has_no_blank_slot(
    workspace_root: Path,
) -> None:
    """空列表 format_filter 的耗尽消息不含量词空位：无双空格、无残缺语义。

    空列表是文档明示的合法输入，与「全部后缀不受支持」共用 exhausted 标记；
    无用户格式可回显时改用不含空位的文案。
    """
    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, format_filter=[])
    )

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"
    assert result.structured_content["format_filter"] == []
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "未指定任何受支持的图片格式" in text
    assert "均不在支持列表" not in text
    assert "  " not in text


# ==================== 剔除项不占分页配额 ====================


async def test_browse_images_dropped_entries_do_not_consume_page_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """早停窗口内的越界条目不占分页配额：has_more 正确、尾部图片翻页可达。

    扫描层按 scan_limit 早停，窗口内越界条目在扫描后才剔除；无补扫时剔除项
    占满配额会使 has_more 假阴性、total_count 低报。经注入扫描器返回越界条目
    居首的有序列表，稳定复现剔除占额场景。
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside_quota_probe_target.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))
    for i in range(3):
        (workspace / f"img_{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_order = [outside] + [workspace / f"img_{i}.png" for i in range(3)]

    def _fake_scan(**kwargs: object) -> list[Path]:
        limit = kwargs["limit"]
        assert isinstance(limit, int)
        return scan_order[:limit]

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _fake_scan)

    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )
    sc1 = page1.structured_content
    assert isinstance(sc1, dict)
    assert sc1["count"] == 2
    assert sc1["has_more"] is True
    assert sc1["total_count"] is None
    assert sc1["next_offset"] == 2

    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=2)
    )
    sc2 = page2.structured_content
    assert isinstance(sc2, dict)
    assert sc2["count"] == 1
    assert sc2["total_count"] == 3
    assert sc2["has_more"] is False
    assert sc2["next_offset"] is None
    text2 = "".join(getattr(content, "text", "") for content in page2.content)
    assert "img_2.png" in text2


async def test_browse_images_out_of_bounds_symlink_keeps_pagination_reachable(
    workspace_root: Path,
) -> None:
    """目录含 1 个越界符号链接与足量真图：has_more 与翻页正确、尾部可达。

    真实符号链接的端到端路径：符号链接文件不列入扫描结果，即使列入也须在越界
    复核被剔除且不占配额。Windows 符号链接创建权限不足时按既有先例 skip，
    剔除占额语义由注入扫描器的用例稳定覆盖。
    """
    # 越界目标置于共享 basetemp 之外的独占临时目录
    outside_dir = Path(tempfile.mkdtemp(prefix="seedream-browse-outside-"))
    target = outside_dir / "target.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    images_root = _seed_images_root(workspace_root)
    link = images_root / "0_link.png"
    try:
        os.symlink(target, link)
    except (OSError, AttributeError):
        shutil.rmtree(outside_dir, ignore_errors=True)
        pytest.skip("当前进程无法创建符号链接（Windows 可能需要开发者模式或管理员）")

    try:
        for i in range(3):
            (images_root / f"img_{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        page1 = await handle_browse_images(
            BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
        )
        sc1 = page1.structured_content
        assert isinstance(sc1, dict)
        assert sc1["has_more"] is True
        text1 = "".join(getattr(content, "text", "") for content in page1.content)
        assert "0_link.png" not in text1

        page2 = await handle_browse_images(
            BrowseImagesInput(directory=".", recursive=False, limit=2, offset=2)
        )
        sc2 = page2.structured_content
        assert isinstance(sc2, dict)
        assert sc2["total_count"] == 3
        assert sc2["has_more"] is False
        text2 = "".join(getattr(content, "text", "") for content in page2.content)
        assert "img_2.png" in text2
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


# ==================== 相对目录路径无效的区分消息 ====================


async def test_browse_images_invalid_relative_directory_reports_invalid_path(
    workspace_root: Path,
) -> None:
    """无法规范化的相对目录报「目录路径无效」，不再误报为超出允许范围。

    含内嵌空字节的相对路径在 normalize_path 抛 ValueError，路径缺陷与拼接的根
    无关；首个根即失败时与绝对分支同口径返回路径无效消息，仅路径合法但全部越界
    时才报超出范围。
    """
    result = await handle_browse_images(BrowseImagesInput(directory="ba\x00d"))

    assert result.is_error is True
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "目录路径无效" in text
    assert "目录超出允许范围" not in text


async def test_browse_images_rejects_relative_escape_outside_images_root(
    workspace_root: Path,
) -> None:
    """相对目录经 ``..`` 解析到图片目录之外时拒绝，即便目标仍在工作区内。"""
    _seed_images_root(workspace_root)

    result = await handle_browse_images(BrowseImagesInput(directory="../..", recursive=False))

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "相对路径仅限图片保存目录内" in text


async def test_browse_session_roots_echoes_images_root_outside_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话 Roots 下显式图片目录位于 Roots 之外时，解析目录与条目仍回显真实路径。

    默认浏览解析到服务器配置的图片目录，读权限判定已覆盖越界防护，回显不做遮蔽。
    """
    from seedream_mcp.config import SeedreamConfig, set_active_config

    workspace = tmp_path / "proj"
    workspace.mkdir()
    outside_root = tmp_path / "private-pics"
    images_root = outside_root / ".seedream" / "images"
    (images_root / "2026-09-06").mkdir(parents=True)
    (images_root / "2026-09-06" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    set_active_config(
        SeedreamConfig(
            api_key="test_key",
            workspace_root=str(workspace),
            data_root=str(outside_root),
        )
    )
    token = _WORKSPACE_ROOTS_VAR.set((workspace,))
    try:
        result = await handle_browse_images(BrowseImagesInput())
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    resolved = result.structured_content["resolved_directories"]
    assert resolved == [str(images_root.resolve()).replace("\\", "/")]
    entry_path = (images_root / "2026-09-06" / "a.png").resolve().as_posix()
    assert result.structured_content["images"][0]["path"] == entry_path
