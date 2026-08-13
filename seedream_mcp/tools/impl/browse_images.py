"""图片浏览工具的 impl 处理器。

直接实现工作区图片扫描与分页，不经 ``execute_generation_handler`` 生成流水线；字段规则
由 schemas.BrowseImagesInput 单一定义。
"""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from typing import Any, Dict

from mcp.server.fastmcp import Context
from mcp.types import CallToolResult, TextContent

from ..core._helpers import _safe_ctx_log, _safe_report_progress
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

logger = get_logger(__name__)


def _format_file_info(display_path: str, stat_path: Path, show_details: bool) -> str:
    """
    格式化文件信息为字符串

    根据是否显示详细信息，返回文件路径或包含大小、修改时间的完整信息。

    Args:
        display_path: 展示给用户的文件路径字符串。
        stat_path: 用于读取文件属性的实际路径对象。
        show_details: 是否显示文件详细信息（大小、修改时间）。

    Returns:
        格式化后的文件信息字符串，详细模式下格式为 "路径 | 大小 | 修改时间"。
    """
    parts = [display_path]
    if show_details:
        try:
            stat_result = stat_path.stat()
        except OSError:
            parts.append("文件信息不可用")
            return " | ".join(parts)
        size_mb = stat_result.st_size / (1024 * 1024)
        # astimezone 将 naive 本地时间标注为本地时区，输出携带 UTC 偏移以消除时区歧义。
        mtime = (
            datetime.datetime.fromtimestamp(stat_result.st_mtime)
            .astimezone()
            .isoformat(sep=" ", timespec="seconds")
        )
        parts.append(f"{size_mb:.2f} MB")
        parts.append(f"修改: {mtime}")
    return " | ".join(parts)


def _browse_error_result(message: str, workspace_roots: list[Path]) -> CallToolResult:
    """构造浏览工具的失败结果，统一结构化错误字段与 workspace_roots 回显。"""
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent={
            "tool": "browse_images",
            "success": False,
            "status": "failed",
            "error": {"type": "browse_failed", "message": message},
            "workspace_roots": [str(root) for root in workspace_roots],
        },
        isError=True,
    )


