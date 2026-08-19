"""Seedream 5.0 Pro 能力差异校验。

5.0 Pro 的 Model ID（doubao-seedream-5-0-pro-*）包含 "doubao-seedream-5-0" 子串，
历史上会被误判为 5.0 Lite。本模块回归其与 5.0 Lite 在工具、输出格式、参考图上限、
提示词优化模式上的差异。
"""

from __future__ import annotations

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.core.validators import (
    validate_background,
    validate_common_generation_params,
    validate_generation_tools,
    validate_layer_decomposition,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_size_for_model,
    validate_stream,
)
from seedream_mcp.utils.model.model_capabilities import (
    get_max_reference_images,
    get_model_capabilities,
)

PRO = "doubao-seedream-5-0-pro-260628"
LITE = "doubao-seedream-5-0-260128"
MODEL_45 = "doubao-seedream-4-5-251128"
MODEL_40 = "doubao-seedream-4-0-250828"


# ==================== 模型识别根因回归 ====================


def test_pro_model_detected_as_pro() -> None:
    assert get_model_capabilities(PRO).max_reference_images == 10


def test_lite_model_not_detected_as_pro() -> None:
    assert get_model_capabilities(LITE).max_reference_images == 14


def test_pro_alias_detected_as_pro() -> None:
    assert get_model_capabilities("doubao-seedream-5.0-pro").max_reference_images == 10


# ==================== tools 联网搜索仅 5.0 Lite 支持 ====================


def test_tools_accepted_for_lite() -> None:
    assert validate_generation_tools([{"type": "web_search"}], LITE) == [{"type": "web_search"}]


def test_tools_rejected_for_pro() -> None:
    with pytest.raises(SeedreamValidationError, match="不支持联网搜索"):
        validate_generation_tools([{"type": "web_search"}], PRO)


def test_tools_rejected_for_45() -> None:
    with pytest.raises(SeedreamValidationError, match="不支持联网搜索"):
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


# ==================== 提示词优化模式：Lite/4.5 仅 standard，Pro/4.0 支持 fast ====================


def test_optimize_fast_accepted_for_pro() -> None:
    assert validate_optimize_prompt_options({"mode": "fast"}, PRO) == {"mode": "fast"}


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


def test_stream_non_bool_rejected_for_supporting_model() -> None:
    """非布尔的 stream 在支持流式的模型下同样于参数级拒绝，不透传上游。"""
    with pytest.raises(SeedreamValidationError, match="stream 必须为布尔值"):
        validate_stream("true", LITE)


# ==================== Endpoint ID 无法识别模型时由 API 校验放行 ====================


def test_output_format_accepted_for_endpoint_id() -> None:
    # Endpoint ID 无法识别模型，放行交由 API 校验，与 stream/size/optimize 策略一致
    assert validate_output_format("png", "ep-20241001-abcde") == "png"


def test_tools_accepted_for_endpoint_id() -> None:
    assert validate_generation_tools([{"type": "web_search"}], "ep-20241001-abcde") == [
        {"type": "web_search"}
    ]


# ==================== 组图生成：5.0 Pro 不支持 ====================


def test_supports_sequential_generation_false_for_pro() -> None:
    """Pro 的能力声明须关闭组图支持，驱动 client 层拒绝组图调用。"""
    assert get_model_capabilities(PRO).supports_sequential_generation is False
    assert get_model_capabilities(LITE).supports_sequential_generation is True


# ==================== 图层拆分：仅 5.0 Pro 支持 ====================


def test_layer_decomposition_accepted_for_pro() -> None:
    assert validate_layer_decomposition(True, PRO) is True


def test_layer_decomposition_rejected_for_lite() -> None:
    with pytest.raises(SeedreamValidationError, match="不支持 layer_decomposition"):
        validate_layer_decomposition(True, LITE)


def test_layer_decomposition_none_defaults_false() -> None:
    assert validate_layer_decomposition(None, LITE) is False


def test_layer_decomposition_rejects_non_bool() -> None:
    with pytest.raises(SeedreamValidationError, match="布尔值"):
        validate_layer_decomposition("true", PRO)


def test_size_auto_accepted_for_pro_with_layer_decomposition() -> None:
    assert validate_size_for_model("auto", PRO, layer_decomposition=True) == "auto"


