"""browse_images 工具结构化结果、分页元数据与读权限拒绝测试。

多数用例直连 handle_browse_images，工作区经 workspace_root fixture 注入的
SEEDREAM_WORKSPACE_ROOT 回退取得，图片目录为工作区派生的 .seedream/images，测试
图片统一放图片目录内；走完整 MCPServer 调用链的用例以 _NoRootsContext 提供无会话
的替身上下文。需要越界目录或越界文件的用例将工作区与越界路径同置于 tmp_path
之下，不污染共享 basetemp。
"""

import asyncio
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, NamedTuple, NoReturn, cast

import pytest
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from _lib.log_fakes import RecordingLogger, capture_loguru_messages
from _lib.os_fakes import _install_counting_realpath, _install_failing_resolve_for
from _lib.progress_fakes import RecordingProgressContext
from seedream_mcp.server.runtime import mcp
from seedream_mcp.tools import BrowseImagesInput
from seedream_mcp.tools.handlers import browse_images as browse_core_module
from seedream_mcp.tools.handlers import browse_images as browse_images_module
from seedream_mcp.tools.handlers.browse_images import handle_browse_images
from seedream_mcp.utils.core.errors import SeedreamConfigError
from seedream_mcp.utils.io.path import _WORKSPACE_ROOTS_VAR
from seedream_mcp.utils.io.scan import ScanIncompleteCause, ScanResult


def _seed_images_root(ws: Path) -> Path:
    """返回 ws 派生的图片目录并确保存在，测试图片统一放图片目录内。"""
    root = ws / ".seedream" / "images"
    root.mkdir(parents=True, exist_ok=True)
    return root


class _RescanSeeding(NamedTuple):
    """补扫用例的工作区目录与按文件名索引的图片路径表。"""

    workspace: Path
    images: dict[str, Path]


def _seed_rescan_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    outside_names: Sequence[str],
    workspace_names: Sequence[str],
) -> _RescanSeeding:
    """在 tmp_path 下创建工作区与越界目录，按名写入图片并注入工作区环境变量。

    images 以去后缀文件名为键索引两侧图片路径。
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))
    images: dict[str, Path] = {}
    for directory, names in ((outside_dir, outside_names), (workspace, workspace_names)):
        for name in names:
            path = directory / f"{name}.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            images[name] = path
    return _RescanSeeding(workspace=workspace, images=images)


def _fake_cached_find(
    windows: list[tuple[list[Path], bool, ScanIncompleteCause | None]],
    scan_limits: list[int],
) -> Callable[..., ScanResult]:
    """cached_find_images_in_directory 的替身工厂：按序弹出窗口并套用调用方谓词。

    windows 逐项声明 (条目, 终点, 成因) 三元组，替身返回前以调用方 include 谓词过滤，
    忠实镜像扫描层契约；scan_limits 记录每次调用收到的配额。
    替身签名或 ScanResult 再增字段时仅此一份须同步。
    """

    def _fake(**kwargs: object) -> ScanResult:
        assert windows, "扫描轮数超出预期"
        scan_limits.append(cast(int, kwargs["scan_limit"]))
        window, complete, incomplete_cause = windows.pop(0)
        pairs = [(path, path) for path in window]
        include = cast("Callable[[tuple[Path, Path]], bool] | None", kwargs.get("include"))
        if include is not None:
            pairs = [pair for pair in pairs if include(pair)]
        return ScanResult(
            images=pairs,
            complete=complete,
            incomplete_cause=incomplete_cause,
        )

    return _fake


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


async def test_browse_images_fallback_logs_business_error_without_traceback(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已归约业务异常兜底时记 warning 无堆栈，与生成流水线的两级日志口径一致。"""

    async def _config_error_request(params: object, ctx: object, **kwargs: object) -> NoReturn:
        raise SeedreamConfigError("数据根目录不可用")

    monkeypatch.setattr(browse_images_module, "execute_browse_request", _config_error_request)
    fake_logger = RecordingLogger()
    monkeypatch.setattr(browse_images_module, "logger", fake_logger)

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is True
    assert any("浏览图片处理失败" in message for message in fake_logger.warnings)
    assert fake_logger.errors == []
    assert not any(kwargs.get("exception") for kwargs in fake_logger.opt_kwargs)


