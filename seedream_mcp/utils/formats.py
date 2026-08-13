"""图像格式定义与推断。

集中管理支持的图像扩展名、MIME 类型映射与基于文件头的格式推断，供 validation、
image_input、file_manager 等模块共享，避免多处重复定义。
"""

from __future__ import annotations

# 自动保存单文件大小上限默认值，config 与 download_manager 共享此单一来源
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024

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

# HEIC/HEIF 的 ISO BMFF ftyp box brand 位于 offset 8-12，按编码归入 .heic / .heif
_HEIC_BRANDS: tuple[bytes, ...] = (b"heic", b"heix", b"hevc", b"heim", b"heis")
_HEIF_BRANDS: tuple[bytes, ...] = (b"mif1", b"msf1")


def infer_extension_from_bytes(content: bytes, default: str = ".jpeg") -> str:
    """基于文件头魔法字节推断图片扩展名。

    以文件头 magic bytes 为准而非扩展名，避免扩展名缺失或伪造导致类型误判；
    仅识别受支持的格式，无法识别时返回 ``default``。

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
        # BMP: "BM" 签名且 offset 6-10 的保留字段须为 0，降低仅凭 2 字节前缀的冲突误判
        if (
            content.startswith(b"BM")
            and len(content) >= 14
            and content[6:10] == b"\x00\x00\x00\x00"
        ):
            return ".bmp"
        # WEBP：RIFF....WEBP 签名
        if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
            return ".webp"
        # TIFF
        if content.startswith(b"II*\x00") or content.startswith(b"MM\x00*"):
            return ".tiff"
        # HEIC/HEIF：ISO BMFF 格式，4 字节 size + "ftyp" + 4 字节 brand
        if len(content) >= 12 and content[4:8] == b"ftyp":
            brand = bytes(content[8:12])
            if brand in _HEIC_BRANDS:
                return ".heic"
            if brand in _HEIF_BRANDS:
                return ".heif"
    except Exception:
        # 字节过短或切片异常时降级为默认扩展名，保证稳定返回
        pass
    return default


def is_known_image_bytes(content: bytes) -> bool:
    """判断字节是否以受支持图片的 magic 开头，用于下载内容真实性校验。"""
    return infer_extension_from_bytes(content, default="") != ""


def _format_file_size_mb(size_bytes: int) -> str:
    """将字节数格式化为 MB 字符串，保留一位小数，供校验与保存模块共享。"""
    return f"{size_bytes / 1024 / 1024:.1f}MB"


def parse_data_uri(data: str) -> tuple[str | None, str]:
    """解析 data URI，返回 (media_type, payload)。

    非以 ``data:`` 开头、缺逗号分隔符或入参非字符串时返回 (None, 原始字符串)。
    media_type 取自 header 的媒体类型部分，例如 ``data:image/png;base64,....`` 解析为
    ``image/png``；header 缺少媒体类型时该字段为 None。payload 为首个逗号后的负载，
    不做 base64 解码，由调用方按编码标记自行处理。供 validation 与 auto_save 共享，
    消除两处 data URI 拆分逻辑的重复。
    """
    if not isinstance(data, str) or not data.startswith("data:"):
        return None, data
    header, sep, payload = data.partition(",")
    if not sep:
        return None, data
    # header 形如 "data:image/png;base64"，去掉 "data:" 前缀后取首个 ";" 前的媒体类型
    body = header[len("data:") :]
    if ";" in body:
        media_type = body.split(";", 1)[0] or None
    else:
        media_type = body or None
    return media_type, payload
