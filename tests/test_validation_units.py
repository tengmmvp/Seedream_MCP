"""校验模块纯函数单元测试。

覆盖水印、响应格式、整数范围强制转换、最大图像数量与并行生成参数组合约束，
不触发网络或文件 I/O，直接验证各分支的返回值与抛错语义。
"""

import pytest

from seedream_mcp.utils.errors import SeedreamValidationError
from seedream_mcp.utils.validation import (
    MAX_SEQUENTIAL_TOTAL_IMAGES,
    _coerce_positive_int_in_range,
    validate_max_images,
    validate_parallel_generation_options,
    validate_response_format,
    validate_watermark,
)

# ==================== validate_watermark ====================


def test_validate_watermark_bool_true() -> None:
    assert validate_watermark(True) is True


def test_validate_watermark_bool_false() -> None:
    assert validate_watermark(False) is False


@pytest.mark.parametrize("val", ["true", "TRUE", "True", "1", "yes", "YES", "on"])
def test_validate_watermark_truthy_strings(val: str) -> None:
    assert validate_watermark(val) is True


@pytest.mark.parametrize("val", ["false", "FALSE", "0", "no", "No", "off"])
def test_validate_watermark_falsy_strings(val: str) -> None:
    assert validate_watermark(val) is False


def test_validate_watermark_strips_whitespace() -> None:
    assert validate_watermark("  true  ") is True
    assert validate_watermark("  off\n") is False


def test_validate_watermark_invalid_string() -> None:
    with pytest.raises(SeedreamValidationError, match="水印参数"):
        validate_watermark("maybe")


def test_validate_watermark_invalid_type() -> None:
    with pytest.raises(SeedreamValidationError, match="水印参数"):
        validate_watermark(123)


def test_validate_watermark_none_rejected() -> None:
    with pytest.raises(SeedreamValidationError, match="水印参数"):
        validate_watermark(None)


# ==================== validate_response_format ====================


def test_validate_response_format_url() -> None:
    assert validate_response_format("url") == "url"


def test_validate_response_format_b64_json() -> None:
    assert validate_response_format("b64_json") == "b64_json"


def test_validate_response_format_normalizes_case_and_whitespace() -> None:
    assert validate_response_format("URL") == "url"
    assert validate_response_format(" B64_JSON ") == "b64_json"


def test_validate_response_format_invalid_value() -> None:
    with pytest.raises(SeedreamValidationError, match="响应格式"):
        validate_response_format("jpeg")


def test_validate_response_format_empty() -> None:
    with pytest.raises(SeedreamValidationError):
        validate_response_format("")


def test_validate_response_format_non_string() -> None:
    with pytest.raises(SeedreamValidationError):
        validate_response_format(123)


# ==================== validate_max_images ====================


def test_validate_max_images_int() -> None:
    assert validate_max_images(5) == 5


def test_validate_max_images_boundary_min() -> None:
    assert validate_max_images(1) == 1


def test_validate_max_images_boundary_max() -> None:
    assert validate_max_images(MAX_SEQUENTIAL_TOTAL_IMAGES) == MAX_SEQUENTIAL_TOTAL_IMAGES


def test_validate_max_images_integer_float() -> None:
    """整数浮点（如 3.0）允许转换为 int。"""
    assert validate_max_images(3.0) == 3


def test_validate_max_images_non_integer_float() -> None:
    """非整数浮点拒绝，避免静默截断。"""
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        validate_max_images(3.5)


def test_validate_max_images_bool_rejected() -> None:
    """bool 是 int 子类但须被显式拒绝。"""
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        validate_max_images(True)


def test_validate_max_images_string_int() -> None:
    assert validate_max_images("4") == 4


def test_validate_max_images_below_min() -> None:
    with pytest.raises(SeedreamValidationError, match="必须在"):
        validate_max_images(0)


def test_validate_max_images_above_max() -> None:
    with pytest.raises(SeedreamValidationError, match="必须在"):
        validate_max_images(MAX_SEQUENTIAL_TOTAL_IMAGES + 1)