async def test_browse_images_fallback_logs_unexpected_error_with_traceback(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非预期异常兜底时仍记 error 级并附堆栈。"""

    async def _exploding_request(params: object, ctx: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr(browse_images_module, "execute_browse_request", _exploding_request)
    fake_logger = RecordingLogger()
    monkeypatch.setattr(browse_images_module, "logger", fake_logger)

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is True
    assert any("浏览图片处理失败" in message for message in fake_logger.errors)
    assert {"exception": True} in fake_logger.opt_kwargs
    assert fake_logger.warnings == []


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
    resolve，仅剩图片目录与请求目录的目录级 resolve。Path.resolve 委托
    os.path.realpath，计数后者即可覆盖，统计第二次浏览期间 .png 的调用数并断言为零。
    """
    images_root = _seed_images_root(workspace_root)
    for i in range(5):
        (images_root / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # 首页 scan_limit = 0+5+1 = 6 > 5，扫到目录末尾缓存完整的 (原始, resolved) 对列表。
    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=5, offset=0)
    )
    assert page1.structured_content["count"] == 5

    realpath_calls = _install_counting_realpath(monkeypatch)

    # 深页 offset=4：scan_limit=4+1+1=6，命中完整缓存，不重扫也不逐文件 resolve。
    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=1, offset=4)
    )
    assert page2.structured_content["count"] == 1
    image_paths = [p for p in realpath_calls if p.endswith(".png")]
    assert image_paths == [], "缓存命中的深页不应再对图片文件逐个 resolve"


async def test_browse_images_hit_path_pages_without_rescan(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中路径按页回放封顶窗口：分页元数据正确且底层扫描不再触发。

    首页扫到目录末尾缓存完整窗口后，各页请求命中回放，末页窗口短于配额时
    按 complete 事实给出精确总数，has_more/next_offset 与切片语义不随封顶回放漂移。
    """
    images_root = _seed_images_root(workspace_root)
    for i in range(7):
        (images_root / f"img_{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    scan_calls: list[int] = []
    from seedream_mcp.utils.io import scan as io_scan

    original_scan = io_scan.find_images_in_directory

    def _counting_scan(**kwargs: Any) -> list[Path]:
        limit = kwargs["limit"]
        assert isinstance(limit, int)
        scan_calls.append(limit)
        return original_scan(**kwargs)

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _counting_scan)

    seed = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=7, offset=0)
    )
    assert seed.structured_content["count"] == 7

    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=3, offset=0)
    )
    sc1 = page1.structured_content
    assert isinstance(sc1, dict)
    assert sc1["count"] == 3
    assert sc1["has_more"] is True
    assert sc1["next_offset"] == 3
    assert sc1["total_count"] is None

    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=3, offset=3)
    )
    sc2 = page2.structured_content
    assert isinstance(sc2, dict)
    assert sc2["count"] == 3
    assert sc2["has_more"] is True
    assert sc2["next_offset"] == 6

    page3 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=3, offset=6)
    )
    sc3 = page3.structured_content
    assert isinstance(sc3, dict)
    assert sc3["count"] == 1
    assert sc3["has_more"] is False
    assert sc3["next_offset"] is None
    assert sc3["total_count"] == 7
    text3 = "".join(getattr(content, "text", "") for content in page3.content)
    assert "img_6.png" in text3
    # 各页命中缓存回放，底层目录扫描仅种子一趟。
    assert scan_calls == [8]


async def test_browse_images_hit_path_predicate_bounded_by_quota(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中回放的读权限复核凑满当页配额即停，考查成本不随缓存窗口规模放大。

    全窗过滤会使每页请求对整个缓存窗口逐条跑越界判定；
    以计数替身锁定考查条数等于 offset+limit+1，分页结果仍按窗口事实判定。
    """
    images_root = _seed_images_root(workspace_root)
    for i in range(12):
        (images_root / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # 种子请求扫到目录末尾，缓存完整窗口。
    seed = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=12, offset=0)
    )
    assert seed.structured_content["count"] == 12

    from seedream_mcp.utils.io import path as io_path

    real_within = io_path.is_within_resolved
    entry_checks = {"count": 0}

    def _counting_within(path: Path, scope: Path) -> bool:
        if str(path).endswith(".png"):
            entry_checks["count"] += 1
        return real_within(path, scope)

    monkeypatch.setattr(browse_core_module, "is_within_resolved", _counting_within)

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )

    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["count"] == 2
    assert sc["has_more"] is True
    assert sc["next_offset"] == 2
    # 谓词考查条数 = offset+limit+1 = 3，而非整个 12 条缓存窗口。
    assert entry_checks["count"] == 3


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
    """handler 在目录解析完成后抛未预期异常时，兜底 structuredContent 回显已解析目录。"""

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
    images_root = _seed_images_root(workspace_root)

    def _raise_permission(path: object) -> NoReturn:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "scandir", _raise_permission)

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
    import seedream_mcp.utils.io.scan as path_module

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
    import seedream_mcp.utils.io.scan as path_module

    images_root = _seed_images_root(workspace_root)
    early = images_root / "a"
    early.mkdir()
    (early / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    late = images_root / "b"
    late.mkdir()
    for i in range(20):
        (late / f"note_{i:02d}.txt").write_bytes(b"x")
    # 预算使子目录 a 完整产出 1 图、子目录 b 触发截断，页内条目为部分结果。
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
        image_pairs=[(image, image.resolve())],
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
        image_pairs=[(image, image)],
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
        image_pairs=[(image, image)],
        show_details=False,
    )

    assert entries[0]["path"] == image.as_posix()
    assert lines == [f"1. {tmp_path.as_posix()}/a b.png"]


