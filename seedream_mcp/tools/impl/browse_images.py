"""
图片浏览工具模块
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict

from mcp.server.fastmcp import Context
from mcp.types import CallToolResult, TextContent

from ...utils.logging import get_logger
from ...utils.path_utils import (
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
        stat = stat_path.stat()
        size_mb = stat.st_size / (1024 * 1024)
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(
            sep=" ", timespec="seconds"
        )
        parts.append(f"{size_mb:.2f} MB")
        parts.append(f"修改: {mtime}")
    return " | ".join(parts)


async def handle_browse_images(
    arguments: Dict[str, Any],
    ctx: Context[Any, Any, Any] | None = None,
) -> CallToolResult:
    """
    处理图片浏览请求

    根据提供的参数搜索指定目录下的图片文件，支持递归搜索、深度限制、
    格式过滤等功能，返回格式化的图片列表。

    Args:
        arguments: 包含搜索参数的字典，支持以下键：
            - directory (str, optional): 搜索目录路径，默认为当前目录。
            - recursive (bool, optional): 是否递归搜索子目录，默认为 True。
            - max_depth (int, optional): 递归搜索的最大深度，默认为 3。
            - limit (int, optional): 返回结果的最大数量，默认为 50。
            - format_filter (str, optional): 文件格式过滤条件。
            - show_details (bool, optional): 是否显示文件详细信息，默认为 False。

    Returns:
        CallToolResult: MCP 标准工具结果。
            - content: 面向模型的文本摘要
            - structuredContent: 结构化结果数据
            - isError: 是否为错误结果
    """
    # 解析并设置默认参数
    directory = arguments.get("directory") or "."
    recursive = bool(arguments.get("recursive", True))
    max_depth = int(arguments.get("max_depth", 3))
    limit = int(arguments.get("limit", 50))
    format_filter = arguments.get("format_filter")
    show_details = bool(arguments.get("show_details", False))

    workspace_roots = get_workspace_roots()
    if not workspace_roots:
        message = "当前 MCP 会话未授权任何工作区目录，无法浏览本地文件。"
        return CallToolResult(
            content=[TextContent(type="text", text=message)],
            structuredContent={
                "tool": "browse_images",
                "success": False,
                "status": "failed",
                "error": message,
                "workspace_roots": [],
            },
            isError=True,
        )

    resolved_dirs: list[Path] = []
    requested_dir = str(directory)
    raw_dir_path = Path(requested_dir)
    if raw_dir_path.is_absolute():
        try:
            absolute_dir = normalize_path(requested_dir)
        except ValueError as exc:
            message = f"目录路径无效: {exc}"
            return CallToolResult(
                content=[TextContent(type="text", text=message)],
                structuredContent={
                    "tool": "browse_images",
                    "success": False,
                    "status": "failed",
                    "error": message,
                    "workspace_roots": [str(root) for root in workspace_roots],
                },
                isError=True,
            )
        if not is_path_within_any_base(absolute_dir, workspace_roots):
            allowed_roots = ", ".join(str(root) for root in workspace_roots)
            message = "目录超出允许范围。" f"仅允许浏览工作区目录: {allowed_roots}"
            return CallToolResult(
                content=[TextContent(type="text", text=message)],
                structuredContent={
                    "tool": "browse_images",
                    "success": False,
                    "status": "failed",
                    "error": message,
                    "workspace_roots": [str(root) for root in workspace_roots],
                },
                isError=True,
            )
        resolved_dirs.append(absolute_dir)
    else:
        for root in workspace_roots:
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
        return CallToolResult(
            content=[TextContent(type="text", text=message)],
            structuredContent={
                "tool": "browse_images",
                "success": False,
                "status": "failed",
                "error": message,
                "workspace_roots": [str(root) for root in workspace_roots],
            },
            isError=True,
        )

    if ctx is not None:
        try:
            await ctx.report_progress(progress=20.0, total=100.0, message="开始扫描图片目录")
        except Exception:
            pass

    logger.info(
        "浏览图片: dirs={}, recursive={}, max_depth={}, limit={}",
        resolved_dirs,
        recursive,
        max_depth,
        limit,
    )

    # 搜索图片文件并限制返回数量
    images: list[Path] = []
    seen_images: set[Path] = set()
    for resolved_dir in resolved_dirs:
        remaining = None if limit is None else max(0, limit - len(images))
        if remaining == 0:
            break
        matched_images = find_images_in_directory(
            directory=str(resolved_dir),
            recursive=recursive,
            max_depth=max_depth,
            extensions=format_filter,
            limit=remaining,
        )
        for image_path in matched_images:
            if not is_path_within_any_base(image_path, workspace_roots):
                logger.warning("检测到越界图片路径，已忽略: {}", image_path)
                continue
            if image_path in seen_images:
                continue
            seen_images.add(image_path)
            images.append(image_path)
            if limit is not None and len(images) >= limit:
                break
        if limit is not None and len(images) >= limit:
            break

    # 处理未找到图片的情况
    if not images:
        message = "未找到图片文件，请确认目录或过滤条件。"
        if ctx is not None:
            try:
                await ctx.report_progress(progress=100.0, total=100.0, message="扫描完成")
            except Exception:
                pass
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
            },
            isError=False,
        )

    # 格式化图片列表输出
    lines = ["图片列表:"]
    structured_images: list[dict[str, Any]] = []
    for idx, img in enumerate(images, 1):
        display_base = next(
            (root for root in workspace_roots if is_path_within_base(img, root)),
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
                "absolute_path": str(img),
            }
        )

    if ctx is not None:
        try:
            await ctx.report_progress(progress=100.0, total=100.0, message="扫描完成")
        except Exception:
            pass

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
            "images": structured_images,
            "recursive": recursive,
            "max_depth": max_depth,
            "limit": limit,
            "show_details": show_details,
            "format_filter": format_filter,
        },
        isError=False,
    )
