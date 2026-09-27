"""get_model_info 工具契约：能力快照载荷、文本摘要与端到端调用。"""

from __future__ import annotations

from dataclasses import fields

from mcp.types import CallToolResult, TextContent

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.resources import mcp
from seedream_mcp.tools.core.model_info import (
    _CAPABILITY_LABELS,
    build_model_info_payload,
    build_model_info_text,
)
from seedream_mcp.utils.core.validators import VALID_OPTIMIZE_MODES
from seedream_mcp.utils.model.model_capabilities import ModelCapabilities

# 能力表字段到快照键的映射全集，映射完整性断言的数据源。
_CAPS_FIELD_TO_PAYLOAD_KEY = {
    "supports_fast_optimize_prompt": "optimize_prompt_modes",
    "max_reference_images": "max_reference_images",
    "supports_layer_decomposition": "layer_decomposition",
    "supports_background": "transparent_background",
    "allowed_presets": "size_presets",
    "min_size_pixels": "min_size_pixels",
    "max_size_pixels": "max_size_pixels",
    "supports_output_format": "output_format",
    "supports_stream": "stream",
    "supports_tools": "web_search",
    "supports_sequential_generation": "sequential_generation",
}

# 能力开关字段全集，未识别模型的全放行兜底断言遍历此集。
_CAPABILITY_SWITCH_KEYS = (
    "output_format",
    "stream",
    "web_search",
    "sequential_generation",
    "layer_decomposition",
    "transparent_background",
)


def test_payload_for_default_model() -> None:
    """默认 5.0 模型的快照：家族、同 ID 双别名与能力字段取值。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))

    assert payload["model_id"] == "doubao-seedream-5-0-260128"
    assert payload["family"] == "5.0-lite"
    assert payload["display_name"] == "doubao-seedream-5.0"
    assert payload["aliases"] == ["doubao-seedream-5.0", "doubao-seedream-5.0-lite"]

    caps = payload["capabilities"]
    assert caps["size_presets"] == ["2K", "3K", "4K"]
    assert caps["min_size_pixels"] == 2560 * 1440
    assert caps["max_size_pixels"] == 4096 * 4096
    assert caps["max_reference_images"] == 14
    assert caps["output_format"] is True
    assert caps["stream"] is True
    assert caps["web_search"] is True
    assert caps["sequential_generation"] is True
    assert caps["layer_decomposition"] is False
    assert caps["transparent_background"] is False
    assert caps["optimize_prompt_modes"] == ["standard"]


def test_capability_mapping_covers_capability_table() -> None:
    """能力字段映射覆盖能力表全部能力字段，新增字段漏配映射即失败。

    快照键为手工映射（含 transparent_background/web_search 等语义化重命名），
    asdict 派生做不到重命名。
    """
    caps_fields = {f.name for f in fields(ModelCapabilities)} - {"family", "display_name"}
    assert set(_CAPS_FIELD_TO_PAYLOAD_KEY) == caps_fields

    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    assert set(payload["capabilities"]) == set(_CAPS_FIELD_TO_PAYLOAD_KEY.values())


def test_capability_table_field_order_matches_snapshot_mapping() -> None:
    """能力表字段序与快照映射序一致，数据类重排使字段序声称失真即失败。"""
    table_order = [f.name for f in fields(ModelCapabilities)][2:]
    assert table_order == list(_CAPS_FIELD_TO_PAYLOAD_KEY)


def test_capability_labels_cover_switch_keys() -> None:
    """文本标签表覆盖全部布尔开关键，新增开关漏配标签即文本通道静默丢失。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    switch_keys = {key for key, value in payload["capabilities"].items() if isinstance(value, bool)}
    assert {key for _, key in _CAPABILITY_LABELS} == switch_keys


def test_optimize_prompt_modes_match_validator_vocabulary() -> None:
    """优化档位清单与校验白名单对账，白名单扩展漏更快照即失败。

    5.0 Pro 声明支持全部档位，其快照返回白名单全集。
    """
    payload = build_model_info_payload(
        SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro")
    )
    assert set(payload["capabilities"]["optimize_prompt_modes"]) == VALID_OPTIMIZE_MODES


def test_payload_field_order_locked() -> None:
    """快照字段顺序锁定：身份字段连续，能力字段按调用流程排序。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))

    assert list(payload) == ["model_id", "display_name", "aliases", "family", "capabilities"]
    assert list(payload["capabilities"]) == [
        "optimize_prompt_modes",
        "max_reference_images",
        "layer_decomposition",
        "transparent_background",
        "size_presets",
        "min_size_pixels",
        "max_size_pixels",
        "output_format",
        "stream",
        "web_search",
        "sequential_generation",
    ]


def test_payload_for_pro_model() -> None:
    """5.0 Pro 快照：组图、联网、流式关闭，图层拆分、透明背景、fast 档开启。"""
    payload = build_model_info_payload(
        SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro")
    )

    caps = payload["capabilities"]
    assert caps["max_reference_images"] == 10
    assert caps["stream"] is False
    assert caps["web_search"] is False
    assert caps["sequential_generation"] is False
    assert caps["layer_decomposition"] is True
    assert caps["transparent_background"] is True
    assert caps["optimize_prompt_modes"] == ["standard", "fast"]


def test_payload_for_unrecognized_model_falls_back_to_allow_all() -> None:
    """未识别模型的快照声明兜底语义：family 为 unknown、display_name 缺省、全放行。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k", model_id="ep-20260101-test"))

    assert payload["family"] == "unknown"
    assert payload["display_name"] is None
    assert payload["aliases"] == []

    caps = payload["capabilities"]
    assert caps["size_presets"] == ["1K", "1.5K", "2K", "3K", "4K"]
    assert caps["min_size_pixels"] is None
    assert caps["max_size_pixels"] is None
    assert all(caps[key] is True for key in _CAPABILITY_SWITCH_KEYS)


def test_text_summary_renders_capability_lines() -> None:
    """文本摘要含模型标识、档位、像素区间与支持/不支持两行。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    text = build_model_info_text(payload)

    assert "当前模型：doubao-seedream-5.0（doubao-seedream-5-0-260128）" in text
    assert "别名：doubao-seedream-5.0、doubao-seedream-5.0-lite" in text
    assert "尺寸档位：2K/3K/4K；像素总量 3686400-16777216" in text
    assert "参考图上限：14 张" in text
    assert "提示词优化：standard" in text
    assert "不支持：图层拆分（layer_decomposition）、透明背景（background）" in text


def test_text_summary_for_unrecognized_model_declares_fallback() -> None:
    """未识别模型的文本摘要声明全放行兜底，不支持行为空。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k", model_id="ep-20260101-test"))
    text = build_model_info_text(payload)

    assert "当前模型：ep-20260101-test（未识别家族，能力按全放行兜底）" in text
    assert "不支持：无" in text


async def test_tool_call_returns_structured_snapshot(
    reset_lifespan_singletons: None,
) -> None:
    """端到端调用返回结构化快照与文本摘要，工具不报错。

    无请求上下文时配置取全局活动配置，fixture 注入默认 5.0 模型。
    """
    del reset_lifespan_singletons
    result = await mcp.call_tool("get_model_info", {})
    assert isinstance(result, CallToolResult)

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None
    assert structured["tool"] == "get_model_info"
    assert structured["success"] is True
    assert structured["model_id"] == "doubao-seedream-5-0-260128"
    assert structured["capabilities"]["size_presets"] == ["2K", "3K", "4K"]
    first_block = result.content[0]
    assert isinstance(first_block, TextContent)
    assert first_block.text.startswith("当前模型：")
