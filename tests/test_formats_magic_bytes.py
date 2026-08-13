"""守护测试：BMP/WEBP/TIFF 魔法字节推断。

验证 infer_extension_from_bytes 对 BMP、WEBP、TIFF 三种格式的文件头识别，
覆盖正向识别、短字节降级与近似签名误判拒绝，防止扩展名推断回归。
"""

from __future__ import annotations

from seedream_mcp.utils.formats import infer_extension_from_bytes, is_known_image_bytes

# ==================== BMP ====================


def test_infer_extension_returns_bmp_for_valid_signature() -> None:
    """BM 签名且 offset 6-10 保留字段为 0 时识别为 .bmp。"""
    content = b"BM" + b"\x00" * 12  # len >= 14，保留字段 offset 6:10 全零
    assert infer_extension_from_bytes(content) == ".bmp"
    assert is_known_image_bytes(content) is True


def test_infer_extension_bmp_requires_reserved_zeros() -> None:
    """BM 前缀但保留字段非零时不识别为 BMP，降低 2 字节前缀冲突误判。"""
    content = b"BM" + b"\x00" * 4 + b"\x01\x00\x00\x00" + b"\x00" * 4
    assert infer_extension_from_bytes(content, default=".jpeg") == ".jpeg"


def test_infer_extension_bmp_requires_min_length() -> None:
    """BM 前缀但字节不足 14 时不识别为 BMP。"""
    content = b"BM" + b"\x00" * 5  # len = 7 < 14
    assert infer_extension_from_bytes(content, default=".jpeg") == ".jpeg"


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
