"""
图片浏览工具模块

提供本地图片文件浏览功能，支持递归搜索、格式过滤、数量限制等特性，
便于在 MCP 客户端选择参考图。
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List

from mcp.types import TextContent

from ...utils.logging import get_logger
from ...utils.path_utils import find_images_in_directory, get_relative_path

logger = get_logger(__name__)


def _format_file_info(path: Path, show_details: bool) -> str:
    """格式化文件信息为字符串。

    根据是否显示详细信息，返回文件路径或包含大小、修改时间的完整信息。

    Args:
        path: 文件路径对象。
        show_details: 是否显示文件详细信息（大小、修改时间）。

    Returns:
        格式化后的文件信息字符串，详细模式下格式为 "路径 | 大小 | 修改时间"。
    """
    parts = [str(path)]
    if show_details:
        stat = path.stat()
        size_mb = stat.st_size / (1024 * 1024)
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(
            sep=" ", timespec="seconds"
        )
        parts.append(f"{size_mb:.2f} MB")
        parts.append(f"修改: {mtime}")
    return " | ".join(parts)


async def handle_browse_images(arguments: Dict[str, Any]) -> List[TextContent]:
    """处理图片浏览请求。

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
        包含图片列表信息的 TextContent 对象列表。若未找到图片，
        返回提示信息；否则返回编号的图片列表。
    """
    # 解析并设置默认参数
    directory = arguments.get("directory") or "."
    recursive = bool(arguments.get("recursive", True))
    max_depth = int(arguments.get("max_depth", 3))
    limit = int(arguments.get("limit", 50))
    format_filter = arguments.get("format_filter")
    show_details = bool(arguments.get("show_details", False))

    logger.info(
        "浏览图片: dir=%s, recursive=%s, max_depth=%s, limit=%s",
        directory,
        recursive,
        max_depth,
        limit,
    )

    # 搜索图片文件并限制返回数量
    images = find_images_in_directory(
        directory=directory,
        recursive=recursive,
        max_depth=max_depth,
        extensions=format_filter,
    )
    images = images[:limit]

    # 处理未找到图片的情况
    if not images:
        return [TextContent(type="text", text="未找到图片文件，请确认目录或过滤条件。")]

    # 格式化图片列表输出
    base_dir = Path(directory).resolve()
    lines = ["图片列表:"]
    for idx, img in enumerate(images, 1):
        relative = get_relative_path(img, str(base_dir))
        lines.append(f"{idx}. {_format_file_info(Path(relative), show_details)}")

    return [TextContent(type="text", text="\n".join(lines))]
