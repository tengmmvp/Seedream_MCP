"""校验模块纯函数单元测试。

覆盖水印、响应格式、整数范围强制转换、最大图像数量与并行生成参数组合约束，
不触发网络或文件 I/O，直接验证各分支的返回值与抛错语义。
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from seedream_mcp.utils.core.errors import SeedreamConfigError, SeedreamValidationError
from seedream_mcp.utils.core.validators import (
    MAX_SEQUENTIAL_TOTAL_IMAGES,
    _coerce_positive_int_in_range,
    parse_bool,
    validate_background,
    validate_max_images,
    validate_optimize_prompt_options,
    validate_parallel_generation_options,
    validate_response_format,
    validate_watermark,
)
from seedream_mcp.utils.model.model_capabilities import ModelCapabilities

# 5.0 Pro 模型标识，background 透明通道参数仅该家族支持。
_PRO_MODEL_ID = "doubao-seedream-5-0-pro-260628"

# ==================== parse_bool ====================


def test_parse_bool_error_message_is_plain_text() -> None:
    """解析失败消息为纯文本：分隔符用正斜杠，不因反斜杠转义出控制字符。"""
    with pytest.raises(SeedreamConfigError) as exc_info:
        parse_bool("maybe")

    message = exc_info.value.message
    assert "true/false/yes/no/on/off/1/0" in message
    assert all(ord(ch) >= 0x20 for ch in message)


def test_parse_bool_none_returns_false() -> None:
    """None 视为未配置返回 False，与 docstring 声明一致，不进入解析失败分支。"""
    assert parse_bool(None) is False


def test_deprecated_model_tokens_is_immutable_frozenset() -> None:
    """已下线模型 token 清单为 frozenset，公共清单不可被原地变异。"""
    from seedream_mcp.utils.model.model_capabilities import DEPRECATED_MODEL_TOKENS

    assert isinstance(DEPRECATED_MODEL_TOKENS, frozenset)
    assert "doubao-seedream-3-0" in DEPRECATED_MODEL_TOKENS
    with pytest.raises(AttributeError):
        DEPRECATED_MODEL_TOKENS.add("doubao-seedream-x")  # type: ignore[attr-defined]


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
    with pytest.raises(SeedreamValidationError, match="response_format"):
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


@pytest.mark.parametrize("value", [Decimal("2.9"), Fraction(5, 2)])
def test_coerce_non_integer_rational_rejected(value: Decimal | Fraction) -> None:
    """Decimal 与 Fraction 的非整数值拒绝，与 float 分支同规则，不静默截断。"""
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        _coerce_positive_int_in_range(value, "f", 1, 10)


def test_coerce_integer_decimal_accepted() -> None:
    """整数值的 Decimal 允许转换为 int。"""
    assert _coerce_positive_int_in_range(Decimal("2"), "f", 1, 10) == 2


@pytest.mark.parametrize("value", [Decimal("Infinity"), Decimal("-Infinity")])
def test_coerce_infinite_decimal_rejected(value: Decimal) -> None:
    """Decimal 无穷经 int() 抛 OverflowError，转译为参数校验错误不外逃。"""
    with pytest.raises(SeedreamValidationError, match="必须是整数"):
        _coerce_positive_int_in_range(value, "f", 1, 10)


def test_coerce_fraction_infinity_boundary_unconstructible() -> None:
    """Fraction 无法表示无穷，构造即抛 OverflowError，该形态到不了校验层。"""
    with pytest.raises(OverflowError):
        Fraction(Decimal("Infinity"))


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
        validate_parallel_generation_options(request_count=11, parallelism=None, stream=False)


def test_parallel_options_parallelism_above_max() -> None:
    with pytest.raises(SeedreamValidationError):
        validate_parallel_generation_options(request_count=10, parallelism=11, stream=False)


def test_parallel_options_parallelism_equal_request_count_ok() -> None:
    """parallelism == request_count 不触发「不能大于」约束。"""
    rc, par = validate_parallel_generation_options(request_count=3, parallelism=3, stream=False)
    assert rc == 3
    assert par == 3


# ==================== validate_optimize_prompt_options ====================


def test_optimize_options_accept_mode_only() -> None:
    """仅含 mode 的合法取值通过；缺省 mode 归一化为 standard。"""
    model_id = "doubao-seedream-4-0-250828"
    assert validate_optimize_prompt_options({"mode": "fast"}, model_id) == {"mode": "fast"}
    assert validate_optimize_prompt_options({}, model_id) == {"mode": "standard"}
    assert validate_optimize_prompt_options(None, model_id) is None


def test_optimize_options_reject_unknown_keys() -> None:
    """未知字段显式拒绝而非静默丢弃，与 validate_generation_tools 的口径一致。"""
    with pytest.raises(SeedreamValidationError, match="包含不支持的字段"):
        validate_optimize_prompt_options(
            {"mode": "standard", "level": 3}, "doubao-seedream-4-0-250828"
        )


# ==================== validate_background：output_format 类型防御 ====================


def test_validate_background_rejects_non_string_output_format() -> None:
    """output_format 非字符串时显式报错，与 background 参数的类型防御口径一致。"""
    with pytest.raises(SeedreamValidationError, match="output_format 必须为字符串"):
        validate_background("transparent", _PRO_MODEL_ID, output_format=123)


def test_validate_background_rejects_non_string_output_format_for_opaque() -> None:
    """background 取 opaque 时同样先做 output_format 类型校验，不因短路跳过。"""
    with pytest.raises(SeedreamValidationError, match="output_format 必须为字符串"):
        validate_background("opaque", _PRO_MODEL_ID, output_format=["jpeg"])


# ==================== 单边像素区间约束 ====================


def _make_caps(min_pixels: int | None, max_pixels: int | None) -> ModelCapabilities:
    """构造仅像素上下限可变、其余字段取默认的测试能力声明。"""
    return ModelCapabilities(
        family="test-family",
        display_name="测试模型",
        supports_output_format=True,
        supports_tools=True,
        supports_stream=True,
        max_reference_images=14,
        allowed_presets=frozenset({"1K"}),
        min_size_pixels=min_pixels,
        max_size_pixels=max_pixels,
        size_pixel_multiple=None,
    )


def test_size_single_sided_min_bound_applies_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仅声明 min 时下限独立生效，不因 max 未声明而整体跳过像素区间校验。"""
    import seedream_mcp.utils.core.validators as validators_module

    monkeypatch.setattr(
        validators_module, "get_model_capabilities", lambda _mid: _make_caps(1_000, None)
    )
    with pytest.raises(SeedreamValidationError, match="总像素需不低于 1000"):
        validators_module.validate_size_for_model("10x10", "any-model")
    assert validators_module.validate_size_for_model("40x40", "any-model") == "40x40"


def test_size_single_sided_max_bound_applies_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仅声明 max 时上限独立生效。"""
    import seedream_mcp.utils.core.validators as validators_module

    monkeypatch.setattr(
        validators_module, "get_model_capabilities", lambda _mid: _make_caps(None, 1_000)
    )
    with pytest.raises(SeedreamValidationError, match="总像素需不超过 1000"):
        validators_module.validate_size_for_model("50x50", "any-model")
    assert validators_module.validate_size_for_model("20x20", "any-model") == "20x20"


def test_size_both_bounds_message_keeps_range_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """双边声明时错误消息保持区间形式，既有调用方依赖的文案不变。"""
    import seedream_mcp.utils.core.validators as validators_module

    monkeypatch.setattr(
        validators_module, "get_model_capabilities", lambda _mid: _make_caps(1_000, 2_000)
    )
    with pytest.raises(SeedreamValidationError, match=r"总像素需在 \[1000, 2000\] 范围内"):
        validators_module.validate_size_for_model("80x80", "any-model")
