"""图片浏览工具的 impl 处理器。

直接实现工作区图片扫描与分页，不经 ``execute_generation_handler`` 生成流水线；字段规则
由 schemas.BrowseImagesInput 单一定义。
"""

from __future__ import annotations

import asyncio
import datetime
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, TextContent

from ..core._helpers import _safe_ctx_log, _safe_report_progress
from ..core.schemas import BrowseImagesInput
from ...utils.errors import format_error_for_user
from ...utils.logging import get_logger
from ...utils.path_utils import (
    SUPPORTED_IMAGE_EXTENSIONS,
    _is_within_resolved,
    find_images_in_directory,
    get_relative_path,
    get_workspace_roots,
    is_path_within_any_base,
    is_path_within_base,
    normalize_path,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

logger = get_logger(__name__)


# ==================== 目录扫描缓存 ====================

# 进程级目录图片列表缓存，消除翻页重复文件系统扫描。
# 键为 (目录路径, recursive, max_depth, 格式过滤元组)，不含 limit：同目录同配置的不同翻页
# 共享一份全量有序列表，命中时返回浅拷贝供调用方切片，将深翻页从每页扫描降为首次扫描加
# O(1) 命中。值为 (目录 mtime_ns, 捕获时的 monotonic 秒, 全量有序图片列表)。非递归以目录
# mtime 失效，新增图片立即反映；递归因子目录变更不改顶层 mtime，用 TTL 失效，接受短时
# 陈旧换取翻页性能。单事件循环内各请求按目录串行 await，跨请求并发经由 GIL 保证 dict
# 读写原子性，最坏情况为缓存击穿即多请求各扫一次再覆写，仅影响性能。
_DIRECTORY_SCAN_CACHE: dict[
    tuple[str, bool, int, tuple[str, ...]], tuple[int | None, float, list[Path]]
] = {}
_DIRECTORY_SCAN_CACHE_MAX_ENTRIES = 64
_DIRECTORY_SCAN_CACHE_MAX_LIST_LEN = 2000
# 递归扫描缓存 TTL：子目录新增图片不改变顶层目录 mtime，以 TTL 兜底保证最终一致。
_DIRECTORY_SCAN_CACHE_TTL_SECONDS = 5.0


def _get_directory_mtime_ns(path: Path) -> int | None:
    """返回目录 mtime 纳秒值，stat 失败返回 None。"""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _cached_find_images_in_directory(
    *,
    resolved_dir: Path,
    recursive: bool,
    max_depth: int,
    format_filter: list[str] | None,
    scan_limit: int,
) -> list[Path]:
    """带进程级缓存的目录图片扫描，翻页共享全量列表缓存。

    缓存键不含 scan_limit，同目录同扫描配置的不同翻页共享一份全量有序列表，命中时返回
    浅拷贝供调用方切片，将深翻页从每页文件系统扫描降为首次扫描加 O(1) 命中。非递归扫描以
    目录 mtime 失效，新增图片立即反映；递归扫描因子目录变更不改变顶层 mtime，改用 TTL
    失效，接受短时陈旧换取翻页性能。未命中时以 scan_limit 早停扫描，仅在扫到目录末尾、
    结果数小于 scan_limit 时方缓存全量列表；超过上限的大目录不缓存，每次按 scan_limit 早停。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归最大深度。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        scan_limit: 扫描数量上限，用于未命中时的早停与是否扫到目录末尾的判定。

    Returns:
        排序后的图片路径列表，缓存命中时为全量，未命中时至多 scan_limit 条。
    """
    cache_key = (
        str(resolved_dir),
        recursive,
        max_depth,
        tuple(format_filter) if format_filter else (),
    )
    cached = _DIRECTORY_SCAN_CACHE.get(cache_key)
    if cached is not None:
        captured_mtime, captured_at, images = cached
        fresh = (
            time.monotonic() - captured_at < _DIRECTORY_SCAN_CACHE_TTL_SECONDS
            if recursive
            else _get_directory_mtime_ns(resolved_dir) == captured_mtime
        )
        if fresh:
            return list(images)
    # 扫描前捕获目录 mtime，使缓存写入的指纹与 images 自洽：扫描与 stat 之间若有并发
    # 写入，扫描后捕获的 mtime 会反映新增而 images 未含，命中时持续返回陈旧列表。递归
    # 扫描不依赖 mtime 失效，跳过捕获。
    base_mtime = _get_directory_mtime_ns(resolved_dir) if not recursive else None
    images = find_images_in_directory(
        directory=str(resolved_dir),
        recursive=recursive,
        max_depth=max_depth,
        extensions=format_filter,
        limit=scan_limit,
    )
    # 仅当扫到目录末尾、结果数小于 scan_limit 时 images 才是完整列表，方可缓存全量；
    # 等于 scan_limit 说明目录更大、仅取得前缀，不缓存以免翻页取到错误前缀。
    if len(images) < scan_limit and len(images) <= _DIRECTORY_SCAN_CACHE_MAX_LIST_LEN:
        if len(_DIRECTORY_SCAN_CACHE) >= _DIRECTORY_SCAN_CACHE_MAX_ENTRIES:
            # 驱逐最旧条目；并发 to_thread 下 next(iter())+pop 可能竞态抛 KeyError，捕获容错
            try:
                _DIRECTORY_SCAN_CACHE.pop(next(iter(_DIRECTORY_SCAN_CACHE)))
            except KeyError:
                pass
        # 递归扫描靠 TTL 失效故总是缓存，mtime 字段留空；非递归仅在 stat 成功时缓存
        if recursive or base_mtime is not None:
            _DIRECTORY_SCAN_CACHE[cache_key] = (base_mtime, time.monotonic(), images)
    return images


def _format_file_info(
    display_path: str, stat_path: Path, show_details: bool
) -> tuple[str, dict[str, Any]]:
    """格式化文件信息，返回展示文本与结构化详情字段。

    show_details 为真时读取文件大小与修改时间，文本格式为 "路径 | 大小 | 修改时间"，
    结构化详情含 size_mb 与 modified 两键；stat 失败时文本追加 "文件信息不可用"，两键置 None。
    show_details 为假时仅返回路径文本与空详情字典。

    Args:
        display_path: 展示给用户的文件路径字符串。
        stat_path: 用于读取文件属性的实际路径对象。
        show_details: 是否显示文件详细信息（大小、修改时间）。

    Returns:
        展示文本与结构化详情字段字典组成的二元组。
    """
    if not show_details:
        return display_path, {}
    try:
        stat_result = stat_path.stat()
    except OSError:
        return f"{display_path} | 文件信息不可用", {"size_mb": None, "modified": None}
    size_mb = stat_result.st_size / (1024 * 1024)
    # astimezone 将 naive 本地时间标注为本地时区，输出携带 UTC 偏移以消除时区歧义。
    mtime = (
        datetime.datetime.fromtimestamp(stat_result.st_mtime)
        .astimezone()
        .isoformat(sep=" ", timespec="seconds")
    )
    return (
        f"{display_path} | {size_mb:.2f} MB | 修改: {mtime}",
        {"size_mb": size_mb, "modified": mtime},
    )


def _build_browse_structured_result(
    *,
    status: str,
    workspace_roots: list[Path],
    directory: str,
    resolved_directories: list[Path],
    recursive: bool,
    max_depth: int,
    limit: int,
    offset: int,
    show_details: bool,
    format_filter: list[str] | None,
    success: bool = True,
    images: list[dict[str, Any]] | None = None,
    total_count: int | None = None,
    has_more: bool | None = None,
    next_offset: int | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """集中构建 browse_images 工具的 structuredContent，字段集与 BrowseImagesStructuredOutput 对齐。

    成功、空结果与失败三分支共用此构建，避免手工内联字典造成的字段漂移。失败分支以默认值
    填充非关键字段，符合 BrowseImagesStructuredOutput 全部字段可选的声明。
    """
    structured: dict[str, Any] = {
        "tool": "seedream_browse_images",
        "success": success,
        "status": status,
        "directory": directory,
        "resolved_directories": [str(item) for item in resolved_directories],
        "workspace_roots": [str(root) for root in workspace_roots],
        "images": images if images is not None else [],
        "count": len(images) if images is not None else 0,
        "total_count": total_count,
        "offset": offset,
        "has_more": has_more,
        "next_offset": next_offset,
        "recursive": recursive,
        "max_depth": max_depth,
        "limit": limit,
        "show_details": show_details,
        "format_filter": format_filter,
    }
    if error is not None:
        structured["error"] = error
    return structured


def _build_browse_error(
    *,
    workspace_roots: list[Path],
    directory: str,
    resolved_directories: list[Path],
    recursive: bool,
    max_depth: int,
    limit: int,
    offset: int,
    show_details: bool,
    format_filter: list[str] | None,
    message: str,
    status: str = "failed",
) -> CallToolResult:
    """集中构造 browse_images 工具的错误 CallToolResult。

    各错误分支共享 9 个状态字段与 isError=True 语义，仅 message 不同；通过此辅助函数
    统一构造，避免重复展开相同 kwargs。structuredContent.error.type 恒为 browse_failed，
    message 同时作为可见文本与结构化错误原因，二者保持一致。
    """
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent=_build_browse_structured_result(
            status=status,
            workspace_roots=workspace_roots,
            directory=directory,
            resolved_directories=resolved_directories,
            recursive=recursive,
            max_depth=max_depth,
            limit=limit,
            offset=offset,
            show_details=show_details,
            format_filter=format_filter,
            success=False,
            error={"type": "browse_failed", "message": message},
        ),
        isError=True,
    )


def _scan_and_filter_directory(
    *,
    resolved_dir: Path,
    recursive: bool,
    max_depth: int,
    format_filter: list[str] | None,
    remaining: int,
    resolved_roots: list[Path],
    seen_images: set[Path],
) -> list[tuple[Path, Path]]:
    """扫描单个目录并完成越界判定与去重，返回新增的 (原始路径, resolved 路径) 列表。

    扫描后的 resolve、越界判定与去重等文件系统相关计算集中在本函数同步执行，由调用方通过
    ``asyncio.to_thread`` 在线程内调用，使深翻页大 offset 或网络挂载目录下的 resolve 不再
    阻塞事件循环。``seen_images`` 跨目录共享以去重重叠根目录的重复图片；调用方按目录串行
    await，无并发写竞争。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归最大深度。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        remaining: 距 scan_limit 上限的剩余配额，新增条目不超过此值。
        resolved_roots: 已 resolve 的工作区根列表，用于越界判定。
        seen_images: 跨目录共享的已见原始路径集合，函数内就地更新。

    Returns:
        新增 (原始路径, resolved 路径) 元组列表，长度不超过 remaining。
    """
    matched_images = _cached_find_images_in_directory(
        resolved_dir=resolved_dir,
        recursive=recursive,
        max_depth=max_depth,
        format_filter=format_filter,
        scan_limit=remaining,
    )
    new_entries: list[tuple[Path, Path]] = []
    for image_path in matched_images:
        # 每张图片至多 resolve 一次；root 已 resolve，直接做 relative_to 比较。
        image_resolved = image_path.resolve()
        if not any(_is_within_resolved(image_resolved, root) for root in resolved_roots):
            logger.warning("检测到越界图片路径，已忽略: {}", image_path)
            continue
        if image_path in seen_images:
            continue
        seen_images.add(image_path)
        new_entries.append((image_path, image_resolved))
        if len(new_entries) >= remaining:
            break
    return new_entries


def _build_display_entries(
    *,
    images: list[Path],
    image_resolved_map: dict[Path, Path],
    resolved_roots: list[Path],
    show_details: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    """组装面向用户的展示文本与结构化图片条目。

    将 _is_within_resolved 命中查找、get_relative_path 与 _format_file_info 的 stat 等文件
    系统相关计算集中在本函数同步执行，由调用方通过 ``asyncio.to_thread`` 在线程内调用，使
    show_details 下网络挂载目录的 stat 不再阻塞事件循环。索引编号沿用 enumerate 语义：被
    忽略条目仍占位、保留原序号不重排，与历史行为一致。

    Args:
        images: 当前页图片原始路径列表。
        image_resolved_map: 原始路径到 resolved 路径的缓存，由扫描阶段填充。
        resolved_roots: 已 resolve 的工作区根列表，用于定位展示基准根。
        show_details: 是否在文本与结构化条目中附加文件大小与修改时间。

    Returns:
        (展示文本行列表, 结构化图片条目列表)，文本行不含 "图片列表:" 标题头。
    """
    lines: list[str] = []
    structured_images: list[dict[str, Any]] = []
    for idx, img in enumerate(images, 1):
        img_resolved = image_resolved_map[img]
        display_base = next(
            (root for root in resolved_roots if _is_within_resolved(img_resolved, root)),
            None,
        )
        if display_base is None:
            logger.warning("图片路径未命中任何工作区根目录，已忽略: {}", img)
            continue
        display_path = get_relative_path(img_resolved, str(display_base))
        detail_text, details = _format_file_info(display_path, img_resolved, show_details)
        lines.append(f"{idx}. {detail_text}")
        entry: dict[str, Any] = {"index": idx, "path": display_path}
        entry.update(details)
        structured_images.append(entry)
    return lines, structured_images


async def handle_browse_images(
    arguments: dict[str, Any],
    ctx: Context[Any, Any, Any] | None = None,
) -> CallToolResult:
    """处理图片浏览请求，扫描工作区内指定目录的图片文件并分页返回。

    仅允许访问 MCP Roots 授权的工作区目录；扫描结果按目录 mtime 缓存以加速翻页，切片
    offset+limit+1 张以判定 has_more。完整字段规则与默认值见 ``BrowseImagesInput``，本函数读取 arguments。
    未预期异常被外层捕获并降级为结构化错误，与生成类 ``execute_generation_handler`` 的错误
    结构对齐，不向调用方抛出。

    Args:
        arguments: 工具原始参数字典，结构见 ``BrowseImagesInput``。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 标准工具结果，含面向模型的图片列表文本与 structuredContent。
    """
    try:
        return await _handle_browse_images_impl(arguments, ctx)
    except Exception as exc:
        # 兜底：未预期异常降级为结构化错误，避免向调用方抛出原始异常。
        logger.error("浏览图片处理失败", exc_info=True)
        await _safe_report_progress(ctx, progress=100.0, message="浏览图片处理失败")
        user_message = format_error_for_user(exc)
        await _safe_ctx_log(ctx, "error", f"浏览图片失败：{user_message}")
        try:
            fallback_roots = get_workspace_roots()
        except Exception:
            fallback_roots = []
        return _build_browse_error(
            workspace_roots=fallback_roots,
            directory=str(arguments.get("directory") or "."),
            resolved_directories=[],
            recursive=bool(arguments.get("recursive", BrowseImagesInput.DEFAULT_RECURSIVE)),
            max_depth=arguments.get("max_depth", BrowseImagesInput.DEFAULT_MAX_DEPTH),
            limit=arguments.get("limit", BrowseImagesInput.DEFAULT_LIMIT),
            offset=arguments.get("offset", BrowseImagesInput.DEFAULT_OFFSET),
            show_details=bool(
                arguments.get("show_details", BrowseImagesInput.DEFAULT_SHOW_DETAILS)
            ),
            format_filter=None,
            message=f"浏览图片失败：{user_message}；请确认目录路径有效且位于工作区内。",
        )


async def _handle_browse_images_impl(
    arguments: dict[str, Any],
    ctx: Context[Any, Any, Any] | None = None,
) -> CallToolResult:
    """浏览工具主逻辑，由 ``handle_browse_images`` 外层兜底包裹。"""
    directory = arguments.get("directory") or "."
    requested_dir = str(directory)
    recursive = bool(arguments.get("recursive", BrowseImagesInput.DEFAULT_RECURSIVE))
    # max_depth/limit/offset 已由 BrowseImagesInput 的 pydantic 校验保证为 int，无需再 int() 包装。
    # 默认值引用 BrowseImagesInput 的类常量，保持字段默认单一来源。
    max_depth = arguments.get("max_depth", BrowseImagesInput.DEFAULT_MAX_DEPTH)
    limit = arguments.get("limit", BrowseImagesInput.DEFAULT_LIMIT)
    offset = arguments.get("offset", BrowseImagesInput.DEFAULT_OFFSET)
    format_filter = arguments.get("format_filter")
    # 格式过滤仅保留受支持的图片扩展名，避免以非图片后缀探测文件。
    # 全部后缀均不受支持时标记 format_filter_exhausted 并跳过扫描；此时 format_filter
    # 保留用户原始输入供 structuredContent 回显，不缩减为空列表。
    # 不能将空列表传给 find_images_in_directory，因其把空列表视为未限制而扫描全部。
    format_filter_exhausted = False
    if format_filter:
        supported_only = [ext for ext in format_filter if ext in SUPPORTED_IMAGE_EXTENSIONS]
        if supported_only:
            format_filter = supported_only
        else:
            format_filter_exhausted = True
    show_details = bool(arguments.get("show_details", BrowseImagesInput.DEFAULT_SHOW_DETAILS))

    workspace_roots = get_workspace_roots()
    resolved_dirs: list[Path] = []
    if not workspace_roots:
        message = "当前 MCP 会话未授权任何工作区目录，无法浏览本地文件。"
        await _safe_ctx_log(ctx, "warning", message)
        return _build_browse_error(
            workspace_roots=workspace_roots,
            directory=requested_dir,
            resolved_directories=resolved_dirs,
            recursive=recursive,
            max_depth=max_depth,
            limit=limit,
            offset=offset,
            show_details=show_details,
            format_filter=format_filter,
            message=message,
        )

    # 预解析工作区根：去重与展示阶段直接用 _is_within_resolved 与这些已 resolve 的 root
    # 比较，root 不再重复 resolve。每张图片也只 resolve 一次，结果缓存于 image_resolved_map。
    # 展示层与 structuredContent 仍回显原始 workspace_roots。
    resolved_roots: list[Path] = [root.resolve() for root in workspace_roots]

    raw_dir_path = Path(requested_dir)
    if raw_dir_path.is_absolute():
        try:
            absolute_dir = normalize_path(requested_dir)
        except ValueError as exc:
            message = f"目录路径无效: {exc}"
            return _build_browse_error(
                workspace_roots=workspace_roots,
                directory=requested_dir,
                resolved_directories=resolved_dirs,
                recursive=recursive,
                max_depth=max_depth,
                limit=limit,
                offset=offset,
                show_details=show_details,
                format_filter=format_filter,
                message=message,
            )
        if not is_path_within_any_base(absolute_dir, resolved_roots):
            allowed_roots = ", ".join(str(root) for root in workspace_roots)
            message = "目录超出允许范围。" f"仅允许浏览工作区目录: {allowed_roots}"
            return _build_browse_error(
                workspace_roots=workspace_roots,
                directory=requested_dir,
                resolved_directories=resolved_dirs,
                recursive=recursive,
                max_depth=max_depth,
                limit=limit,
                offset=offset,
                show_details=show_details,
                format_filter=format_filter,
                message=message,
            )
        resolved_dirs.append(absolute_dir)
    else:
        for root in resolved_roots:
            try:
                candidate = normalize_path(requested_dir, str(root))
            except ValueError:
                continue
            if not is_path_within_base(candidate, root):
                continue
            if candidate not in resolved_dirs:
                resolved_dirs.append(candidate)

    if not resolved_dirs:
        allowed_roots = ", ".join(str(root) for root in workspace_roots)
        message = "目录超出允许范围。" f"仅允许浏览工作区目录: {allowed_roots}"
        return _build_browse_error(
            workspace_roots=workspace_roots,
            directory=requested_dir,
            resolved_directories=resolved_dirs,
            recursive=recursive,
            max_depth=max_depth,
            limit=limit,
            offset=offset,
            show_details=show_details,
            format_filter=format_filter,
            message=message,
        )

    await _safe_ctx_log(
        ctx,
        "info",
        f"浏览图片：目录={requested_dir}, 递归={recursive}, 最大深度={max_depth}, 限制={limit}",
    )
    await _safe_report_progress(ctx, progress=20.0, message="开始扫描图片目录")

    logger.info(
        "浏览图片: dirs={}, recursive={}, max_depth={}, limit={}",
        resolved_dirs,
        recursive,
        max_depth,
        limit,
    )

    # 搜索图片文件：_scan_and_filter_directory 经 _cached_find_images_in_directory 扫描目录，
    # 翻页共享全量列表缓存（非递归按 mtime 失效、递归按 TTL 失效），scan_limit 用于早停与切片判定 has_more。
    # format_filter_exhausted 时跳过扫描，all_images 保持为空，由下方空结果分支统一返回。
    # 扫描与扫描后的 resolve、越界判定、去重整体下沉到 _scan_and_filter_directory 在线程内
    # 执行；仅进度上报留在事件循环。深翻页大 offset 或网络挂载目录下 resolve 不再阻塞事件循环。
    scan_limit = offset + limit + 1
    all_images: list[Path] = []
    # 原始路径到已 resolve 路径的缓存：扫描阶段每张图片仅 resolve 一次，展示阶段复用。
    image_resolved_map: dict[Path, Path] = {}
    if not format_filter_exhausted:
        seen_images: set[Path] = set()
        total_dirs = len(resolved_dirs)
        for dir_index, resolved_dir in enumerate(resolved_dirs, start=1):
            if len(all_images) >= scan_limit:
                break
            remaining = scan_limit - len(all_images)
            new_entries = await asyncio.to_thread(
                _scan_and_filter_directory,
                resolved_dir=resolved_dir,
                recursive=recursive,
                max_depth=max_depth,
                format_filter=format_filter,
                remaining=remaining,
                resolved_roots=resolved_roots,
                seen_images=seen_images,
            )
            for image_path, image_resolved in new_entries:
                all_images.append(image_path)
                image_resolved_map[image_path] = image_resolved
            # 多目录扫描时按已扫描目录占比上报中间进度，区间为 20% 至 90%；单目录跳过。
            # 进度上报必须留在事件循环，不能在线程内调用 ctx。
            if total_dirs > 1:
                await _safe_report_progress(
                    ctx,
                    progress=20.0 + 70.0 * dir_index / total_dirs,
                    message=f"已扫描 {dir_index}/{total_dirs} 个目录，找到 {len(all_images)} 张图片",
                )

    # 分页切片：has_more 时仅扫到 scan_limit、无法精确总数，total_count 置 None
    page_end = offset + limit
    images = all_images[offset:page_end]
    has_more = len(all_images) > page_end
    next_offset = page_end if has_more else None
    total_count = None if has_more else len(all_images)

    # 处理当前页为空的情况：
    # - format_filter_exhausted：用户指定后缀全部不受支持，返回独立区分消息
    # - total_count == 0：确实无匹配图片
    # - total_count > 0：offset 超出最后一页越界，仍需返回实际总数供客户端正确翻页
    if not images:
        if format_filter_exhausted:
            supported_list = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
            user_formats = ", ".join(format_filter) if format_filter else ""
            message = (
                f"指定的图片格式 {user_formats} 均不在支持列表内，" f"支持: {supported_list}。"
            )
            log_message = "图片格式过滤条件全部不受支持"
        elif total_count == 0:
            message = "未找到图片文件，请确认目录或过滤条件。"
            log_message = "未找到匹配的图片文件"
        else:
            message = (
                f"offset={offset} 超出范围，目录共有 {total_count} 张图片，"
                f"请使用 0 <= offset < {total_count}。"
            )
            log_message = f"offset={offset} 越界（目录共 {total_count} 张）"
        await _safe_ctx_log(ctx, "info", log_message)
        await _safe_report_progress(ctx, progress=100.0, message="扫描完成")
        return CallToolResult(
            content=[TextContent(type="text", text=message)],
            structuredContent=_build_browse_structured_result(
                status="empty",
                workspace_roots=workspace_roots,
                directory=requested_dir,
                resolved_directories=resolved_dirs,
                recursive=recursive,
                max_depth=max_depth,
                limit=limit,
                offset=offset,
                show_details=show_details,
                format_filter=format_filter,
                success=True,
                images=[],
                total_count=total_count,
                has_more=has_more,
                next_offset=next_offset,
            ),
            isError=False,
        )

    # 展示组装下沉到 _build_display_entries 在线程内执行：show_details 的 stat 与
    # _is_within_resolved 命中查找不再在事件循环阻塞网络挂载目录的读取。最终结果构建留在事件循环。
    display_lines, structured_images = await asyncio.to_thread(
        _build_display_entries,
        images=images,
        image_resolved_map=image_resolved_map,
        resolved_roots=resolved_roots,
        show_details=show_details,
    )
    lines = ["图片列表:"] + display_lines

    await _safe_ctx_log(ctx, "info", f"浏览完成：共 {len(structured_images)} 张图片")
    await _safe_report_progress(ctx, progress=100.0, message="扫描完成")

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent=_build_browse_structured_result(
            status="completed",
            workspace_roots=workspace_roots,
            directory=requested_dir,
            resolved_directories=resolved_dirs,
            recursive=recursive,
            max_depth=max_depth,
            limit=limit,
            offset=offset,
            show_details=show_details,
            format_filter=format_filter,
            success=True,
            images=structured_images,
            total_count=total_count,
            has_more=has_more,
            next_offset=next_offset,
        ),
        isError=False,
    )
