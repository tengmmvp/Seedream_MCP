"""browse_images 工具结构化结果、分页元数据与工作区越界拒绝测试。

多数用例直连 handle_browse_images，工作区边界经 workspace_root fixture 注入的
SEEDREAM_WORKSPACE_ROOT 回退取得；走完整 MCPServer 调用链的用例以
_NoRootsContext 提供无会话的替身上下文。需要越界目录或越界文件的用例将工作区与
越界路径同置于 tmp_path 之下，不污染共享 basetemp。
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from seedream_mcp.resources import mcp
from seedream_mcp.tools import BrowseImagesInput
from seedream_mcp.tools.core import browse as browse_core_module
from seedream_mcp.tools.core.browse import _FALLBACK_BOUNDARY_PLACEHOLDER
from seedream_mcp.tools.impl import browse_images as browse_images_module
from seedream_mcp.tools.impl.browse_images import handle_browse_images


async def test_browse_images_returns_structured_success(workspace_root: Path) -> None:
    (workspace_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "completed"
    assert result.structured_content["count"] == 1
    assert any(isinstance(content, TextContent) for content in result.content)


async def test_browse_images_returns_empty_when_no_files(workspace_root: Path) -> None:
    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "empty"
    assert result.structured_content["count"] == 0


async def test_browse_images_rejects_out_of_workspace_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside_dir_for_test"
    outside_dir.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    result = await handle_browse_images(BrowseImagesInput(directory=str(outside_dir)))

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"


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
    """
    (workspace_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _fail_find(*args, **kwargs):
        raise AssertionError("无有效后缀时不应触发目录扫描")

    monkeypatch.setattr(browse_core_module, "find_images_in_directory", _fail_find)

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, format_filter=[])
    )

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"
    assert result.structured_content["count"] == 0
    assert result.structured_content["error"]["type"] == "browse_failed"