def test_size_auto_rejected_without_layer_decomposition() -> None:
    with pytest.raises(SeedreamValidationError):
        validate_size_for_model("auto", PRO)


def test_size_auto_rejected_for_lite_even_with_layer_decomposition() -> None:
    # Lite 不支持图层拆分，auto 在门控层即被拒绝
    with pytest.raises(SeedreamValidationError, match="不支持图层拆分"):
        validate_size_for_model("auto", LITE, layer_decomposition=True)


def test_size_pixel_value_rejected_in_layer_decomposition_scenario() -> None:
    # 官方图层拆分场景仅支持分辨率档位方式，宽高像素值直接拒绝
    with pytest.raises(SeedreamValidationError, match="仅支持分辨率档位"):
        validate_size_for_model("2048x2048", PRO, layer_decomposition=True)


def test_layer_preset_message_derives_from_model_capabilities() -> None:
    """图层拒绝文案的档位清单从能力声明派生，1K 按数值序排在 1.5K 之前。

    Pro 拒绝消息仅含其能力档位 1K/1.5K/2K；未知家族 supports_layer_decomposition
    为真且档位为全集，同一场景拒绝消息须含 3K/4K，与数据驱动的放行判定一致。
    """
    with pytest.raises(SeedreamValidationError, match=r"仅支持分辨率档位（1K/1\.5K/2K）或 auto"):
        validate_size_for_model("2048x2048", PRO, layer_decomposition=True)

    with pytest.raises(
        SeedreamValidationError, match=r"仅支持分辨率档位（1K/1\.5K/2K/3K/4K）或 auto"
    ):
        validate_size_for_model("2048x2048", "ep-20241001-abcde", layer_decomposition=True)


def test_common_params_prompt_none_accepted_with_layer_decomposition() -> None:
    validated = validate_common_generation_params(
        prompt=None,
        optimize_prompt_options=None,
        size="auto",
        watermark=False,
        response_format="url",
        output_format=None,
        stream=False,
        tools=None,
        model_id=PRO,
        layer_decomposition=True,
    )

    assert validated.prompt is None


def test_common_params_prompt_none_rejected_without_layer_decomposition() -> None:
    with pytest.raises(SeedreamValidationError, match="prompt 不能为空"):
        validate_common_generation_params(
            prompt=None,
            optimize_prompt_options=None,
            size="2K",
            watermark=False,
            response_format="url",
            output_format=None,
            stream=False,
            tools=None,
            model_id=PRO,
        )


# ==================== 透明通道 background：仅 5.0 Pro 支持 ====================


def test_background_transparent_accepted_for_pro() -> None:
    assert validate_background("transparent", PRO) == "transparent"


def test_background_opaque_accepted_for_pro() -> None:
    assert validate_background("opaque", PRO) == "opaque"


def test_background_rejected_for_lite() -> None:
    with pytest.raises(SeedreamValidationError, match="不支持 background"):
        validate_background("transparent", LITE)


def test_background_rejects_invalid_value() -> None:
    with pytest.raises(SeedreamValidationError, match="background 必须为"):
        validate_background("alpha", PRO)


def test_background_none_returns_none() -> None:
    assert validate_background(None, LITE) is None


def test_background_transparent_rejects_jpeg_output_format() -> None:
    # 官方语义：透明背景输出为 png，与 output_format=jpeg 互斥
    with pytest.raises(SeedreamValidationError, match="互斥"):
        validate_background("transparent", PRO, output_format="jpeg")


def test_background_transparent_allows_png_output_format() -> None:
    assert validate_background("transparent", PRO, output_format="png") == "transparent"


def test_background_transparent_allows_unspecified_output_format() -> None:
    assert validate_background("transparent", PRO, output_format=None) == "transparent"


async def test_pro_model_rejects_sequential_generation_call() -> None:
    """Pro 模型调用组图生成须在能力判定处被拒绝，不进入参数校验与 API 调用。"""
    client = SeedreamClient(SeedreamConfig(api_key="k", model_id=PRO, max_retries=1))
    with pytest.raises(SeedreamValidationError, match="不支持组图"):
        await client.sequential_generation(prompt="t", max_images=3, size="2K")