# ==================== _coerce_positive_int_in_range ====================


def test_coerce_int_in_range() -> None:
    assert _coerce_positive_int_in_range(5, "f", 1, 10) == 5


def test_coerce_integer_float() -> None:
    assert _coerce_positive_int_in_range(5.0, "f", 1, 10) == 5


def test_coerce_non_integer_float_rejected() -> None:
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        _coerce_positive_int_in_range(5.5, "f", 1, 10)


def test_coerce_bool_rejected() -> None:
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        _coerce_positive_int_in_range(True, "f", 1, 10)


def test_coerce_false_bool_rejected() -> None:
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        _coerce_positive_int_in_range(False, "f", 1, 10)


def test_coerce_string_int() -> None:
    assert _coerce_positive_int_in_range("3", "f", 1, 10) == 3


def test_coerce_invalid_string() -> None:
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        _coerce_positive_int_in_range("abc", "f", 1, 10)


def test_coerce_none_rejected() -> None:
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        _coerce_positive_int_in_range(None, "f", 1, 10)


def test_coerce_below_min() -> None:
    with pytest.raises(SeedreamValidationError, match="必须在"):
        _coerce_positive_int_in_range(0, "f", 1, 10)


def test_coerce_above_max() -> None:
    with pytest.raises(SeedreamValidationError, match="必须在"):
        _coerce_positive_int_in_range(11, "f", 1, 10)


def test_coerce_boundary_min() -> None:
    assert _coerce_positive_int_in_range(1, "f", 1, 10) == 1


def test_coerce_boundary_max() -> None:
    assert _coerce_positive_int_in_range(10, "f", 1, 10) == 10


# ==================== validate_parallel_generation_options ====================


def test_parallel_options_single_request_default_parallelism() -> None:
    """request_count=1、parallelism=None 时 parallelism 取 min(1, max)=1。"""
    rc, par = validate_parallel_generation_options(request_count=1, parallelism=None, stream=False)
    assert rc == 1
    assert par == 1


def test_parallel_options_defaults_parallelism_to_request_count() -> None:
    """parallelism=None 时取 min(request_count, max_request_count)。"""
    rc, par = validate_parallel_generation_options(request_count=3, parallelism=None, stream=False)
    assert rc == 3
    assert par == 3


def test_parallel_options_default_parallelism_capped_by_max() -> None:
    rc, par = validate_parallel_generation_options(request_count=4, parallelism=None, stream=False)
    assert par == 4


def test_parallel_options_explicit_parallelism() -> None:
    rc, par = validate_parallel_generation_options(request_count=4, parallelism=2, stream=False)
    assert rc == 4
    assert par == 2


def test_parallel_options_parallelism_exceeds_request_count() -> None:
    with pytest.raises(SeedreamValidationError, match="不能大于"):
        validate_parallel_generation_options(request_count=2, parallelism=3, stream=False)


def test_parallel_options_stream_single_request() -> None:
    """stream=true 时 request_count 必须为 1。"""
    rc, par = validate_parallel_generation_options(request_count=1, parallelism=None, stream=True)
    assert rc == 1


def test_parallel_options_stream_rejects_multi_request() -> None:
    with pytest.raises(SeedreamValidationError, match="stream=true"):
        validate_parallel_generation_options(request_count=2, parallelism=None, stream=True)


def test_parallel_options_request_count_above_max() -> None:
    with pytest.raises(SeedreamValidationError):
        validate_parallel_generation_options(request_count=5, parallelism=None, stream=False)


def test_parallel_options_parallelism_above_max() -> None:
    with pytest.raises(SeedreamValidationError):
        validate_parallel_generation_options(request_count=4, parallelism=5, stream=False)


def test_parallel_options_parallelism_equal_request_count_ok() -> None:
    """parallelism == request_count 不触发"不能大于"约束。"""
    rc, par = validate_parallel_generation_options(request_count=3, parallelism=3, stream=False)
    assert rc == 3
    assert par == 3