def test_build_display_entries_preserves_backslash_in_posix_filename() -> None:
    """POSIX 含反斜杠文件名的条目路径仅归一分隔符，字面反斜杠保持原样可回流。"""
    image = cast("Path", PurePosixPath("/tmp_dir/a\\b.png"))

    lines, entries = browse_core_module._build_display_entries(
        image_pairs=[(image, image)],
        show_details=False,
    )

    assert entries[0]["path"] == "/tmp_dir/a\\b.png"
    assert lines == ["1. /tmp_dir/a\\b.png"]


async def test_browse_images_surrogate_filename_does_not_break_serialization(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文件名携带未配对代理时浏览响应整体成功，不再翻为序列化失败。

    NTFS 可存代理文件名，Linux 文件名字节经代理保留解码（surrogateescape）亦可
    产此类路径；代理在结果装配点剥离后 path 可 UTF-8 编码，代理本就不可回流，
    回流语义不受影响。
    """
    images_root = _seed_images_root(workspace_root)
    clean = images_root / "ok.png"
    clean.write_bytes(b"\x89PNG\r\n\x1a\n")
    surrogate = "\udcff"
    dirty = images_root / f"bad{surrogate}.png"

    def _fake_find(**kwargs: object) -> list[Path]:
        return [dirty, clean]

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _fake_find)

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    entries = sc["images"]
    assert len(entries) == 2
    dirty_entry = entries[0]
    assert surrogate not in dirty_entry["path"]
    dirty_entry["path"].encode("utf-8")
    assert dirty_entry["path"].endswith("bad.png")
    # 其余条目不受影响，保持原样可回流
    assert entries[1]["path"] == clean.resolve().as_posix()
    # 整个响应可经严格 JSON 序列化，含代理文件名不再抛 PydanticSerializationError
    result.model_dump_json().encode("utf-8")


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
    assert len(directory) <= 1024  # schema 侧 max_length 内，进入 handler 触发拒绝。

    result = await handle_browse_images(BrowseImagesInput(directory=directory))

    assert result.is_error is True
    # 摘要与 JSON 镜像两条文本通道均不得携带敏感样本，镜像经数据通道净化掩码。
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "secret" not in text
    summary = cast(TextContent, result.content[0]).text
    assert "目录路径无效" in summary
    # 截断保留前 500 字符并附带截断标注，上限按标注开销放宽。
    assert len(summary) <= 540
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
    # 摘要与 JSON 镜像两条文本通道均不得携带敏感样本，镜像经数据通道净化掩码。
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


async def test_browse_images_resolve_failed_entry_keeps_has_more_true(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中扫描早停且窗口内有条目 resolve 失败时 has_more 仍真，尾部图片翻页可达。

    resolve 失败的条目在扫描层被剔除，早停判定若按剔除后条目数推断
    会把命中限额误判为扫尽，has_more 假阴性使后续图片静默不可见。末页窗口已扫尽：
    has_more 按事实判定 False，总数因少计不可信维持 None，各页标记携带跳过计数。
    """
    images_root = _seed_images_root(workspace_root)
    # bad 居首稳定落入首个早停窗口，五张真图供三页翻完。
    (images_root / "0_bad.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    for name in ("a.png", "b.png", "c.png", "d.png", "e.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    _install_failing_resolve_for(monkeypatch, "0_bad.png")

    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )
    sc1 = page1.structured_content
    assert isinstance(sc1, dict)
    assert sc1["count"] == 2
    assert sc1["has_more"] is True
    assert sc1["next_offset"] == 2
    assert sc1["skipped_count"] == 1
    text1 = "".join(getattr(content, "text", "") for content in page1.content)
    assert "0_bad.png" not in text1
    assert "部分图片暂时无法访问（跳过 1 张）" in text1

    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=2)
    )
    sc2 = page2.structured_content
    assert isinstance(sc2, dict)
    assert sc2["count"] == 2
    assert sc2["has_more"] is True

    page3 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=4)
    )
    sc3 = page3.structured_content
    assert isinstance(sc3, dict)
    assert sc3["count"] == 1
    # 窗口已扫尽：has_more 按事实判定 False，总数少计不可信维持 None 与可见标记。
    assert sc3["has_more"] is False
    assert sc3["next_offset"] is None
    assert sc3["total_count"] is None
    assert sc3["skipped_count"] == 1
    text3 = "".join(getattr(content, "text", "") for content in page3.content)
    assert "e.png" in text3
    assert "部分图片暂时无法访问（跳过 1 张）" in text3


