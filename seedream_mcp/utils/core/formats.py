"""图像格式定义与推断。

集中管理支持的图像扩展名、MIME 类型映射与基于文件头的格式推断，供 validation、
image_input、io_storage 等模块共享，避免多处重复定义。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

# 自动保存单文件大小上限默认值，config 与 io_download 共享此单一来源。
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024

# 无法推断扩展名时的默认图片扩展名，URL 提取、字节嗅探与 MIME 反推共用此单一来源。
DEFAULT_IMAGE_EXTENSION = ".jpeg"

# 校验与浏览支持的图片扩展名，小写且含点号。有序版本供展示，frozenset 版本供 in 成员判断；
# 有序版本为不可变元组，防止公共容器被原地变异污染共享行为。
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

# 扩展名到 MIME 类型映射，用于本地文件转 Data URI。以 MappingProxyType 包装为
# 只读视图，与 SUPPORTED_IMAGE_EXTENSIONS 的 frozenset 口径一致，防止公共映射被
# 原地改写污染共享行为。
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


def infer_extension_from_bytes(content: bytes, default: str = DEFAULT_IMAGE_EXTENSION) -> str:
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
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if content.startswith(b"\xff\xd8\xff"):
            return ".jpeg"
        if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
            return ".gif"
        # BMP: "BM" 魔数且文件头完整（14 字节 BITMAPFILEHEADER）。个别合法 BMP 变体的
        # offset 6-10 保留字段非零，不据此拒判；完整文件头长度足以约束误判面。
        if content.startswith(b"BM") and len(content) >= 14:
            return ".bmp"
        if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
            return ".webp"
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
        # 字节过短或切片异常时降级为默认扩展名，保证稳定返回。
        pass
    return default


# 下载内容真实性判定按格式分级的最小合法长度下界：BMP 的 14 字节文件头约束过宽，
# 以 BM 开头的任意短文本都会被判为图片；真实 BMP 至少由 14 字节文件头、40 字节
# DIB 头与像素行构成，64 字节下界拦截短文本误判而不拒绝合法 BMP。其余格式签名
# 特异性足够，不设下界。下载侧以流式首部做本校验时，累计字节数须覆盖目标格式的
# 下界，否则合法内容会被误拒。
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


def parse_data_uri(data: str) -> tuple[str | None, str]:
    """解析 data URI，返回 (media_type, payload)。

    scheme 前缀按 RFC 3986 大小写不敏感判定，与 image_ref 的分类口径一致，使
    ``DATA:image/png;base64,....`` 也能进入校验流水线获得精确报错而非笼统的
    "格式无效"。非 data URI、缺逗号分隔符或入参非字符串时返回 (None, 原始字符串)。
    media_type 取自 header 的媒体类型部分，例如 ``data:image/png;base64,....`` 解析为
    ``image/png``；header 缺少媒体类型时该字段为 None。payload 为首个逗号后的负载，
    不做 base64 解码，由调用方按编码标记自行处理。供 validation 与 auto_save 共享，
    消除两处 data URI 拆分逻辑的重复。
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
