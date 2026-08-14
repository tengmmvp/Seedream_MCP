"""URL 辅助工具。

承载与 URL 解析相关的纯函数，供 io_storage 生成保存路径时复用；仅依赖标准库
解析能力，不涉及网络与文件系统访问。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from ..core.formats import DEFAULT_IMAGE_EXTENSION


def get_file_extension_from_url(url: str, default: str = DEFAULT_IMAGE_EXTENSION) -> str:
    """从 URL 路径推断文件扩展名，含点号。

    Args:
        url: 图片 URL。
        default: 无法推断时返回的默认扩展名，含点号。

    Returns:
        小写的扩展名，含点号，或 ``default``。
    """
    try:
        # 扩展名仅从 URL 路径部分提取，query 与 fragment 不参与推断。
        path = urlparse(url).path
        suffix = Path(path).suffix.lower()
        if suffix:
            return suffix
    except Exception:
        # 解析失败时降级为默认扩展名，保证调用方始终拿到可用的点号后缀。
        pass
    return default
