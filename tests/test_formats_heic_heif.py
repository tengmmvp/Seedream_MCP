"""守护测试：HEIC/HEIF ISO BMFF 字节签名推断。

验证 infer_extension_from_bytes 对 ISO BMFF ftyp box 的 brand 识别，
覆盖 _HEIC_BRANDS 与 _HEIF_BRANDS，防止扩展名推断回归。
"""

from seedream_mcp.utils.core.formats import (
    infer_extension_from_bytes,
    is_known_image_bytes,
)


def _make_iso_bmff(brand: bytes) -> bytes:
    """构造 ISO BMFF ftyp box：4 字节 size + 'ftyp' + 4 字节 brand + 填充。

    offset 4:8 = b'ftyp'，offset 8:12 = brand，对齐 infer_extension_from_bytes 的判定。
    """
    return b"\x00\x00\x00\x1c" + b"ftyp" + brand + b"\x00" * 16


def test_infer_extension_returns_heic_for_heic_brand() -> None:
    content = _make_iso_bmff(b"heic")
    assert infer_extension_from_bytes(content) == ".heic"
    assert is_known_image_bytes(content) is True


def test_infer_extension_returns_heic_for_heix_brand() -> None:
    assert infer_extension_from_bytes(_make_iso_bmff(b"heix")) == ".heic"


def test_infer_extension_returns_heic_for_hevc_brand() -> None:
    assert infer_extension_from_bytes(_make_iso_bmff(b"hevc")) == ".heic"


def test_infer_extension_returns_heif_for_mif1_brand() -> None:
    content = _make_iso_bmff(b"mif1")
    assert infer_extension_from_bytes(content) == ".heif"
    assert is_known_image_bytes(content) is True


def test_infer_extension_returns_heif_for_msf1_brand() -> None:
    assert infer_extension_from_bytes(_make_iso_bmff(b"msf1")) == ".heif"


def test_infer_extension_falls_back_to_default_for_unknown_brand() -> None:
    """ftyp box 但 brand 非已知 HEIC/HEIF 时，回落到 default。"""
    content = _make_iso_bmff(b"XXXX")
    assert infer_extension_from_bytes(content, default=".jpeg") == ".jpeg"


def test_is_known_image_bytes_returns_false_for_non_image_magic() -> None:
    """非已知图片魔法字节视为未知，供下载字节校验拒绝。"""
    assert is_known_image_bytes(b"\x00" * 32) is False
