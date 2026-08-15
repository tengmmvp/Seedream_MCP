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


# ==================== BMP ====================


def test_infer_extension_returns_bmp_for_valid_signature() -> None:
    """BM 签名且文件头完整（14 字节）时识别为 .bmp，扩展名推断不受真实性下界影响。"""
    content = b"BM" + b"\x00" * 12  # len >= 14
    assert infer_extension_from_bytes(content) == ".bmp"


def test_infer_extension_bmp_accepts_nonzero_reserved_fields() -> None:
    """BM 前缀且保留字段非零的合法 BMP 变体同样识别，不据保留字段拒判。"""
    content = b"BM" + b"\x00" * 4 + b"\x01\x00\x00\x00" + b"\x00" * 4
    assert infer_extension_from_bytes(content) == ".bmp"


def test_infer_extension_bmp_requires_min_length() -> None:
    """BM 前缀但字节不足 14 时不识别为 BMP。"""
    content = b"BM" + b"\x00" * 5  # len = 7 < 14
    assert infer_extension_from_bytes(content, default=".jpeg") == ".jpeg"


# ==================== 下载内容真实性：BMP 按格式分级下界 ====================


def test_is_known_image_bytes_bmp_requires_format_min_length() -> None:
    """BMP 真实性判定施加 64 字节下界：BM 开头的短文本不再误判为图片。

    扩展名推断对 14 字节文件头仍放行，真实性校验按格式分级收紧，供下载与
    Base64 解码路径拒绝 Content-Type 伪造的非图片内容。
    """
    short_text = b"BM" + b"just some plain text padding here!!"
    assert len(short_text) >= 14
    assert len(short_text) < 64
    assert infer_extension_from_bytes(short_text) == ".bmp"
    assert is_known_image_bytes(short_text) is False


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
