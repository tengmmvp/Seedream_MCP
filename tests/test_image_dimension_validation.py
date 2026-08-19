"""参考图维度校验三层防护测试：最短边、宽高比与总像素边界及解码包装分支。

测试图片经 PIL 在内存或临时目录生成，不依赖外部文件；解压炸弹用例通过调低
Image.MAX_IMAGE_PIXELS 触发，避免构造真实大图占用内存。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image
from PIL.Image import UnidentifiedImageError

from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.images import image_validation
from seedream_mcp.utils.images.image_validation import (
    MAX_IMAGE_PIXELS,
    MIN_IMAGE_EDGE,
    _validate_image_dimensions,
    decode_and_validate_dimensions,
    validate_image_input,
    validate_image_path,
)


def _png_bytes(width: int, height: int) -> bytes:
    """生成指定宽高的内存 PNG 字节。"""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def _write_image(tmp_path: Path, name: str, width: int, height: int) -> Path:
    """在临时目录写入指定宽高的 PNG 文件并返回路径。"""
    path = tmp_path / name
    path.write_bytes(_png_bytes(width, height))
    return path


def _png_data_uri(width: int, height: int) -> str:
    """生成指定宽高 PNG 的 data URI。"""
    return "data:image/png;base64," + base64.b64encode(_png_bytes(width, height)).decode()


def _lower_pil_pixel_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 PIL 解压炸弹阈值调低到 10000 像素，供小图触发防护分支。

    同时固定注册哨兵为已注册，防止 _ensure_heif_opener_registered 在解码前用
    模块常量 3600 万覆盖已调低的阈值。
    """
    monkeypatch.setattr(image_validation, "_heif_opener_registered", True)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10000)


# ==================== _validate_image_dimensions 数值边界 ====================


def test_min_edge_rejects_14px_and_accepts_15px() -> None:
    """任一边低于 15px 被拒，双边恰为 15px 通过。"""
    assert MIN_IMAGE_EDGE == 15
    with pytest.raises(SeedreamValidationError, match="图像宽高长度至少15px"):
        _validate_image_dimensions(14, 15, "small.png")
    with pytest.raises(SeedreamValidationError, match="图像宽高长度至少15px"):
        _validate_image_dimensions(15, 14, "small.png")
    _validate_image_dimensions(15, 15, "small.png")


def test_ratio_accepts_exactly_1_to_16_and_16_to_1() -> None:
    """宽高比恰好落在上下限 16 与 1/16 时通过。"""
    _validate_image_dimensions(240, 15, "wide.png")
    _validate_image_dimensions(15, 240, "tall.png")


def test_ratio_rejects_1_to_17_and_17_to_1() -> None:
    """宽高比越过上下限即被拒。"""
    with pytest.raises(SeedreamValidationError, match="宽高比"):
        _validate_image_dimensions(255, 15, "wide.png")
    with pytest.raises(SeedreamValidationError, match="宽高比"):
        _validate_image_dimensions(15, 255, "tall.png")


def test_total_pixels_accepts_limit_and_limit_minus_one() -> None:
    """总像素恰为上限 3600 万与上限减一通过，超限被拒。

    上限加一不存在满足最短边与宽高比约束的整数宽高组合，以最小超限组合
    6000x6001 断言拒绝。
    """
    assert MAX_IMAGE_PIXELS == 36_000_000
    _validate_image_dimensions(6000, 6000, "limit.png")
    _validate_image_dimensions(5999, 6001, "limit-minus-one.png")
    with pytest.raises(SeedreamValidationError, match="图像总像素不能超过"):
        _validate_image_dimensions(6000, 6001, "over-limit.png")


def test_dimension_error_carries_field_and_value() -> None:
    """维度校验失败附带的 field 与 value 供上游定位出错的图像输入。"""
    with pytest.raises(SeedreamValidationError) as exc_info:
        _validate_image_dimensions(10, 10, "small.png")
    assert exc_info.value.field == "image"
    assert exc_info.value.value == "small.png"


# ==================== decode_and_validate_dimensions ====================


def test_decode_accepts_valid_png_bytes() -> None:
    """合法 PNG 字节经解码校验后通过。"""
    decode_and_validate_dimensions(_png_bytes(32, 32), "valid.png")


def test_decode_enforces_dimension_rules_on_decoded_size() -> None:
    """解码后的实际尺寸仍受三层维度规则约束。"""
    with pytest.raises(SeedreamValidationError, match="图像宽高长度至少15px"):
        decode_and_validate_dimensions(_png_bytes(10, 10), "tiny.png")


def test_decode_propagates_pil_error_for_non_image_bytes() -> None:
    """非图片字节以 PIL 原生 UnidentifiedImageError 传播，由调用方包装。"""
    with pytest.raises(UnidentifiedImageError):
        decode_and_validate_dimensions(b"definitely not an image", "fake.png")


def test_decode_raises_decompression_bomb_error_when_limit_lowered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """像素超过调低后的 PIL 阈值两倍时，open 阶段抛 DecompressionBombError。"""
    _lower_pil_pixel_limit(monkeypatch)
    with pytest.raises(Image.DecompressionBombError):
        decode_and_validate_dimensions(_png_bytes(200, 200), "bomb.png")