async def test_browse_images_fallback_error_preserves_format_filter(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外层兜底错误分支回显经同一规则过滤的 format_filter，不丢失用户原始输入。"""

    async def _exploding_request(params, ctx, **kwargs):
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
    for name in ("a.png", "b.png", "c.png"):
        (workspace_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

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
    for name in ("a.png", "b.png", "c.png"):
        (workspace_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(
        BrowseImagesInput(directory=".", recursive=False, limit=2, offset=4)
    )
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert result.is_error is True
    assert sc["status"] == "failed"
    assert sc["count"] == 0
    assert sc["error"]["type"] == "browse_failed"
    assert "目录共有 3 张图片" in sc["error"]["message"]
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "0 <= offset < 3" in text


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

    structuredContent 回显用户原始 format_filter。用 .svg 而非任务示例的 .gif：
    formats.py 的 SUPPORTED_IMAGE_EXTENSIONS 含 .gif，若用 .gif 会落入
    supported_only 非空分支而不触发 format_filter_exhausted，无法覆盖区分消息。
    .svg 不在支持集合内，可真正命中 exhausted 分支。断言区分消息含
    "均不在支持列表"与"支持"，status 为 failed 且 isError 为 True；format_filter 保留
    用户原始非空列表 [".svg"] 供回显，不缩减为空列表。
    """
    result = await handle_browse_images(BrowseImagesInput(directory=".", format_filter=[".svg"]))

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"
    assert result.structured_content["format_filter"] == [".svg"]
    assert result.structured_content["error"]["type"] == "browse_failed"
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
    for name in ("a.png", "b.png"):
        (workspace_root / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await mcp.call_tool(
        "browse_images",
        {"directory": ".", "recursive": False, "limit": 2, "offset": 5},
        context=_NoRootsContext(),
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["error"]["type"] == "browse_failed"


async def test_browse_images_format_filter_error_signal_visible_to_client(
    workspace_root: Path,
) -> None:
    """format_filter 全不支持的错误信号经 MCPServer 调用链可被客户端识别。"""
    result = await mcp.call_tool(
        "browse_images",
        {"directory": ".", "format_filter": [".svg"]},
        context=_NoRootsContext(),
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["error"]["type"] == "browse_failed"


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


async def test_browse_images_fallback_preserves_resolved_directories(
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """impl 在目录解析完成后抛未预期异常时，兜底 structuredContent 回显已解析目录。

    resolved_directories 列表由外层创建并共享给 core 流水线；兜底分支不再恒为空列表。
    以会话 Roots 场景断言真实路径回显：env/CWD 回退场景的路径回显被占位符遮蔽，
    无法承载本断言。
    """
    from seedream_mcp.utils.io.io_path import _WORKSPACE_ROOTS_VAR

    def _exploding_display_entries(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(browse_core_module, "_build_display_entries", _exploding_display_entries)
    (workspace_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    token = _WORKSPACE_ROOTS_VAR.set((workspace_root.resolve(),))
    try:
        result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["resolved_directories"] == [str(workspace_root.resolve())]


async def test_browse_images_fallback_boundary_masks_paths_in_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无会话 Roots 时越界拒绝不回显 env/CWD 绝对路径。

    直接调用 handle_browse_images（未进入 workspace_roots_scope），边界经
    SEEDREAM_WORKSPACE_ROOT 回退取得。越界消息与 structuredContent 的
    workspace_roots 均以占位符替代，不向调用方暴露服务器本地目录结构。
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside_dir_for_masking_test"
    outside_dir.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    result = await handle_browse_images(BrowseImagesInput(directory=str(outside_dir)))

    assert result.is_error is True
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "服务器配置的工作区目录" in text
    assert str(workspace) not in text
    assert str(outside_dir.resolve().parent) not in text
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["workspace_roots"] == [_FALLBACK_BOUNDARY_PLACEHOLDER]
    # 越界场景目录解析未产出任何界内目录，保持空列表而非占位符。
    assert result.structured_content["resolved_directories"] == []


async def test_browse_images_fallback_boundary_masks_paths_on_success(
    workspace_root: Path,
) -> None:
    """无会话 Roots 的成功浏览同样遮蔽边界路径，展示层保持相对路径。"""
    (workspace_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    sc = result.structured_content
    assert isinstance(sc, dict)
    assert sc["workspace_roots"] == [_FALLBACK_BOUNDARY_PLACEHOLDER]
    assert sc["resolved_directories"] == [_FALLBACK_BOUNDARY_PLACEHOLDER]
    assert sc["images"][0]["path"] == "demo.png"


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

    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "empty"
    text = "".join(getattr(content, "text", "") for content in result.content)
    assert "目录不可读或无图片文件" in text
    assert "1 个目录（回退边界场景不回显路径）" in text
    assert str(workspace_root.resolve()) not in text


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
    """畸形时间戳使 fromtimestamp 抛 ValueError 时降级为「文件信息不可用」。

    不向调用方抛异常；stat 本身成功，降级分支须同时置空 size_mb 与 modified
    两键，避免半份详情误导调用方。以替身模块替换 browse 命名空间内的
    datetime 名字，使 datetime.datetime.fromtimestamp 抛 ValueError；内建
    datetime 类为不可变类型，无法直接对其打属性补丁。
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


def test_build_display_entries_sanitizes_file_name_credentials(
    workspace_root: Path,
) -> None:
    """含凭据样式片段的文件名经净化进入文本与结构化两条通道，不外泄片段。"""
    from seedream_mcp.utils.io.io_path import resolve_workspace_roots

    image = workspace_root / "img api_key=secret.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    resolved_roots = resolve_workspace_roots([workspace_root])

    lines, entries = browse_core_module._build_display_entries(
        images=[image],
        image_resolved_map={image: image.resolve()},
        resolved_roots=resolved_roots,
        show_details=False,
    )

    assert "secret" not in lines[0]
    assert "secret" not in entries[0]["path"]
    assert "***" in entries[0]["path"]


async def test_browse_images_invalid_directory_error_sanitized_and_truncated(
    workspace_root: Path,
) -> None:
    """无效目录错误消息经净化截断，不整体回显超长输入与凭据样式片段。

    以 // 前缀构造跨平台命中的 UNC 路径，normalize_path 在 resolve 前拒绝并携带
    完整原始路径抛 ValueError；错误消息须收敛到错误文本输出上限内，且输入中的
    api_key 裸值被脱敏。
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

    扫描层按 scan_limit 早停，窗口内的越界条目在扫描之后才被剔除；若无补扫，
    剔除项占满配额会使 has_more 假阴性、total_count 低报。经注入的扫描器返回
    越界条目居首的有序列表，稳定复现剔除占额场景：limit=2 且窗口内含 1 个越界
    条目时，首页须报 has_more 且次页取到尾部真图。
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
    target = workspace_root.parent / "outside_browse_symlink_target.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    link = workspace_root / "0_link.png"
    try:
        os.symlink(target, link)
    except (OSError, AttributeError):
        target.unlink(missing_ok=True)
        pytest.skip("当前进程无法创建符号链接（Windows 可能需要开发者模式或管理员）")

    try:
        for i in range(3):
            (workspace_root / f"img_{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

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
        target.unlink(missing_ok=True)


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