async def handle_browse_images(
    arguments: Dict[str, Any],
    ctx: Context[Any, Any, Any] | None = None,
) -> CallToolResult:
    """处理图片浏览请求，扫描工作区内指定目录的图片文件并分页返回。

    仅允许访问 MCP Roots 授权的工作区目录；扫描 offset+limit+1 张以判定 has_more，避免
    大目录无上限扫描。完整字段规则与默认值见 ``BrowseImagesInput``，本函数读取 arguments。

    Args:
        arguments: 工具原始参数字典，结构见 ``BrowseImagesInput``。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 标准工具结果，含面向模型的图片列表文本与 structuredContent。
    """
    directory = arguments.get("directory") or "."
    recursive = bool(arguments.get("recursive", True))
    # max_depth/limit/offset 已由 BrowseImagesInput 的 pydantic 校验保证为 int，无需再 int() 包装
    max_depth = arguments.get("max_depth", 3)
    limit = arguments.get("limit", 50)
    offset = arguments.get("offset", 0)
    format_filter = arguments.get("format_filter")
    # 格式过滤仅保留受支持的图片扩展名，避免以非图片后缀探测文件
    if format_filter:
        format_filter = [ext for ext in format_filter if ext in SUPPORTED_IMAGE_EXTENSIONS]
        if not format_filter:
            format_filter = None
    show_details = bool(arguments.get("show_details", False))

    workspace_roots = get_workspace_roots()
    if not workspace_roots:
        message = "当前 MCP 会话未授权任何工作区目录，无法浏览本地文件。"
        await _safe_ctx_log(ctx, "warning", message)
        return _browse_error_result(message, workspace_roots)

    # 预解析工作区根，避免在 limit 循环内对每张图片 × 每个 root 重复 resolve。
    # 越界校验仍由 is_path_within_* 内部对 path 再做 resolve，base 与 path 比较语义不变。
    # 展示层继续使用 workspace_roots，用于错误提示与 structuredContent 回显。
    resolved_roots: list[Path] = [root.resolve() for root in workspace_roots]

    resolved_dirs: list[Path] = []
    requested_dir = str(directory)
    raw_dir_path = Path(requested_dir)
    if raw_dir_path.is_absolute():
        try:
            absolute_dir = normalize_path(requested_dir)
        except ValueError as exc:
            message = f"目录路径无效: {exc}"
            return _browse_error_result(message, workspace_roots)
        if not is_path_within_any_base(absolute_dir, resolved_roots):
            allowed_roots = ", ".join(str(root) for root in workspace_roots)
            message = "目录超出允许范围。" f"仅允许浏览工作区目录: {allowed_roots}"
            return _browse_error_result(message, workspace_roots)
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
        return _browse_error_result(message, workspace_roots)

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

    # 搜索图片文件：扫描 offset+limit+1 张，多取的 1 张用于判定 has_more，避免大目录无上限扫描
    scan_limit = offset + limit + 1
    all_images: list[Path] = []
    seen_images: set[Path] = set()
    total_dirs = len(resolved_dirs)
    for dir_index, resolved_dir in enumerate(resolved_dirs, start=1):
        if len(all_images) >= scan_limit:
            break
        matched_images = await asyncio.to_thread(
            find_images_in_directory,
            directory=str(resolved_dir),
            recursive=recursive,
            max_depth=max_depth,
            extensions=format_filter,
            limit=scan_limit - len(all_images),
        )
        for image_path in matched_images:
            if not is_path_within_any_base(image_path, resolved_roots):
                logger.warning("检测到越界图片路径，已忽略: {}", image_path)
                continue
            if image_path in seen_images:
                continue
            seen_images.add(image_path)
            all_images.append(image_path)
            if len(all_images) >= scan_limit:
                break
        # 多目录扫描时按已扫描目录占比上报中间进度，区间为 20% 至 90%；单目录跳过
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
    # - total_count == 0：确实无匹配图片
    # - total_count > 0：offset 越界（超出最后一页），仍需返回实际总数供客户端正确翻页
    if not images:
        if total_count == 0:
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
            structuredContent={
                "tool": "browse_images",
                "success": True,
                "status": "empty",
                "directory": requested_dir,
                "resolved_directories": [str(item) for item in resolved_dirs],
                "workspace_roots": [str(root) for root in workspace_roots],
                "images": [],
                "count": 0,
                "total_count": total_count,
                "offset": offset,
                "has_more": has_more,
                "next_offset": next_offset,
                "recursive": recursive,
                "max_depth": max_depth,
                "limit": limit,
                "show_details": show_details,
                "format_filter": format_filter,
            },
            isError=False,
        )

    lines = ["图片列表:"]
    structured_images: list[dict[str, Any]] = []
    for idx, img in enumerate(images, 1):
        # 预 resolve img 一次，复用 fast-path 与已 resolve 的 roots 比较，
        # 避免每张图片对每个 root 重复 resolve。
        img_resolved = img.resolve()
        display_base = next(
            (root for root in resolved_roots if _is_within_resolved(img_resolved, root)),
            None,
        )
        if display_base is None:
            logger.warning("图片路径未命中任何工作区根目录，已忽略: {}", img)
            continue
        display_path = get_relative_path(img, str(display_base))
        lines.append(f"{idx}. {_format_file_info(display_path, img, show_details)}")
        structured_images.append(
            {
                "index": idx,
                "path": display_path,
            }
        )

    await _safe_ctx_log(ctx, "info", f"浏览完成：共 {len(structured_images)} 张图片")
    await _safe_report_progress(ctx, progress=100.0, message="扫描完成")

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structuredContent={
            "tool": "browse_images",
            "success": True,
            "status": "completed",
            "directory": requested_dir,
            "resolved_directories": [str(item) for item in resolved_dirs],
            "workspace_roots": [str(root) for root in workspace_roots],
            "count": len(structured_images),
            "total_count": total_count,
            "offset": offset,
            "has_more": has_more,
            "next_offset": next_offset,
            "images": structured_images,
            "recursive": recursive,
            "max_depth": max_depth,
            "limit": limit,
            "show_details": show_details,
            "format_filter": format_filter,
        },
        isError=False,
    )