async def test_browse_images_resolve_failure_run_keeps_pagination_complete(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续多个 resolve 失败条目占首窗过半时翻页仍完整，短列表不得误报为完整页。

    扫描窗口增长若按剔除后的条目数驱动，连续坏段使增长停摆在坏段之前，
    首页报 count=1、has_more=False，坏段后的图片在任何 offset 下永久不可见。
    """
    images_root = _seed_images_root(workspace_root)
    for name in ("0_bad1.png", "0_bad2.png", "a.png", "b.png", "c.png", "d.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    _install_failing_resolve_for(monkeypatch, "0_bad1.png", "0_bad2.png")

    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )
    sc1 = page1.structured_content
    assert isinstance(sc1, dict)
    assert sc1["count"] == 2
    assert sc1["has_more"] is True
    assert sc1["next_offset"] == 2
    text1 = "".join(getattr(content, "text", "") for content in page1.content)
    assert "0_bad1.png" not in text1

    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=2)
    )
    sc2 = page2.structured_content
    assert isinstance(sc2, dict)
    assert sc2["count"] == 2
    # 窗口已扫尽：has_more 按事实判定 False，总数少计不可信维持 None 与可见标记。
    assert sc2["has_more"] is False
    assert sc2["total_count"] is None
    assert sc2["next_offset"] is None
    assert sc2["skipped_count"] == 2
    text2 = "".join(getattr(content, "text", "") for content in page2.content)
    assert "c.png" in text2
    assert "d.png" in text2
    assert "部分图片暂时无法访问（跳过 2 张）" in text2


async def test_browse_images_unresolvable_window_still_reaches_later_entries(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首个扫描窗口全为 resolve 失败条目时不早停，窗口之后的图片仍可见。

    扫描层若把剔除后的空窗判为扫尽，
    坏段占满整个窗口会把空页误报为 total_count=0 的完整结果。
    """
    images_root = _seed_images_root(workspace_root)
    for name in ("0_bad1.png", "0_bad2.png", "0_bad3.png", "a.png", "b.png", "c.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    _install_failing_resolve_for(monkeypatch, "0_bad1.png", "0_bad2.png", "0_bad3.png")

    page1 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )
    sc1 = page1.structured_content
    assert isinstance(sc1, dict)
    assert sc1["count"] == 2
    assert sc1["has_more"] is True

    page2 = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=2)
    )
    sc2 = page2.structured_content
    assert isinstance(sc2, dict)
    assert sc2["count"] == 1
    # 窗口已扫尽：has_more 按事实判定 False，总数少计不可信维持 None。
    assert sc2["has_more"] is False
    assert sc2["total_count"] is None


def test_resolve_failure_negative_cache_skips_retry_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve 失败条目按 TTL 负缓存：TTL 内再次扫描不再逐条重试阻塞 resolve。"""
    from seedream_mcp.utils.io import scan as io_scan

    failure_counts = _install_failing_resolve_for(monkeypatch, "bad.png")
    io_scan.reset_directory_scan_cache()
    bad_entry = Path("definitely/missing/bad.png")
    try:
        assert io_scan._resolve_scan_pairs([bad_entry]) == []
        assert failure_counts == {"bad.png": 1}
        # TTL 内的第二次扫描直接跳过负缓存条目，不再触发 resolve。
        assert io_scan._resolve_scan_pairs([bad_entry]) == []
        assert failure_counts == {"bad.png": 1}
    finally:
        io_scan.reset_directory_scan_cache()


def test_resolve_negative_cache_absent_key_not_recent_near_clock_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未登记键不判近期失败：开机初期 monotonic 接近 0，缺省 0.0 不可当时间戳。"""
    from seedream_mcp.utils.io import scan as io_scan

    # 时钟钉在 10.0 模拟开机初期的 monotonic 读数，未登记键若以 0.0 兜底会误判近期失败。
    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    failure_counts = _install_failing_resolve_for(monkeypatch, "bad.png")
    good_entry = Path("fresh/good.png")
    bad_entry = Path("definitely/missing/bad.png")
    io_scan.reset_directory_scan_cache()
    try:
        assert len(io_scan._resolve_scan_pairs([good_entry])) == 1
        assert io_scan._resolve_scan_pairs([bad_entry]) == []
        # 同窗口内再次扫描经负缓存略过失败键，不再触发 resolve。
        assert io_scan._resolve_scan_pairs([bad_entry]) == []
        assert failure_counts == {"bad.png": 1}
    finally:
        io_scan.reset_directory_scan_cache()


