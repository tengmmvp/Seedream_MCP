"""URL 辅助工具。

承载与 URL 解析相关的纯函数：扩展名推断供 io_storage 生成保存路径复用，URL 脱敏
供 io_download 与 io_save 记录日志复用；仅依赖标准库解析能力，不涉及网络与文件
系统访问。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from ..core.formats import DEFAULT_IMAGE_EXTENSION


def sanitize_url(url: str) -> str:
    """脱敏 URL 用于日志，保留 scheme/host/path，剥离凭据、查询参数与控制字符。

    控制字符 CRLF 等会被剥离，防止攻击者经由 URL 在日志中伪造行，注入误导性记录。
    scheme 非 http/https 或 host 与 path 同时为空时无 authority 可保留，改输出
    ``scheme:<redacted>``。

    Args:
        url: 原始 URL 字符串。

    Returns:
        脱敏后的 URL；解析失败返回 ``<invalid-url>``。
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme not in ("http", "https") or (not host and not parsed.path):
            result = f"{parsed.scheme}:<redacted>"
        else:
            # 重建不含 userinfo 的 netloc；hostname 对 IPv6 字面量剥离方括号，需补回
            # 以保持 host 与端口边界。
            if ":" in host:
                host = f"[{host}]"
            netloc = host
            if parsed.port is not None:
                netloc = f"{netloc}:{parsed.port}"
            if parsed.query:
                result = f"{parsed.scheme}://{netloc}{parsed.path}?<query-redacted>"
            else:
                result = f"{parsed.scheme}://{netloc}{parsed.path}"
    except Exception:
        return "<invalid-url>"
    return re.sub(r"[\x00-\x1f\x7f]", "", result)


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
    except ValueError:
        # 解析失败时降级为默认扩展名，保证调用方始终拿到可用的点号后缀。
        pass
    return default
