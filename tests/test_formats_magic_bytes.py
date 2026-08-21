"""守护测试：BMP/WEBP/TIFF 魔法字节推断。

验证 infer_extension_from_bytes 对 BMP、WEBP、TIFF 三种格式的文件头识别，
覆盖正向识别、短字节降级与近似签名误判拒绝，防止扩展名推断回归。
"""

from __future__ import annotations

import io
from types import MappingProxyType

import pytest
from PIL import Image

from seedream_mcp.utils.core.formats import (
    EXTENSION_BY_MIME,
    MIME_BY_EXTENSION,
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS_ORDERED,
    infer_extension_from_bytes,
    is_known_image_bytes,
)

# ==================== 公共容器不可变 ====================


def test_supported_extensions_ordered_is_immutable_tuple() -> None:
    """有序扩展名容器为不可变元组，公共白名单不可被原地变异。"""
    assert isinstance(SUPPORTED_IMAGE_EXTENSIONS_ORDERED, tuple)
    with pytest.raises((AttributeError, TypeError)):
        SUPPORTED_IMAGE_EXTENSIONS_ORDERED.append(".exe")  # type: ignore[attr-defined]


def test_mime_mappings_are_immutable_readonly_views() -> None:
    """扩展名与 MIME 映射为只读视图，公共映射不可被原地改写污染共享行为。"""
    assert isinstance(MIME_BY_EXTENSION, MappingProxyType)
    assert isinstance(EXTENSION_BY_MIME, MappingProxyType)
    with pytest.raises(TypeError):
        MIME_BY_EXTENSION[".exe"] = "application/x-msdownload"  # type: ignore[index]
    with pytest.raises(TypeError):
        EXTENSION_BY_MIME["image/x"] = ".x"  # type: ignore[index]


# ==================== MIME 反推表反转派生 ====================


def test_extension_by_mime_derives_from_mime_by_extension() -> None:
    """MIME 反推表由扩展名正向表反转派生：键集与值域闭合，往返映射一致。

    平行手写双表在新增格式时会漂移；反向表键集须等于支持扩展集经正向表去重
    后的 MIME 值域，值域落在支持扩展集内。
    """
    assert set(EXTENSION_BY_MIME) == {MIME_BY_EXTENSION[ext] for ext in SUPPORTED_IMAGE_EXTENSIONS}
    assert set(EXTENSION_BY_MIME.values()) <= SUPPORTED_IMAGE_EXTENSIONS
    for mime, ext in EXTENSION_BY_MIME.items():
        assert MIME_BY_EXTENSION[ext] == mime


def test_extension_by_mime_resolves_jpeg_conflict_to_canonical_extension() -> None:
    """.jpg 与 .jpeg 同映射 image/jpeg，反转冲突显式择 .jpeg，不依赖键序。"""
    assert MIME_BY_EXTENSION[".jpg"] == MIME_BY_EXTENSION[".jpeg"] == "image/jpeg"
    assert EXTENSION_BY_MIME["image/jpeg"] == ".jpeg"
    assert ".jpg" not in set(EXTENSION_BY_MIME.values())


# ==================== BMP ====================


def _make_bmp(dib_size: int = 40, total_length: int = 64, reserved: bytes = b"\x00" * 4) -> bytes:
    """手工构造合法头字节的 BMP：魔数、文件大小、保留字段、像素偏移与 DIB 头 size。

    offset 14 起的 4 字节为 DIB 头 size 字段，取值须在已知头结构尺寸集合内；
    尾部以零填充至 total_length，像素数据本身不参与签名判定。
    """
    header = (
        b"BM"
        + total_length.to_bytes(4, "little")
        + reserved
        + (14 + dib_size).to_bytes(4, "little")
        + dib_size.to_bytes(4, "little")
    )
    return header + b"\x00" * (total_length - len(header))


def test_infer_extension_returns_bmp_for_valid_signature() -> None:
    """BM 签名且 DIB 头 size 字段合法时识别为 .bmp，扩展名推断不受真实性下界影响。"""
    assert infer_extension_from_bytes(_make_bmp()) == ".bmp"


def test_infer_extension_bmp_accepts_known_dib_header_sizes() -> None:
    """BITMAPCOREHEADER 12、OS/2 简化头 16、BITMAPINFOHEADER 40 与扩展头均识别。"""
    for dib_size in (12, 16, 40, 52, 56, 64, 108, 124):
        assert infer_extension_from_bytes(_make_bmp(dib_size=dib_size)) == ".bmp"


def test_infer_extension_bmp_accepts_nonzero_reserved_fields() -> None:
    """BM 前缀且保留字段非零的合法 BMP 变体同样识别，不据保留字段拒判。"""
    content = _make_bmp(reserved=b"\x01\x00\x00\x00")
    assert infer_extension_from_bytes(content) == ".bmp"


