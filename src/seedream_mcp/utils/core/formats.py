"""图像格式定义与推断。

集中管理格式家族表、受支持图像扩展名、MIME 类型映射、PIL 格式名映射、
基于文件头的格式推断与 base64 解码尺寸估算，供 validation、image_input、
io_download、io_save 等模块共享，避免多处重复定义。
"""

from __future__ import annotations

import base64
import re
from types import MappingProxyType
from typing import Any, Mapping

# 自动保存单文件大小上限默认值，config 与 io_download 共享此单一来源。
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024

# 无法推断扩展名时的默认图片扩展名，URL 提取、字节嗅探与 MIME 反推共用此单一来源。
DEFAULT_IMAGE_EXTENSION = ".jpeg"

# 参考图与下载两入口的像素上限单一来源。
MAX_IMAGE_PIXELS = 36_000_000

# 数据目录名：<数据根目录>/DATA_DIR_NAME 下并列 images/thumbs/logs，整体删除即清理。
DATA_DIR_NAME = ".seedream"

# 格式家族表：家族名 -> 族内扩展名（小写含点号，族序与族内序即展示序）。
# 同族扩展名互为同格式别名（.jpg/.jpeg、.heic/.heif）；受支持白名单、下载侧
# 等价类与 PIL 格式映射均从本表派生，是扩展名宇宙的单一来源。
FORMAT_FAMILIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "jpeg": (".jpg", ".jpeg"),
        "png": (".png",),
        "gif": (".gif",),
        "bmp": (".bmp",),
        "webp": (".webp",),
        "tiff": (".tiff",),
        "heif": (".heic", ".heif"),
    }
)

# 校验与浏览支持的图片扩展名，按家族表展开派生；有序版本供展示，frozenset 版本
# 供 in 成员判断。
SUPPORTED_IMAGE_EXTENSIONS_ORDERED: tuple[str, ...] = tuple(
    extension for family_extensions in FORMAT_FAMILIES.values() for extension in family_extensions
)
SUPPORTED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(SUPPORTED_IMAGE_EXTENSIONS_ORDERED)

# 家族 -> PIL 实际识别的格式名元组：按键集派生自家族表的 (家族名大写,)，
# JPEG 家族另含多图形态别名 MPO，HEIF 家族 heic/heif 共用同一格式名；
# 新增家族或别名不散落到消费方。
PIL_FORMATS_BY_FAMILY: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        **{family: (family.upper(),) for family in FORMAT_FAMILIES},
        "jpeg": ("JPEG", "MPO"),
    }
)

# PIL 实际识别格式名 -> 族内扩展名集，由家族表与格式名表展开派生；键集合即
# 解码放行的格式宇宙，弱魔数格式（PPM/XBM 等）不在键内。
EXTENSIONS_BY_PIL_FORMAT: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        pil_format: frozenset(family_extensions)
        for family, family_extensions in FORMAT_FAMILIES.items()
        for pil_format in PIL_FORMATS_BY_FAMILY[family]
    }
)

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

# 新增扩展名漏更 MIME 表时在导入期暴露；显式抛错而非 assert，防 python -O
# 剥离后守护失效。
if _missing_mime := SUPPORTED_IMAGE_EXTENSIONS - MIME_BY_EXTENSION.keys():
    raise RuntimeError(f"MIME_BY_EXTENSION 缺少扩展名映射: {sorted(_missing_mime)}")

