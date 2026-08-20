"""图像格式定义与推断。

集中管理支持的图像扩展名、MIME 类型映射与基于文件头的格式推断，供 validation、
image_input、io_storage 等模块共享，避免多处重复定义。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

# 自动保存单文件大小上限默认值，config 与 io_download 共享此单一来源。
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024

# 无法推断扩展名时的默认图片扩展名，URL 提取、字节嗅探与 MIME 反推共用此单一来源。
DEFAULT_IMAGE_EXTENSION = ".jpeg"

# 校验与浏览支持的图片扩展名，小写且含点号。有序版本供展示，frozenset 版本供 in
# 成员判断。
SUPPORTED_IMAGE_EXTENSIONS_ORDERED: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".tiff",
    ".heic",
    ".heif",
)
SUPPORTED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(SUPPORTED_IMAGE_EXTENSIONS_ORDERED)

# 扩展名到 MIME 类型映射，用于本地文件转 Data URI，取只读视图防止公共映射被原地改写。
MIME_BY_EXTENSION: Mapping[str, str] = MappingProxyType(
    {
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
)

# MIME 类型到扩展名映射，用于 Data URI 解码后推断扩展名，同样取只读视图。
EXTENSION_BY_MIME: Mapping[str, str] = MappingProxyType(
    {
        "image/png": ".png",
        "image/jpeg": ".jpeg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
)

# HEIC/HEIF 的 ISO BMFF ftyp box brand 位于 offset 8-12，按编码归入 .heic / .heif。
_HEIC_BRANDS: tuple[bytes, ...] = (b"heic", b"heix", b"hevc", b"heim", b"heis")
_HEIF_BRANDS: tuple[bytes, ...] = (b"mif1", b"msf1")

# BMP DIB 头 size 字段的合法取值：BITMAPCOREHEADER 12、OS/2 简化头 16、
# BITMAPINFOHEADER 40 与 V4/V5 等扩展头 52-124。取 DIB 头字段而非文件大小字段
# 比对：下载侧以流式首部前缀校验，前缀短于完整文件，大小字段比对会误拒合法 BMP；
# DIB 头位于文件前 18 字节，前缀形态同样可判。
_BMP_DIB_HEADER_SIZES: frozenset[int] = frozenset({12, 16, 40, 52, 56, 64, 108, 124})


def infer_extension_from_bytes(content: bytes, default: str = DEFAULT_IMAGE_EXTENSION) -> str:
    """基于文件头魔法字节推断图片扩展名，含点号，无法识别时返回 ``default``。

    以文件头为准而非扩展名，避免扩展名缺失或伪造导致类型误判；仅识别受支持的格式。
    """
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpeg"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return ".gif"
    # BMP: "BM" 魔数且 DIB 头 size 字段取值合法。offset 6-10 保留字段非零的
    # 合法变体不据此拒判；"BM" 前缀的长文本因 DIB 头非法按未知内容处理。
    if (
        content.startswith(b"BM")
        and len(content) >= 18
        and int.from_bytes(content[14:18], "little") in _BMP_DIB_HEADER_SIZES
    ):
        return ".bmp"
    if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
        return ".webp"
    if content.startswith(b"II*\x00") or content.startswith(b"MM\x00*"):
        return ".tiff"
    # HEIC/HEIF：ISO BMFF 格式，4 字节 size + "ftyp" + 4 字节 brand。
    if len(content) >= 12 and content[4:8] == b"ftyp":
        brand = bytes(content[8:12])
        if brand in _HEIC_BRANDS:
            return ".heic"
        if brand in _HEIF_BRANDS:
            return ".heif"
    return default


# 内容真实性判定的按格式最小长度下界：BMP 的 64 字节要求达到文件头、DIB 头与首行
# 像素的最小构成，拦截头字段合法但内容过短的截断形态；其余格式签名特异性足够，不设
# 下界。流式首部校验的累计字节数须覆盖目标格式下界，否则合法内容会被误拒。
_MIN_KNOWN_BYTES_BY_EXTENSION: Mapping[str, int] = MappingProxyType({".bmp": 64})

# 流式首部校验的最小累计窗口：各格式下界的最大值，下载侧的首部缓冲须至少覆盖
# 该字节数，使 is_known_image_bytes 对全部受支持格式可判定。
SNIFF_HEAD_BYTES_FLOOR = max(_MIN_KNOWN_BYTES_BY_EXTENSION.values())


def is_known_image_bytes(content: bytes) -> bool:
    """判断字节是否以受支持图片的 magic 开头且达到该格式的最小长度。

    用于下载与 Base64 解码路径的内容真实性校验；扩展名推断仍由
    infer_extension_from_bytes 独立承担，不受最小长度下界影响。
    """
    extension = infer_extension_from_bytes(content, default="")
    if not extension:
        return False
    return len(content) >= _MIN_KNOWN_BYTES_BY_EXTENSION.get(extension, 0)


def format_file_size_mb(size_bytes: int) -> str:
    """将字节数格式化为 MB 字符串，保留一位小数，供校验与保存模块共享。"""
    return f"{size_bytes / 1024 / 1024:.1f}MB"


def parse_data_uri(data: Any) -> tuple[str | None, Any]:
    """解析 data URI，返回 (media_type, payload)。

    scheme 前缀按 RFC 3986 大小写不敏感判定，与 image_ref 的分类口径一致，使
    ``DATA:image/png;base64,....`` 也进入校验流水线获得精确报错。media_type 取自
    header 的媒体类型部分，缺失时为 None；payload 为首个逗号后的负载，不做 base64
    解码，由调用方按编码标记处理。非 data URI、缺逗号分隔符或入参非字符串时返回
    (None, 原样入参)，非字符串入参原样落于 payload 位。
    """
    if not isinstance(data, str):
        return None, data
    header, sep, payload = data.partition(",")
    if not sep or not header.lower().startswith("data:"):
        return None, data
    # header 形如 "data:image/png;base64"，去掉 scheme 前缀后取首个 ";" 前的媒体类型。
    body = header.split(":", 1)[1]
    if ";" in body:
        media_type = body.split(";", 1)[0] or None
    else:
        media_type = body or None
    return media_type, payload