def test_scan_rescan_stops_when_no_new_resolvable_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """坏段贯穿扩窗时连续两轮零新增即停：单次调用不再几何级重列目录，停滞以成因显式外传。"""
    from seedream_mcp.utils.io import scan as io_scan

    images_root = tmp_path / "images"
    images_root.mkdir()
    bad_names = [f"bad{index:02d}.png" for index in range(12)]
    for name in bad_names:
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    _install_failing_resolve_for(monkeypatch, *bad_names)
    scan_calls: list[int] = []

    def _counting_scan(**kwargs: Any) -> list[Path]:
        limit = kwargs["limit"]
        assert isinstance(limit, int)
        scan_calls.append(limit)
        return io_scan.find_images_in_directory(**kwargs)

    io_scan.reset_directory_scan_cache()
    try:
        result = io_scan.cached_find_images_in_directory(
            resolved_dir=images_root,
            recursive=False,
            max_depth=1,
            format_filter=None,
            scan_limit=3,
            scanner=_counting_scan,
        )
        # 首轮扫原始窗口，连续两轮扩窗零新增即停，第四轮不再重列。
        assert result.images == []
        assert result.complete is False
        assert result.incomplete_cause is ScanIncompleteCause.STALLED
        assert len(scan_calls) == 3
    finally:
        io_scan.reset_directory_scan_cache()