# ==================== validate_image_input 本地文件路径 ====================


def test_validate_image_input_accepts_valid_local_png(tmp_path: Path) -> None:
    """合法尺寸的本地 PNG 通过并返回规范化绝对路径。"""
    path = _write_image(tmp_path, "valid.png", 32, 32)
    result = validate_image_input(str(path))
    assert Path(result).resolve() == path.resolve()


def test_validate_image_input_wraps_non_image_local_file_as_unidentified(
    tmp_path: Path,
) -> None:
    """本地文件内容不可识别时包装为固定文案，不泄露 BytesIO 对象地址。"""
    path = tmp_path / "fake.png"
    path.write_bytes(b"definitely not an image")
    with pytest.raises(SeedreamValidationError, match="无法识别的图像内容") as exc_info:
        validate_image_input(str(path), skip_dimensions=False)
    assert "_io.BytesIO" not in exc_info.value.message


def test_validate_image_input_wraps_decompression_bomb_for_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PIL 解压炸弹在本地文件路径被包装为图像维度解析失败。"""
    path = _write_image(tmp_path, "bomb.png", 200, 200)
    _lower_pil_pixel_limit(monkeypatch)
    with pytest.raises(SeedreamValidationError, match="图像维度解析失败"):
        validate_image_input(str(path), skip_dimensions=False)


# ==================== validate_image_input 的 skip_dimensions 差异 ====================


def test_skip_dimensions_bypasses_local_dimension_rules(tmp_path: Path) -> None:
    """skip_dimensions 为 True 时跳过像素维度校验，False 时按尺寸下限拒绝。"""
    path = _write_image(tmp_path, "tiny.png", 10, 10)
    with pytest.raises(SeedreamValidationError, match="图像宽高长度至少15px"):
        validate_image_input(str(path), skip_dimensions=False)
    result = validate_image_input(str(path), skip_dimensions=True)
    assert Path(result).resolve() == path.resolve()


def test_skip_dimensions_bypasses_local_decode_failure(tmp_path: Path) -> None:
    """skip_dimensions 为 True 时不做 PIL 解码，损坏字节的图片文件仍通过。"""
    path = tmp_path / "fake.png"
    path.write_bytes(b"definitely not an image")
    with pytest.raises(SeedreamValidationError, match="无法识别的图像内容"):
        validate_image_input(str(path), skip_dimensions=False)
    result = validate_image_input(str(path), skip_dimensions=True)
    assert Path(result).resolve() == path.resolve()


def test_skip_dimensions_does_not_affect_data_uri_validation() -> None:
    """skip_dimensions 仅作用于本地文件路径，Data URI 恒做维度校验。"""
    data_uri = _png_data_uri(10, 10)
    with pytest.raises(SeedreamValidationError, match="图像宽高长度至少15px"):
        validate_image_input(data_uri, skip_dimensions=False)
    with pytest.raises(SeedreamValidationError, match="图像宽高长度至少15px"):
        validate_image_input(data_uri, skip_dimensions=True)


# ==================== validate_image_input Data URI ====================


def test_validate_image_input_accepts_valid_data_uri() -> None:
    """合法尺寸的小写 Data URI 通过并原样返回。"""
    data_uri = _png_data_uri(32, 32)
    assert validate_image_input(data_uri) == data_uri


def test_validate_image_input_normalizes_data_uri_media_type() -> None:
    """官方要求格式小写：大写格式与 image/jpg 归一为小写标准 MIME 后返回。"""
    data_uri = _png_data_uri(32, 32)
    # _png_data_uri 产出小写 png 头；手工改写为大写形式验证归一化。
    upper_uri = data_uri.replace("data:image/png", "data:image/PNG")
    assert validate_image_input(upper_uri) == data_uri

    payload = data_uri.split(",", 1)[1]
    jpg_uri = f"data:image/jpg;base64,{payload}"
    assert validate_image_input(jpg_uri) == f"data:image/jpeg;base64,{payload}"


def test_validate_image_input_wraps_non_image_data_uri_as_unidentified() -> None:
    """Data URI 负载不可识别时包装为固定文案，不泄露 BytesIO 对象地址。"""
    payload = base64.b64encode(b"definitely not an image").decode()
    with pytest.raises(SeedreamValidationError, match="无法识别的图像内容") as exc_info:
        validate_image_input(f"data:image/png;base64,{payload}")
    assert "_io.BytesIO" not in exc_info.value.message


# ==================== validate_image_path 的非本地引用短路 ====================


@pytest.mark.parametrize(
    "reference",
    ["https://example.com/x.png", "data:image/png;base64,iVBORw0KGgo="],
)
def test_validate_image_path_short_circuits_non_local_references(reference: str) -> None:
    """URL 与 Data URI 同口径短路：视为有效引用且路径为 None，不当本地路径处理。

    Data URI 此前落入本地路径分支，被拼接为畸形文件名后误报不存在；其内容校验
    由 validate_image_input 承担。
    """
    is_valid, error, normalized = validate_image_path(reference, base_dir="/nonexistent")

    assert (is_valid, error, normalized) == (True, "", None)
