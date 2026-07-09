"""Seedream 5.0 Pro 能力差异校验。

5.0 Pro 的 Model ID（doubao-seedream-5-0-pro-*）包含 "doubao-seedream-5-0" 子串，
历史上会被误判为 5.0 Lite。本模块回归其与 5.0 Lite 在工具、输出格式、参考图上限、
提示词优化模式上的差异。
"""

from __future__ import annotations

import pytest

from seedream_mcp.utils.errors import SeedreamValidationError
from seedream_mcp.utils.validation import (
    get_max_reference_images,
    is_seedream_50_pro_model,
    validate_generation_tools,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_stream,
)

PRO = "doubao-seedream-5-0-pro-260628"
LITE = "doubao-seedream-5-0-260128"
MODEL_45 = "doubao-seedream-4-5-251128"
MODEL_40 = "doubao-seedream-4-0-250828"


# ==================== 模型识别（根因回归） ====================


def test_pro_model_detected_as_pro() -> None:
    assert is_seedream_50_pro_model(PRO) is True


def test_lite_model_not_detected_as_pro() -> None:
    assert is_seedream_50_pro_model(LITE) is False


def test_pro_alias_detected_as_pro() -> None:
    assert is_seedream_50_pro_model("doubao-seedream-5.0-pro") is True


# ==================== tools（联网搜索）仅 5.0 Lite 支持 ====================


def test_tools_accepted_for_lite() -> None:
    assert validate_generation_tools([{"type": "web_search"}], LITE) == [{"type": "web_search"}]


def test_tools_rejected_for_pro() -> None:
    with pytest.raises(SeedreamValidationError, match="5.0 Lite"):
        validate_generation_tools([{"type": "web_search"}], PRO)


def test_tools_rejected_for_45() -> None:
    with pytest.raises(SeedreamValidationError, match="5.0 Lite"):
        validate_generation_tools([{"type": "web_search"}], MODEL_45)


# ==================== output_format 5.0 系列支持 ====================


def test_output_format_accepted_for_pro() -> None:
    assert validate_output_format("png", PRO) == "png"


def test_output_format_accepted_for_lite() -> None:
    assert validate_output_format("jpeg", LITE) == "jpeg"


def test_output_format_rejected_for_45() -> None:
    with pytest.raises(SeedreamValidationError, match="5.0 系列"):
        validate_output_format("png", MODEL_45)


def test_output_format_rejected_for_40() -> None:
    with pytest.raises(SeedreamValidationError, match="5.0 系列"):
        validate_output_format("png", MODEL_40)


# ==================== 参考图上限 ====================


def test_max_reference_images_pro_is_10() -> None:
    assert get_max_reference_images(PRO) == 10


def test_max_reference_images_lite_is_14() -> None:
    assert get_max_reference_images(LITE) == 14


def test_max_reference_images_45_is_14() -> None:
    assert get_max_reference_images(MODEL_45) == 14


def test_max_reference_images_40_is_14() -> None:
    assert get_max_reference_images(MODEL_40) == 14


# ==================== 提示词优化模式：Pro/Lite/4.5 仅 standard ====================


def test_optimize_fast_rejected_for_pro() -> None:
    with pytest.raises(SeedreamValidationError, match="standard"):
        validate_optimize_prompt_options({"mode": "fast"}, PRO)


def test_optimize_fast_rejected_for_lite() -> None:
    with pytest.raises(SeedreamValidationError, match="standard"):
        validate_optimize_prompt_options({"mode": "fast"}, LITE)


def test_optimize_fast_accepted_for_40() -> None:
    assert validate_optimize_prompt_options({"mode": "fast"}, MODEL_40) == {"mode": "fast"}


def test_optimize_standard_accepted_for_pro() -> None:
    assert validate_optimize_prompt_options({"mode": "standard"}, PRO) == {"mode": "standard"}


# ==================== 流式输出：5.0 Pro 不支持 ====================


def test_stream_disabled_ok_for_pro() -> None:
    assert validate_stream(False, PRO) is False


def test_stream_enabled_rejected_for_pro() -> None:
    with pytest.raises(SeedreamValidationError, match="5.0-pro 不支持流式输出"):
        validate_stream(True, PRO)


def test_stream_enabled_ok_for_lite() -> None:
    assert validate_stream(True, LITE) is True


# ==================== Endpoint ID：无法识别模型时放行（由 API 校验） ====================


def test_output_format_accepted_for_endpoint_id() -> None:
    # Endpoint ID 无法识别模型，放行由 API 校验（与 stream/size/optimize 策略一致）
    assert validate_output_format("png", "ep-20241001-abcde") == "png"


def test_tools_accepted_for_endpoint_id() -> None:
    assert validate_generation_tools([{"type": "web_search"}], "ep-20241001-abcde") == [
        {"type": "web_search"}
    ]