async def test_browse_images_stalled_window_terminates_under_mtime_churn(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全部条目 resolve 失败且目录 mtime 每轮变化时补扫循环终止，不无限重试。

    扫描层连续两轮扩窗零新增返回短窗使 dropped 恒为零，修复前 scan_limit 永不增长、
    缓存又因 mtime 持续过期，while 循环无限重扫同一目录；
    末轮翻倍扫到目录末尾以 SKIPPED 成因出口，扫描限额序列同时锁定单次调用仅三趟枚举、
    无放大重扫。
    """
    from seedream_mcp.utils.io import scan as io_scan

    images_root = _seed_images_root(workspace_root)
    bad_names = [f"stall_{index}.png" for index in range(6)]
    for name in bad_names:
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    _install_failing_resolve_for(monkeypatch, *bad_names)
    real_scan = io_scan.find_images_in_directory
    scan_limits: list[int] = []
    churn_ns = {"next": int((time.time() - 3600.0) * 1_000_000_000)}

    def _churning_scan(**kwargs: Any) -> list[Path]:
        limit = kwargs["limit"]
        assert isinstance(limit, int)
        scan_limits.append(limit)
        # 模拟并发自动保存写入使目录 mtime 每轮前移、缓存持续过期；递增步长 3 秒超粗粒度时间戳窗口，每轮必变。
        churn_ns["next"] += 3_000_000_000
        os.utime(images_root, ns=(churn_ns["next"], churn_ns["next"]))
        return real_scan(**kwargs)

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _churning_scan)

    result = await asyncio.wait_for(
        handle_browse_images(BrowseImagesInput(directory=".", recursive=False, limit=2)),
        timeout=10.0,
    )

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "empty"
    assert sc["count"] == 0
    assert sc["total_count"] is None
    # 末轮翻倍扫到目录末尾：窗口已扫尽，has_more 按事实判定 False 并携带跳过计数。
    assert sc["has_more"] is False
    assert sc["skipped_count"] == 6
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "部分图片暂时无法访问（跳过 6 张）" in text
    # 首轮与首次扩窗零新增后，末轮翻倍扫到目录末尾即止：仅三趟枚举，不再继续重扫。
    assert scan_limits == [3, 6, 12]

    # mtime 持续变化下重复请求同样终止，不随缓存过期逐轮放大。
    again = await asyncio.wait_for(
        handle_browse_images(BrowseImagesInput(directory=".", recursive=False, limit=2)),
        timeout=10.0,
    )
    again_sc = again.structured_content
    assert isinstance(again_sc, dict)
    assert again_sc["status"] == "empty"
    assert scan_limits == [3, 6, 12, 3, 6, 12]


async def test_browse_images_stalled_short_page_marks_incomplete(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """坏条目剔除的短页如实标注结果不完整：总数置未知，不以低报精确值声称完备。

    坏段吞没已得条目之外的窗口时配额未填满即退出；
    末轮翻倍扫到目录末尾使扫尽事实可确定，has_more 按事实判定 False、标记携带跳过计数。
    """
    images_root = _seed_images_root(workspace_root)
    (images_root / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    bad_names = [f"z_bad_{index}.png" for index in range(5)]
    for name in bad_names:
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    _install_failing_resolve_for(monkeypatch, *bad_names)

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False, limit=2))

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "completed"
    assert sc["count"] == 1
    assert sc["total_count"] is None
    assert sc["has_more"] is False
    assert sc["next_offset"] is None
    assert sc["skipped_count"] == 5
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "a.png" in text
    assert "仍有更多" not in text
    assert "部分图片暂时无法访问（跳过 5 张）" in text


async def test_browse_images_negative_cache_hit_marks_incomplete(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负缓存命中的条目被静默略过时页面携带标记：总数置未知，跳过计数随结果外传。

    网络挂载抖动使单张图解析失败一次后，若无标记机制，15 秒负缓存窗口内
    浏览该目录会静默少一张且页面看起来完整直到 TTL 过期；窗口扫尽事实
    可确定，has_more 按事实判定 False。
    """
    images_root = _seed_images_root(workspace_root)
    for name in ("0_bad.png", "a.png", "b.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    failure_counts = _install_failing_resolve_for(monkeypatch, "0_bad.png")

    async def _browse() -> CallToolResult:
        return await handle_browse_images(
            BrowseImagesInput(directory=".", recursive=False, limit=10)
        )

    first = await _browse()
    sc1 = first.structured_content
    assert isinstance(sc1, dict)
    assert sc1["count"] == 2
    assert sc1["total_count"] is None
    assert sc1["has_more"] is False
    assert sc1["next_offset"] is None
    assert sc1["skipped_count"] == 1
    text1 = "".join(getattr(content, "text", "") for content in first.content)
    assert "部分图片暂时无法访问（跳过 1 张）" in text1

    # 第二次调用经负缓存略过坏条目，标记与跳过计数仍出现且不再重试 resolve。
    second = await _browse()
    sc2 = second.structured_content
    assert isinstance(sc2, dict)
    assert sc2["count"] == 2
    assert sc2["has_more"] is False
    assert sc2["skipped_count"] == 1
    text2 = "".join(getattr(content, "text", "") for content in second.content)
    assert "部分图片暂时无法访问（跳过 1 张）" in text2
    assert failure_counts == {"0_bad.png": 1}


async def test_browse_consumes_stall_signal_without_rescan_round(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """扫描层停滞成因直接进入分页：单次调用后短页如实标注未扫尽。

    配额填充收敛扫描层后 browse 不再叠加补扫循环，替身 cached 层声明单个
    (条目, 终点, 成因) 三元组窗口，锁定单层契约：停滞信号不触发额外扫描轮，
    末页成因未知是否扫尽时 has_more 置未知。
    窗口内越界条目经谓词剔除且无 resolve 失败，停滞标记按永久不可读表述。
    """
    outside_names = [f"out_{i}" for i in range(4)]
    seeding = _seed_rescan_layout(
        tmp_path, monkeypatch, outside_names=outside_names, workspace_names=["a", "b"]
    )
    out_paths = [seeding.images[f"out_{i}"] for i in range(4)]
    a_path, b_path = seeding.images["a"], seeding.images["b"]
    windows: list[tuple[list[Path], bool, ScanIncompleteCause | None]] = [
        (out_paths[:3] + [a_path, b_path], False, ScanIncompleteCause.STALLED),
    ]
    scan_limits: list[int] = []

    monkeypatch.setattr(
        browse_core_module,
        "cached_find_images_in_directory",
        _fake_cached_find(windows, scan_limits),
    )

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )

    assert windows == []
    # 单次调用即止：停滞信号由扫描层上报，browse 不追加重扫轮。
    assert scan_limits == [3]
    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "completed"
    assert sc["count"] == 2
    assert sc["total_count"] is None
    assert sc["has_more"] is None
    assert sc["next_offset"] is None
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "a.png" in text
    assert "b.png" in text
    assert "部分图片不在读取范围内，结果可能不完整" in text
    assert "暂时无法访问" not in text


async def test_browse_stalled_by_out_of_scope_drops_reports_permanent_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """谓词剔除致扩窗停滞的标记表述永久不可读，不以「暂时」诱导模型重试。

    目录条目 resolve 后全部越出读权限时扩窗零进展判 STALLED 且无 resolve 失败，
    越界为永久状态，按瞬时提示重试必然命中同一停滞。
    """
    outside_names = [f"out_{index:02d}" for index in range(12)]
    seeding = _seed_rescan_layout(
        tmp_path, monkeypatch, outside_names=outside_names, workspace_names=[]
    )
    scan_order = [seeding.images[name] for name in outside_names]

    def _fake_scan(**kwargs: object) -> list[Path]:
        limit = kwargs["limit"]
        assert isinstance(limit, int)
        return scan_order[:limit]

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _fake_scan)

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "empty"
    assert sc["count"] == 0
    assert sc["total_count"] is None
    assert sc["has_more"] is None
    assert sc["next_offset"] is None
    assert sc["skipped_count"] is None
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "部分图片不在读取范围内，结果可能不完整" in text
    assert "暂时无法访问" not in text


async def test_browse_stalled_by_resolve_failures_keeps_transient_marker(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve 失败致扩窗停滞的标记维持「暂时」表述：负缓存到期重试仍可能恢复。"""
    images_root = _seed_images_root(workspace_root)
    bad_names = [f"stall_{index:02d}.png" for index in range(12)]
    for name in bad_names:
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    _install_failing_resolve_for(monkeypatch, *bad_names)

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "empty"
    assert sc["count"] == 0
    assert sc["total_count"] is None
    assert sc["has_more"] is None
    assert sc["next_offset"] is None
    assert sc["skipped_count"] == 12
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "部分图片暂时无法访问，结果可能不完整" in text
    assert "不在读取范围内" not in text


async def test_browse_single_scan_call_fills_quota_with_predicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配额填充由扫描层经谓词完成后 browse 单次调用取回足量窗口，分页按事实判定。

    替身窗口含界外前缀条目，经 include 谓词过滤后仍满足配额；
    分页元数据按窗口事实取值，完备页不误标结果不完整。
    """
    outside_names = [f"out_{i}" for i in range(6)]
    seeding = _seed_rescan_layout(
        tmp_path, monkeypatch, outside_names=outside_names, workspace_names=["a", "b", "c"]
    )
    out_paths = [seeding.images[f"out_{i}"] for i in range(6)]
    a_path, b_path, c_path = seeding.images["a"], seeding.images["b"], seeding.images["c"]
    windows: list[tuple[list[Path], bool, ScanIncompleteCause | None]] = [
        (out_paths[:4] + [a_path, b_path, c_path], False, None),
    ]
    scan_limits: list[int] = []

    monkeypatch.setattr(
        browse_core_module,
        "cached_find_images_in_directory",
        _fake_cached_find(windows, scan_limits),
    )

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
    )

    assert windows == []
    assert scan_limits == [3]
    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "completed"
    assert sc["count"] == 2
    assert sc["has_more"] is True
    assert sc["next_offset"] == 2
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "a.png" in text
    assert "b.png" in text
    assert "部分图片暂时无法访问" not in text
    assert "目录条目过多" not in text
    assert "仍有更多" in text


async def test_browse_images_out_of_scope_warning_aggregates_across_rescan_rounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """越界告警覆盖整个补扫过程：多轮补扫聚合为单条含累计总量的告警。

    聚合若只覆盖单轮，越界条目多的目录单次调用会产生 O(轮数) 条同类告警，
    违背防刷屏意图；补扫轮次由注入扫描器返回越界条目居首的有序列表稳定复现。
    """
    outside_names = [f"out_{i}" for i in range(6)]
    workspace_names = [f"img_{i}" for i in range(3)]
    seeding = _seed_rescan_layout(
        tmp_path, monkeypatch, outside_names=outside_names, workspace_names=workspace_names
    )
    scan_order = [seeding.images[name] for name in outside_names + workspace_names]

    def _fake_scan(**kwargs: object) -> list[Path]:
        limit = kwargs["limit"]
        assert isinstance(limit, int)
        return scan_order[:limit]

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _fake_scan)

    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        result = await handle_browse_images(
            BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
        )

    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["count"] == 2
    out_of_scope_warnings = [w for w in warnings if "越界图片路径" in w]
    assert len(out_of_scope_warnings) == 1
    assert "越界图片路径 6 条" in out_of_scope_warnings[0]


