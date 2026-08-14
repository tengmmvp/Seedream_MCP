"""图片浏览工具的 impl 处理器。

直接实现工作区图片扫描与分页，不经 ``execute_generation_handler`` 生成流水线；字段规则
由 schemas.BrowseImagesInput 单一定义。
"""

from __future__ import annotations

import asyncio
import datetime
import time  # noqa: F401  # time 为进程共享模块，扫描缓存经其驱动 TTL，外部经本模块替换 monotonic 即可模拟过期
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, TextContent

from ..core._helpers import _safe_ctx_log, _safe_report_progress
from ..core.schemas import BrowseImagesInput
from ...utils.directory_scan_cache import (  # noqa: F401  # 扫描缓存符号经本模块重导出，外部经本模块访问缓存状态
    _DIRECTORY_SCAN_CACHE,
    _DIRECTORY_SCAN_CACHE_TTL_SECONDS,
    _cached_find_images_in_directory,
)
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


@dataclass(frozen=True)
class _BrowseRequestState:
    """单次浏览请求的状态快照，错误分支与兜底分支共享取值。

    resolved_directories 为随解析流程逐步填充的活动列表；构建快照时绑定其引用，所有错误
    分支发生在该列表尚未填充的阶段，成功分支在填充完成后读取同一引用。
    """

    workspace_roots: list[Path]
    directory: str
    resolved_directories: list[Path]
    recursive: bool
    max_depth: int
    limit: int
    offset: int
    show_details: bool
    format_filter: list[str] | None

    @classmethod
    def from_arguments(
        cls,
        arguments: dict[str, Any],
        *,
        workspace_roots: list[Path],
        resolved_directories: list[Path],
        format_filter: list[str] | None = None,
    ) -> _BrowseRequestState:
        """从工具原始参数与既有状态构建请求快照，impl 与兜底分支共享同一取值逻辑。"""
        return cls(
            workspace_roots=workspace_roots,
            directory=str(arguments.get("directory") or "."),
            resolved_directories=resolved_directories,
            recursive=bool(arguments.get("recursive", BrowseImagesInput.DEFAULT_RECURSIVE)),
            max_depth=arguments.get("max_depth", BrowseImagesInput.DEFAULT_MAX_DEPTH),
            limit=arguments.get("limit", BrowseImagesInput.DEFAULT_LIMIT),
            offset=arguments.get("offset", BrowseImagesInput.DEFAULT_OFFSET),
            show_details=bool(
                arguments.get("show_details", BrowseImagesInput.DEFAULT_SHOW_DETAILS)
            ),
            format_filter=format_filter,
        )


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
    state: _BrowseRequestState,
    message: str,
    status: str = "failed",
) -> CallToolResult:
    """集中构造 browse_images 工具的错误 CallToolResult。

    各错误分支共享同一请求状态快照与 isError=True 语义，仅 message 不同；通过此辅助函数
    统一构造，避免重复展开相同状态字段。structuredContent.error.type 恒为 browse_failed，
    message 同时作为可见文本与结构化错误原因，二者保持一致。
    """
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent=_build_browse_structured_result(
            status=status,
            workspace_roots=state.workspace_roots,
            directory=state.directory,
            resolved_directories=state.resolved_directories,
            recursive=state.recursive,
            max_depth=state.max_depth,
            limit=state.limit,
            offset=state.offset,
            show_details=state.show_details,
            format_filter=state.format_filter,
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
    # 底层扫描经本模块作用域的 find_images_in_directory 注入，外部替换本模块同名属性即可生效
    matched_images = _cached_find_images_in_directory(
        resolved_dir=resolved_dir,
        recursive=recursive,
        max_depth=max_depth,
        format_filter=format_filter,
        scan_limit=remaining,
        scanner=find_images_in_directory,
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
            state=_BrowseRequestState.from_arguments(
                arguments,
                workspace_roots=fallback_roots,
                resolved_directories=[],
                format_filter=None,
            ),
            message=f"浏览图片失败：{user_message}；请确认目录路径有效且位于工作区内。",
        )


async def _handle_browse_images_impl(
    arguments: dict[str, Any],
    ctx: Context[Any, Any, Any] | None = None,
) -> CallToolResult:
    """浏览工具主逻辑，由 ``handle_browse_images`` 外层兜底包裹。"""
    # 格式过滤仅保留受支持的图片扩展名，避免以非图片后缀探测文件。全部后缀均不受支持时标记
    # format_filter_exhausted 并跳过扫描；此时保留用户原始输入供 structuredContent 回显，不缩减
    # 为空列表。不能将空列表传给 find_images_in_directory，因其把空列表视为未限制而扫描全部。
    raw_format_filter = arguments.get("format_filter")
    format_filter_exhausted = False
    if raw_format_filter:
        supported_only = [ext for ext in raw_format_filter if ext in SUPPORTED_IMAGE_EXTENSIONS]
        if supported_only:
            raw_format_filter = supported_only
        else:
            format_filter_exhausted = True

    workspace_roots = get_workspace_roots()
    # resolved_dirs 随解析流程逐步填充；state 绑定其引用，错误分支在填充前读取、成功分支在
    # 填充后读取同一引用，确保请求状态在各分支间一致。
    resolved_dirs: list[Path] = []
    state = _BrowseRequestState.from_arguments(
        arguments,
        workspace_roots=workspace_roots,
        resolved_directories=resolved_dirs,
        format_filter=raw_format_filter,
    )

    if not workspace_roots:
        message = "当前 MCP 会话未授权任何工作区目录，无法浏览本地文件。"
        await _safe_ctx_log(ctx, "warning", message)
        return _build_browse_error(state=state, message=message)

    # 预解析工作区根：去重与展示阶段直接用 _is_within_resolved 与这些已 resolve 的 root
    # 比较，root 不再重复 resolve。每张图片也只 resolve 一次，结果缓存于 image_resolved_map。
    # 展示层与 structuredContent 仍回显原始 workspace_roots。
    resolved_roots: list[Path] = [root.resolve() for root in workspace_roots]

    raw_dir_path = Path(state.directory)
    if raw_dir_path.is_absolute():
        try:
            absolute_dir = normalize_path(state.directory)
        except ValueError as exc:
            message = f"目录路径无效: {exc}"
            return _build_browse_error(state=state, message=message)
        if not is_path_within_any_base(absolute_dir, resolved_roots):
            allowed_roots = ", ".join(str(root) for root in workspace_roots)
            message = "目录超出允许范围。" f"仅允许浏览工作区目录: {allowed_roots}"
            return _build_browse_error(state=state, message=message)
        resolved_dirs.append(absolute_dir)
    else:
        for root in resolved_roots:
            try:
                candidate = normalize_path(state.directory, str(root))
            except ValueError:
                continue
            if not is_path_within_base(candidate, root):
                continue
            if candidate not in resolved_dirs:
                resolved_dirs.append(candidate)

    if not resolved_dirs:
        allowed_roots = ", ".join(str(root) for root in workspace_roots)
        message = "目录超出允许范围。" f"仅允许浏览工作区目录: {allowed_roots}"
        return _build_browse_error(state=state, message=message)

    await _safe_ctx_log(
        ctx,
        "info",
        f"浏览图片：目录={state.directory}, 递归={state.recursive}, "
        f"最大深度={state.max_depth}, 限制={state.limit}",
    )
    await _safe_report_progress(ctx, progress=20.0, message="开始扫描图片目录")

    logger.info(
        "浏览图片: dirs={}, recursive={}, max_depth={}, limit={}",
        resolved_dirs,
        state.recursive,
        state.max_depth,
        state.limit,
    )

    # 搜索图片文件：_scan_and_filter_directory 经 _cached_find_images_in_directory 扫描目录，
    # 翻页共享有序列表缓存（非递归按 mtime 失效、递归按 TTL 失效），scan_limit 用于早停与切片
    # 判定 has_more。format_filter_exhausted 时跳过扫描，all_images 保持为空，由下方空结果分支
    # 统一返回。扫描与扫描后的 resolve、越界判定、去重整体下沉到 _scan_and_filter_directory 在
    # 线程内执行；仅进度上报留在事件循环。深翻页大 offset 或网络挂载目录下 resolve 不再阻塞事件循环。
    scan_limit = state.offset + state.limit + 1
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
                recursive=state.recursive,
                max_depth=state.max_depth,
                format_filter=state.format_filter,
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
    page_end = state.offset + state.limit
    images = all_images[state.offset : page_end]
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
            user_formats = ", ".join(state.format_filter) if state.format_filter else ""
            message = (
                f"指定的图片格式 {user_formats} 均不在支持列表内，" f"支持: {supported_list}。"
            )
            log_message = "图片格式过滤条件全部不受支持"
        elif total_count == 0:
            message = "未找到图片文件，请确认目录或过滤条件。"
            log_message = "未找到匹配的图片文件"
        else:
            message = (
                f"offset={state.offset} 超出范围，目录共有 {total_count} 张图片，"
                f"请使用 0 <= offset < {total_count}。"
            )
            log_message = f"offset={state.offset} 越界（目录共 {total_count} 张）"
        await _safe_ctx_log(ctx, "info", log_message)
        await _safe_report_progress(ctx, progress=100.0, message="扫描完成")
        return CallToolResult(
            content=[TextContent(type="text", text=message)],
            structuredContent=_build_browse_structured_result(
                status="empty",
                workspace_roots=state.workspace_roots,
                directory=state.directory,
                resolved_directories=state.resolved_directories,
                recursive=state.recursive,
                max_depth=state.max_depth,
                limit=state.limit,
                offset=state.offset,
                show_details=state.show_details,
                format_filter=state.format_filter,
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
        show_details=state.show_details,
    )
    lines = ["图片列表:"] + display_lines

    await _safe_ctx_log(ctx, "info", f"浏览完成：共 {len(structured_images)} 张图片")
    await _safe_report_progress(ctx, progress=100.0, message="扫描完成")

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent=_build_browse_structured_result(
            status="completed",
            workspace_roots=state.workspace_roots,
            directory=state.directory,
            resolved_directories=state.resolved_directories,
            recursive=state.recursive,
            max_depth=state.max_depth,
            limit=state.limit,
            offset=state.offset,
            show_details=state.show_details,
            format_filter=state.format_filter,
            success=True,
            images=structured_images,
            total_count=total_count,
            has_more=has_more,
            next_offset=next_offset,
        ),
        isError=False,
    )
