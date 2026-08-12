"""图像格式定义与推断。

集中管理支持的图像扩展名、MIME 类型映射与基于文件头的格式推断，供 validation、
image_input、file_manager 等模块共享，避免多处重复定义。
"""

from __future__ import annotations

# 校验与浏览支持的图片扩展名，小写且含点号
SUPPORTED_IMAGE_EXTENSIONS: list[str] = [
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".tiff",
    ".heic",
    ".heif",
]

# 扩展名到 MIME 类型映射，用于本地文件转 Data URI
MIME_BY_EXTENSION: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".heic": "image/heic",
    ".heif": "image/heif",
}

# MIME 类型到扩展名映射，用于 Data URI 解码后推断扩展名
EXTENSION_BY_MIME: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpeg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/heic": ".heic",
    "image/heif": ".heif",
}

# HEIC/HEIF 的 ISO BMFF ftyp box brand（offset 8-12），按编码归入 .heic / .heif
_HEIC_BRANDS: tuple[bytes, ...] = (b"heic", b"heix", b"hevc", b"heim", b"heis")
_HEIF_BRANDS: tuple[bytes, ...] = (b"mif1", b"msf1")


def infer_extension_from_bytes(content: bytes, default: str = ".jpeg") -> str:
    """基于文件头魔法字节推断图片扩展名。

    Args:
        content: 图片字节内容。
        default: 无法识别时返回的默认扩展名，含点号。

    Returns:
        推断出的扩展名，含点号。
    """
    try:
        # PNG
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        # JPEG
        if content.startswith(b"\xff\xd8\xff"):
            return ".jpeg"
        # GIF
        if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
            return ".gif"
        # BMP
        if content.startswith(b"BM"):
            return ".bmp"
        # WEBP (RIFF....WEBP)
        if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
            return ".webp"
        # TIFF
        if content.startswith(b"II*\x00") or content.startswith(b"MM\x00*"):
            return ".tiff"
        # HEIC/HEIF（ISO BMFF：4 字节 size + "ftyp" + 4 字节 brand）
        if len(content) >= 12 and content[4:8] == b"ftyp":
            brand = bytes(content[8:12])
            if brand in _HEIC_BRANDS:
                return ".heic"
            if brand in _HEIF_BRANDS:
                return ".heif"
    except Exception:
        pass
    return default


def is_known_image_bytes(content: bytes) -> bool:
    """判断字节是否以受支持图片的 magic 开头，用于下载内容真实性校验。"""
    return infer_extension_from_bytes(content, default="") != ""