async def test_browse_images_out_of_scope_warning_survives_scan_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补扫轮抛异常时越界告警仍落日志：计数不可得，改落无计数表述。"""

    outside_names = [f"out_{i}" for i in range(6)]
    workspace_names = [f"img_{i}" for i in range(3)]
    seeding = _seed_rescan_layout(
        tmp_path, monkeypatch, outside_names=outside_names, workspace_names=workspace_names
    )
    scan_order = [seeding.images[name] for name in outside_names + workspace_names]
    calls = {"count": 0}

    def _fake_scan(**kwargs: object) -> list[Path]:
        limit = kwargs["limit"]
        assert isinstance(limit, int)
        if calls["count"] == 0:
            calls["count"] += 1
            return scan_order[:limit]
        raise OSError("scan exploded mid-rescan")

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _fake_scan)

    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        result = await handle_browse_images(
            BrowseImagesInput(directory=".", recursive=False, limit=2, offset=0)
        )

    assert result.is_error is True
    out_of_scope_warnings = [w for w in warnings if "越界" in w]
    assert len(out_of_scope_warnings) == 1
    assert "越界图片剔除计数不可得" in out_of_scope_warnings[0]
    # 异常路径不回退到带计数表述，防止与 excluded_count 口径分叉。
    assert [w for w in warnings if "越界图片路径" in w] == []


def test_scan_and_filter_directory_passes_include_and_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_scan_and_filter_directory 单次进入扫描层：配额与读权限谓词经参数下发。

    配额填充与越界剔除收敛扫描层后，本函数只组装配额（remaining）与 include
    谓词并透传结果信号；扫描层不接受谓词或配额时扩窗契约在此暴露。
    """
    seeding = _seed_rescan_layout(
        tmp_path, monkeypatch, outside_names=["out_0", "out_1"], workspace_names=["a", "b"]
    )
    captured: dict[str, object] = {}

    def _fake_cached_find(**kwargs: object) -> ScanResult:
        captured.update(kwargs)
        return ScanResult(
            images=[(seeding.images["a"], seeding.images["a"])],
            complete=True,
            incomplete_cause=None,
        )

    monkeypatch.setattr(browse_core_module, "cached_find_images_in_directory", _fake_cached_find)

    result = browse_core_module._scan_and_filter_directory(
        resolved_dir=seeding.workspace.resolve(),
        recursive=False,
        max_depth=3,
        format_filter=None,
        remaining=7,
        read_scope=[seeding.workspace.resolve()],
        unreadable_dirs=[],
    )

    assert captured["scan_limit"] == 7
    from seedream_mcp.utils.io import scan as io_scan

    # scanner 取 browse 作用域的同名属性，外部替换该属性即可替换底层扫描。
    assert captured["scanner"] is io_scan.find_images_in_directory
    # scope_signature 与 include 同为接线层下发参数：删除传参的回归在此当场暴露。
    assert captured["scope_signature"] == io_scan.read_scope_signature(
        [seeding.workspace.resolve()]
    )
    include = cast("Callable[[tuple[Path, Path]], bool]", captured["include"])
    assert include((seeding.images["a"], seeding.images["a"])) is True
    assert include((seeding.images["out_0"], seeding.images["out_0"])) is False
    assert result.images == [(seeding.images["a"], seeding.images["a"])]
    assert result.complete is True
    assert result.incomplete_cause is None


