"""守护测试：BMP/WEBP/TIFF 魔法字节推断。

验证 infer_extension_from_bytes 对 BMP、WEBP、TIFF 三种格式的文件头识别，
覆盖正向识别、短字节降级与近似签名误判拒绝，防止扩展名推断回归。
"""

from __future__ import annotations

import base64
import io
from types import MappingProxyType

import pytest
from PIL import Image

from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.core.formats import (
    EXTENSIONS_BY_PIL_FORMAT,
    EXTENSION_BY_MIME,
    FORMAT_FAMILIES,
    MIME_BY_EXTENSION,
    PIL_FORMATS_BY_FAMILY,
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS_ORDERED,
    ensure_image_decoders_ready,
    estimate_base64_decoded_size,
    format_file_too_large,
    format_file_too_large_bytes,
    infer_extension_from_bytes,
    is_known_image_bytes,
    parse_data_uri,
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


# ==================== 格式家族表单一来源 ====================


def test_format_families_expand_to_supported_extension_universe() -> None:
    """家族表按族序展开即受支持扩展名全集，锁定展示顺序且族间互不重叠。"""
    flattened = [
        extension
        for family_extensions in FORMAT_FAMILIES.values()
        for extension in family_extensions
    ]
    assert flattened == [
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
    assert len(flattened) == len(set(flattened))
    assert tuple(flattened) == SUPPORTED_IMAGE_EXTENSIONS_ORDERED
    assert frozenset(flattened) == SUPPORTED_IMAGE_EXTENSIONS


def test_pil_format_map_and_download_equivalences_derive_from_families() -> None:
    """PIL 格式映射与下载侧等价类从家族表派生，回退为手写表漂移时在此报警。"""
    from seedream_mcp.utils.io.io_download import _FORMAT_EQUIVALENT_EXTENSIONS

    assert EXTENSIONS_BY_PIL_FORMAT == {
        pil_format: frozenset(family_extensions)
        for family, family_extensions in FORMAT_FAMILIES.items()
        for pil_format in PIL_FORMATS_BY_FAMILY[family]
    }
    # MPO 为 JPEG 家族的多图形态别名，别名条目在单一来源锁定，不散落到消费方。
    assert EXTENSIONS_BY_PIL_FORMAT["MPO"] == frozenset(FORMAT_FAMILIES["jpeg"])
    assert frozenset().union(*EXTENSIONS_BY_PIL_FORMAT.values()) == SUPPORTED_IMAGE_EXTENSIONS
    assert _FORMAT_EQUIVALENT_EXTENSIONS == tuple(
        frozenset(family_extensions)
        for family_extensions in FORMAT_FAMILIES.values()
        if len(family_extensions) > 1
    )


def test_pil_format_names_are_registered_pil_formats() -> None:
    """PIL 格式名逐条目对账 PIL 已注册插件的格式名，拼错或大小写错误必红。

    家族名派生与手写 MPO 都不校验 PIL 注册表，
    未注册的格式名使 EXTENSIONS_BY_PIL_FORMAT 派生出永不放行的键、整族图片在解码准入处静默不可用；
    HEIF 经 pillow_heif 惰性注册，对账前先就绪解码器。
    """
    ensure_image_decoders_ready()
    registered = set(Image.registered_extensions().values())
    for pil_formats in PIL_FORMATS_BY_FAMILY.values():
        for pil_format in pil_formats:
            assert pil_format in registered


# ==================== parse_data_uri ====================


def test_parse_data_uri_returns_base64_flag() -> None:
    """解析返回 base64 编码标记，消费方不再自行切片 header 判定。"""
    assert parse_data_uri("data:image/png;base64,aGk=") == ("image/png", "aGk=", True)
    assert parse_data_uri("DATA:image/png;BASE64,aGk=") == ("image/png", "aGk=", True)
    assert parse_data_uri("data:image/png,aGk=") == ("image/png", "aGk=", False)


@pytest.mark.parametrize("param_name", ["base64url", "base64xyz"])
def test_parse_data_uri_base64_prefixed_param_names_not_flagged(param_name: str) -> None:
    """以 base64 开头的非 base64 参数名不判为编码声明，is_base64 按参数名精确比较。"""
    assert parse_data_uri(f"data:image/png;{param_name},aGk=") == ("image/png", "aGk=", False)


def test_parse_data_uri_non_data_input_returns_sentinel_triple() -> None:
    """非 data URI、缺逗号与非字符串入参返回 (None, 原样入参, False)。"""
    assert parse_data_uri("https://example.com/x.png") == (
        None,
        "https://example.com/x.png",
        False,
    )
    assert parse_data_uri("data:image/png;base64") == (None, "data:image/png;base64", False)
    assert parse_data_uri(b"not-a-string") == (None, b"not-a-string", False)


# ==================== 数据大小超限文案单一来源 ====================


def test_validate_image_input_rejects_oversized_data_uri_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """巨型 base64 在解码前按换算的估算字节数拒绝，超限文案经 format_file_too_large 收口。"""
    import seedream_mcp.utils.images.image_validation as image_validation_module

    max_size = 1024 * 1024
    monkeypatch.setattr(image_validation_module, "MAX_IMAGE_FILE_SIZE", max_size)
    huge_b64 = "A" * 2_000_000
    with pytest.raises(SeedreamValidationError, match="数据过大") as exc_info:
        image_validation_module.validate_image_input(f"data:image/png;base64,{huge_b64}")

    estimated_bytes = 2_000_000 * 3 // 4
    assert exc_info.value.message == format_file_too_large(estimated_bytes, max_size, label="数据")
    assert "base64 长度" not in exc_info.value.message


def test_format_file_too_large_bytes_keeps_exact_byte_counts() -> None:
    """字节口径超限文案保留精确字节数，亚 MB 值不因 MB 取整失真。"""
    assert (
        format_file_too_large_bytes(1000, 600, label="响应体")
        == "响应体过大: 1000 字节，最大支持600 字节"
    )


# ==================== base64 解码尺寸估算单一来源 ====================


@pytest.mark.parametrize("size", [3, 4, 5])
def test_estimate_base64_decoded_size_equals_actual_decoded_bytes(size: int) -> None:
    """合法 base64 的估算恰等于实际解码字节数，无 padding、单 = 与双 = 三档对齐。"""
    payload = base64.b64encode(b"x" * size).decode()
    assert estimate_base64_decoded_size(payload, limit=size) == size


def test_estimate_base64_decoded_size_keeps_oversized_without_whitespace() -> None:
    """无空白载荷剥离复核后估算不变，超限口径不放松。"""
    assert estimate_base64_decoded_size("A" * 200, limit=100) == 150


def test_estimate_base64_decoded_size_refines_whitespace_when_naive_over() -> None:
    """朴素上界超限时剥离空白复核：折行与 NBSP 载荷按净长度估算，入限不误拒。"""
    clean = base64.b64encode(b"x" * 300).decode()
    folded = "\r\n".join(clean[index : index + 76] for index in range(0, len(clean), 76))
    dirty = clean[:5] + "\xa0" * 8 + clean[5:]
    assert estimate_base64_decoded_size(clean, limit=300) == 300
    assert estimate_base64_decoded_size(folded, limit=300) == 300
    assert estimate_base64_decoded_size(dirty, limit=300) == 300


def test_trailing_base64_padding_counts_full_trailing_run() -> None:
    """尾部空白剥离后完整统计连续 '=' 串，不设扫描窗口。"""
    from seedream_mcp.utils.core.formats import _trailing_base64_padding

    clean = base64.b64encode(b"x" * 301).decode()
    assert clean.endswith("==")
    assert _trailing_base64_padding(clean + "\n" * 100) == 2
    assert _trailing_base64_padding("QUFB" + "=" * 100) == 100


def test_trailing_base64_padding_distinguishes_whitespace_around_padding() -> None:
    """padding 前空白属折行数据区、padding 后空白属尾部，两侧计数同为 2。"""
    from seedream_mcp.utils.core.formats import _trailing_base64_padding

    assert _trailing_base64_padding("QUFB\n==") == 2
    assert _trailing_base64_padding("QUFB==" + "\n" * 63) == 2
    assert _trailing_base64_padding("QUFB\n==" + "\n" * 63) == 2


def test_trailing_base64_padding_boundary_shapes() -> None:
    """无 padding、纯 padding、纯空白与空白隔断等边界形态计数与切片口径一致。"""
    from seedream_mcp.utils.core.formats import _trailing_base64_padding

    assert _trailing_base64_padding("") == 0
    assert _trailing_base64_padding(" \n\t ") == 0
    assert _trailing_base64_padding("QUFB") == 0
    assert _trailing_base64_padding("QUFB\n") == 0
    assert _trailing_base64_padding("=" * 5) == 5
    assert _trailing_base64_padding("\n" + "=" * 5) == 5
    # '=' 串中隔空白只计末段连续 run，与 rstrip 先剥尾部空白再数等号的口径一致。
    assert _trailing_base64_padding("QQ==\n=") == 1


def test_estimate_base64_decoded_size_stays_upper_bound_behind_trailing_whitespace() -> None:
    """padding 后跟长尾部空白时仍被完整剔除，估算保持上界。"""
    clean = base64.b64encode(b"x" * 301).decode()
    assert clean.endswith("==")
    exact = (len(clean) - 2) * 3 // 4
    estimate = estimate_base64_decoded_size(clean + "\n" * 100, limit=300)
    assert exact <= estimate <= exact + 2


def test_estimate_base64_decoded_size_accepts_boundary_payload_with_hidden_padding() -> None:
    """恰等于上限的载荷在尾部空白剥离后 padding 仍完整剔除时不误拒。"""
    clean = base64.b64encode(b"x" * 301).decode()
    assert clean.endswith("==")
    assert estimate_base64_decoded_size(clean + "\n" * 100, limit=301) == 301


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