# MIME 类型到扩展名映射，用于 Data URI 解码后推断扩展名，由 MIME_BY_EXTENSION
# 反转派生，同样取只读视图。.jpg 与 .jpeg 同映射 image/jpeg，反转的多键冲突经
# 显式覆盖消解、保留 .jpeg，派生结果不依赖正向表的键序。
EXTENSION_BY_MIME: Mapping[str, str] = MappingProxyType(
    {
        **{mime: ext for ext, mime in MIME_BY_EXTENSION.items()},
        "image/jpeg": ".jpeg",
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


# PIL 解码器就绪标志，images 与 io 两组的解码前置共用。
_decoders_ready = False

# 首次覆写前留存的 PIL 默认解压阈值，测试隔离复位共用。
_pil_pristine_max_image_pixels: int | None = None


def ensure_image_decoders_ready() -> None:
    """设置 PIL 解压炸弹阈值并注册 HEIF 解码器，进程级仅首次执行。

    PIL 与 pillow_heif 延迟导入，避免模块导入期加载图像库产生全局副作用；
    check-then-set 非线程安全，但两项操作均幂等，并发重复执行无功能影响。
    """
    global _decoders_ready, _pil_pristine_max_image_pixels
    if _decoders_ready:
        return
    from PIL import Image
    from pillow_heif import register_heif_opener

    # 覆写后 PIL 在 36M 告警、72M 报错，36M 硬约束由各解码路径的显式检查承担。
    if _pil_pristine_max_image_pixels is None:
        _pil_pristine_max_image_pixels = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    register_heif_opener()
    _decoders_ready = True


def format_file_too_large(size_bytes: int, max_size: int, label: str = "文件") -> str:
    """构造数据大小超限的用户可见文案，输入校验与保存路径共用同一格式。

    label 为超限主体名称，Base64 估算与解码后等调用点传入各自主体；大小与上限的
    拼接格式在此单一收口，新增校验点不得再自行拼接。
    """
    return (
        f"{label}过大: {format_file_size_mb(size_bytes)}，"
        f"最大支持{format_file_size_mb(max_size)}"
    )


def format_file_too_large_bytes(size_bytes: int, max_size: int, label: str = "文件") -> str:
    """构造纯字节口径的超限文案，字节级核算路径与 MB 口径在此并列收口。"""
    return f"{label}过大: {size_bytes} 字节，最大支持{max_size} 字节"


# base64 解码尺寸估算与解码前剥离共用的空白模式源，str 模式 \s 含 NBSP 等异形空白。
BASE64_WHITESPACE_PATTERN = re.compile(r"\s+")


def _trailing_base64_padding(payload: str) -> int:
    """剥离尾部空白后统计连续 padding 字符数，供解码上界估算剔除。"""
    # 常规载荷的 padding 与折行尾空白均落在尾部 64 字符探测窗内，免整串拷贝。
    tail = payload[-64:].rstrip()
    body = tail.rstrip("=")
    if body:
        return len(tail) - len(body)
    # 窗口被空白与等号耗尽时 run 可能越窗，回退全串两段 rstrip（均在 C 层完成）。
    stripped = payload.rstrip()
    return len(stripped) - len(stripped.rstrip("="))


def estimate_base64_decoded_size(payload: str, limit: int) -> int:
    """估算 base64 文本解码后的字节数上界，无空白合法载荷恰等于实际解码字节数。

    尾部 padding 恒不计入：计入会把解码后恰在上限的载荷在预检误拒。limit 为
    调用方的接受上限，朴素长度上界入限时免整串空白扫描直达校验，超限才零分配
    剥离复核，折行与 NBSP 等异形空白不因开销被误拒。
    """
    padding = _trailing_base64_padding(payload)
    estimated = ((len(payload) - padding) * 3) // 4
    if estimated <= limit:
        return estimated
    stripped_length = len(payload) - sum(
        match.end() - match.start() for match in BASE64_WHITESPACE_PATTERN.finditer(payload)
    )
    return ((stripped_length - padding) * 3) // 4


def encode_data_uri(media_type: str, payload: bytes) -> str:
    """把字节负载编码为 base64 data URI，与 parse_data_uri 共守同一语法。"""
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def parse_data_uri(data: Any) -> tuple[str | None, Any, bool]:
    """解析 data URI，返回 (media_type, payload, is_base64)。

    scheme 前缀按 RFC 3986 大小写不敏感判定，与 image_ref 的分类口径一致，使
    ``DATA:image/png;base64,....`` 也进入校验流水线获得精确报错。media_type 取自
    header 的媒体类型部分，缺失时为 None；payload 为首个逗号后的负载，不做 base64
    解码；is_base64 标记 header 是否携带 ``;base64`` 参数（大小写不敏感），由调用方
    决定解码方式。非 data URI、缺逗号分隔符或入参非字符串时返回 (None, 原样入参,
    False)，非字符串入参原样落于 payload 位。
    """
    if not isinstance(data, str):
        return None, data, False
    header, sep, payload = data.partition(",")
    if not sep or not header.lower().startswith("data:"):
        return None, data, False
    # header 形如 "data:image/png;base64"，去掉 scheme 前缀后取首个 ";" 前的媒体类型。
    body = header.split(":", 1)[1]
    media_type = body.split(";", 1)[0] or None
    # 参数名按分号切分后精确比较，base64url 等以 base64 开头的参数名不误判为编码声明。
    is_base64 = any(param.strip() == "base64" for param in header.lower().split(";")[1:])
    return media_type, payload, is_base64