async def test_browse_images_out_of_bounds_symlink_keeps_pagination_reachable(
    workspace_root: Path,
) -> None:
    """目录含 1 个越界符号链接与足量真图：has_more 与翻页正确、尾部可达。

    真实符号链接的端到端路径：符号链接文件不列入扫描结果，即使列入也须在越界
    复核被剔除且不占配额。Windows 符号链接创建权限不足时按既有先例 skip，
    剔除占额语义由注入扫描器的用例稳定覆盖。
    """
    # 越界目标置于共享 basetemp 之外的独占临时目录。
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


# ==================== bounds_scope 与读权限求交 ====================


async def test_browse_images_bounds_scope_wider_than_read_scope_dropped_entirely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超宽 bounds_scope 未落入读权限即整界剔除：界内图片也不返回，结果为空。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    images_root = workspace / ".seedream" / "images"
    images_root.mkdir(parents=True)
    inside_img = images_root / "inside.png"
    inside_img.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False), bounds_scope=[tmp_path]
    )

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["status"] == "empty"
    assert sc["count"] == 0
    assert sc["images"] == []
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "inside.png" not in text


async def test_browse_images_bounds_scope_narrower_filters_at_source(
    workspace_root: Path,
) -> None:
    """窄于读权限的 bounds_scope 在源头剔除界内但替代界外的条目。"""
    images_root = _seed_images_root(workspace_root)
    for name in ("keep.png", "drop.png"):
        (images_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    subdir = images_root / "sub"
    subdir.mkdir()
    (subdir / "keep.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=True), bounds_scope=[subdir]
    )

    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["count"] == 1
    assert sc["images"][0]["path"] == (subdir / "keep.png").resolve().as_posix()


async def test_browse_images_rejects_empty_bounds_scope(workspace_root: Path) -> None:
    """空 bounds_scope 为编程错误：以工具错误结果拒绝，不静默清空结果。"""
    images_root = _seed_images_root(workspace_root)
    (images_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False), bounds_scope=[]
    )

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "bounds_scope 不能为空列表" in text