def test_infer_extension_bmp_requires_min_length() -> None:
    """BM 前缀但总长不足 18、读不到完整 DIB 头 size 字段时不识别为 BMP。"""
    content = b"BM" + b"\x00" * 15  # len = 17 < 18
    assert infer_extension_from_bytes(content, default=".jpeg") == ".jpeg"


def test_infer_extension_bmp_rejects_unknown_dib_header_size() -> None:
    """DIB 头 size 字段取值不在已知头结构尺寸集合内时按未知内容处理。"""
    content = _make_bmp(dib_size=36)
    assert infer_extension_from_bytes(content, default=".jpeg") == ".jpeg"
    assert is_known_image_bytes(content) is False


def test_is_known_image_bytes_rejects_bm_prefixed_long_text() -> None:
    """ "BM" 前缀的长文本即使超过 64 字节下界，也因 DIB 头 size 字段非法被拒判。"""
    garbage = b"BM" + b"just some plain text padding here!!" * 2
    assert len(garbage) >= 64
    assert infer_extension_from_bytes(garbage, default=".jpeg") == ".jpeg"
    assert is_known_image_bytes(garbage) is False


# ==================== 下载内容真实性：BMP 按格式分级下界 ====================


def test_is_known_image_bytes_bmp_requires_format_min_length() -> None:
    """BMP 真实性判定施加 64 字节下界：头字段合法但内容不足 64 字节的截断形态被拒。

    扩展名推断对合法 DIB 头放行，真实性校验按格式分级收紧，供下载与
    Base64 解码路径拒绝 Content-Type 伪造的非图片内容。
    """
    short_bmp = _make_bmp(total_length=40)
    assert len(short_bmp) >= 18
    assert len(short_bmp) < 64
    assert infer_extension_from_bytes(short_bmp) == ".bmp"
    assert is_known_image_bytes(short_bmp) is False


def test_is_known_image_bytes_accepts_minimal_valid_bmp() -> None:
    """手工构造合法头字节的最小 BMP 达到下界后通过真实性判定。"""
    content = _make_bmp(total_length=64)

    assert len(content) == 64
    assert is_known_image_bytes(content) is True


def test_is_known_image_bytes_rejects_three_byte_bm_prefix() -> None:
    """3 字节 BM 开头的输入拒绝：短于 BMP 文件头，也不达真实性下界。"""
    assert is_known_image_bytes(b"BMx") is False


def test_is_known_image_bytes_accepts_real_bmp() -> None:
    """正常 BMP 通过真实性判定。"""
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color="white").save(buffer, format="BMP")
    content = buffer.getvalue()

    assert len(content) >= 64
    assert is_known_image_bytes(content) is True


def test_is_known_image_bytes_keeps_other_formats_without_extra_bound() -> None:
    """其余格式不设额外下界：短 magic 前缀仍按推断结果通过真实性判定。"""
    assert is_known_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) is True
    assert is_known_image_bytes(b"GIF89a") is True


# ==================== WEBP ====================


def test_infer_extension_returns_webp_for_valid_signature() -> None:
    """RIFF....WEBP 签名识别为 .webp。"""
    content = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16
    assert infer_extension_from_bytes(content) == ".webp"
    assert is_known_image_bytes(content) is True


def test_infer_extension_webp_rejects_riff_without_webp_brand() -> None:
    """RIFF 前缀但 offset 8:12 非 WEBP 时不识别为 WEBP。"""
    content = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 16
    assert infer_extension_from_bytes(content, default=".jpeg") == ".jpeg"


def test_infer_extension_webp_requires_min_length() -> None:
    """RIFF + WEBP 但总长不足 12 时不识别。"""
    content = b"RIFF" + b"\x00\x00\x00" + b"WEB"  # len = 11 < 12
    assert infer_extension_from_bytes(content, default=".jpeg") == ".jpeg"


# ==================== TIFF ====================


def test_infer_extension_returns_tiff_for_little_endian() -> None:
    """II*\\x00 小端字节序识别为 .tiff。"""
    content = b"II*\x00" + b"\x00" * 16
    assert infer_extension_from_bytes(content) == ".tiff"
    assert is_known_image_bytes(content) is True


def test_infer_extension_returns_tiff_for_big_endian() -> None:
    """MM\\x00* 大端字节序识别为 .tiff。"""
    content = b"MM\x00*" + b"\x00" * 16
    assert infer_extension_from_bytes(content) == ".tiff"
    assert is_known_image_bytes(content) is True


# ==================== 降级与默认值 ====================


def test_infer_extension_returns_default_for_empty_bytes() -> None:
    """空字节降级为 default。"""
    assert infer_extension_from_bytes(b"", default=".png") == ".png"


def test_infer_extension_returns_default_for_unknown_magic() -> None:
    """未知魔法字节降级为 default。"""
    assert infer_extension_from_bytes(b"UNKNOWN\x00" * 4, default=".jpeg") == ".jpeg"
