"""get_model_info 工具契约：能力快照载荷、文本摘要与端到端调用。"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.server.runtime import mcp
from seedream_mcp.tools.handlers.get_model_info import (
    _VALUE_FIELD_LABELS,
    build_model_info_payload,
    build_model_info_text,
)
from seedream_mcp.utils.core.validators import VALID_OPTIMIZE_MODES, capability_gate_error
from seedream_mcp.utils.model.capabilities import CAPABILITY_LABELS, ModelCapabilities

# 能力表字段到快照键的映射全集，映射完整性断言的数据源；allowed_background_modes
# 一字段派生两键（布尔开关与取值清单），background_forms_by_mode 派生全形态布尔
# 快照键，快照键为手工映射（含 web_search 等语义化重命名），asdict 派生做不到
# 重命名。
_CAPS_FIELD_TO_PAYLOAD_KEYS = {
    "supports_fast_optimize_prompt": ("optimize_prompt_modes",),
    "max_reference_images": ("max_reference_images",),
    "supports_layer_decomposition": ("layer_decomposition",),
    "supports_layer_decomposition_options": ("layer_decomposition_options",),
    "allowed_background_modes": ("transparent_background", "background_modes"),
    "background_forms_by_mode": ("full_form_transparency",),
    "allowed_presets": ("size_presets",),
    "min_size_pixels": ("min_size_pixels",),
    "max_size_pixels": ("max_size_pixels",),
    "supports_output_format": ("output_format",),
    "supports_stream": ("stream",),
    "supports_tools": ("web_search",),
    "supports_sequential_generation": ("sequential_generation",),
    "supports_safety_identifier": ("safety_identifier",),
}

# 供拆分预算推导与图层数上限文案取值、不进能力快照的能力表字段。
_NON_SNAPSHOT_CAPS_FIELDS = frozenset({"max_layer_decomposition_images"})

# 能力开关字段全集，未知模型的全放行兜底断言遍历此集；safety_identifier 因
# 服务器主动附加而从紧，是该兜底的唯一例外。
_CAPABILITY_SWITCH_KEYS = (
    "output_format",
    "stream",
    "web_search",
    "sequential_generation",
    "layer_decomposition",
    "layer_decomposition_options",
    "transparent_background",
    "full_form_transparency",
    "safety_identifier",
)


def test_payload_for_default_model() -> None:
    """默认 5.5 模型的快照：家族、别名与能力字段取值。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))

    assert payload["model_id"] == "doubao-seedream-5-5-261028"
    assert payload["family"] == "5.5"
    assert payload["display_name"] == "doubao-seedream-5.5"
    assert payload["aliases"] == ["doubao-seedream-5.5"]

    caps = payload["capabilities"]
    assert caps["size_presets"] == ["1K", "1.5K", "2K"]
    assert caps["min_size_pixels"] == 1280 * 720
    assert caps["max_size_pixels"] == 4624220
    assert caps["max_reference_images"] == 10
    assert caps["output_format"] is True
    assert caps["stream"] is False
    assert caps["web_search"] is False
    assert caps["sequential_generation"] is False
    assert caps["layer_decomposition"] is True
    assert caps["layer_decomposition_options"] is True
    assert caps["transparent_background"] is True
    assert caps["background_modes"] == ["auto", "opaque", "transparent"]
    assert caps["safety_identifier"] is True
    assert caps["optimize_prompt_modes"] == ["standard"]


def test_capability_mapping_covers_capability_table() -> None:
    """能力字段映射覆盖能力表全部快照字段，新增字段漏配映射即失败。"""
    caps_fields = (
        {f.name for f in fields(ModelCapabilities)}
        - {"family", "display_name"}
        - _NON_SNAPSHOT_CAPS_FIELDS
    )
    assert set(_CAPS_FIELD_TO_PAYLOAD_KEYS) == caps_fields

    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    all_keys = {key for keys in _CAPS_FIELD_TO_PAYLOAD_KEYS.values() for key in keys}
    assert set(payload["capabilities"]) == all_keys


def test_capability_table_field_order_matches_snapshot_mapping() -> None:
    """能力表字段序与快照映射序一致，数据类重排使字段序声称失真即失败。"""
    excluded = {"family", "display_name"} | _NON_SNAPSHOT_CAPS_FIELDS
    table_order = [f.name for f in fields(ModelCapabilities) if f.name not in excluded]
    assert table_order == list(_CAPS_FIELD_TO_PAYLOAD_KEYS)


def test_capability_labels_cover_switch_keys() -> None:
    """标签表自含的载荷键与快照布尔键集一致，新增开关漏配标签即文本通道静默丢失。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    switch_keys = {key for key, value in payload["capabilities"].items() if isinstance(value, bool)}
    assert {entry.payload_key for entry in CAPABILITY_LABELS} == switch_keys


def test_value_field_labels_cover_payload_value_fields() -> None:
    """取值段标签表覆盖全部非布尔能力字段，新增取值字段漏配标签即工具描述列举缺项。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    value_keys = {
        key for key, value in payload["capabilities"].items() if not isinstance(value, bool)
    }
    labeled = {field for _, fields in _VALUE_FIELD_LABELS for field in fields}
    assert labeled == value_keys


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
        "layer_decomposition_options",
        "transparent_background",
        "full_form_transparency",
        "background_modes",
        "size_presets",
        "min_size_pixels",
        "max_size_pixels",
        "output_format",
        "stream",
        "web_search",
        "sequential_generation",
        "safety_identifier",
    ]


def test_payload_for_pro_model() -> None:
    """5.0 Pro 快照：组图、联网、流式、拆分配置关闭，图层拆分、透明背景、fast 档开启。"""
    payload = build_model_info_payload(
        SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro")
    )

    caps = payload["capabilities"]
    assert caps["max_reference_images"] == 10
    assert caps["stream"] is False
    assert caps["web_search"] is False
    assert caps["sequential_generation"] is False
    assert caps["layer_decomposition"] is True
    assert caps["layer_decomposition_options"] is False
    assert caps["transparent_background"] is True
    assert caps["background_modes"] == ["opaque", "transparent"]
    assert caps["safety_identifier"] is True
    assert caps["optimize_prompt_modes"] == ["standard", "fast"]


def test_payload_for_unrecognized_model_falls_back_to_allow_all() -> None:
    """未知模型的快照声明兜底语义：family 为 unknown、display_name 缺省，生成参数能力全放行、终端用户标识例外。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k", model_id="ep-20260101-test"))

    assert payload["family"] == "unknown"
    assert payload["display_name"] is None
    assert payload["aliases"] == []

    caps = payload["capabilities"]
    assert caps["size_presets"] == ["1K", "1.5K", "2K", "3K", "4K"]
    assert caps["min_size_pixels"] is None
    assert caps["max_size_pixels"] is None
    assert all(caps[key] is True for key in _CAPABILITY_SWITCH_KEYS if key != "safety_identifier")
    assert caps["safety_identifier"] is False


def test_text_summary_renders_capability_lines() -> None:
    """文本摘要含模型标识、档位、透明通道取值、像素区间与支持/不支持两行。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    text = build_model_info_text(payload)

    assert "当前模型：doubao-seedream-5.5（doubao-seedream-5-5-261028）" in text
    assert "别名：doubao-seedream-5.5" in text
    assert "尺寸档位：1K/1.5K/2K；像素总量 921600-4624220" in text
    assert "参考图上限：10 张" in text
    assert "提示词优化：standard" in text
    assert "透明通道取值：auto/opaque/transparent" in text
    assert text.index("参考图上限：") < text.index("透明通道取值：") < text.index("尺寸档位：")
    assert (
        "支持：图层拆分（layer_decomposition）、"
        "图层拆分配置与富文本 OCR（layer_decomposition_options）、"
        "透明背景（background）、透明通道全形态（background）、"
        "输出格式（output_format）、"
        "终端用户标识（safety_identifier）"
    ) in text
    assert (
        "不支持：流式输出（stream）、联网搜索（tools）、组图生成（sequential_generation）" in text
    )


# 取值段标签到摘要行首的映射，行首是渲染处的手写文案，不能自标签派生。
_VALUE_LABEL_TO_SUMMARY_LINE_PREFIX = {
    "提示词优化档位": "提示词优化：",
    "参考图上限": "参考图上限：",
    "透明通道取值": "透明通道取值：",
    "尺寸档位与像素区间": "尺寸档位：",
}


def test_text_summary_renders_every_value_field_label() -> None:
    """取值段每个标签在文本摘要中都有对应行，新增标签漏渲染即摘要静默缺行。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    lines = build_model_info_text(payload).splitlines()

    labels = {label for label, _ in _VALUE_FIELD_LABELS}
    assert set(_VALUE_LABEL_TO_SUMMARY_LINE_PREFIX) == labels
    for label, line_prefix in _VALUE_LABEL_TO_SUMMARY_LINE_PREFIX.items():
        assert any(line.startswith(line_prefix) for line in lines), label


def test_text_summary_renders_partial_background_modes() -> None:
    """5.0 Pro 的摘要只列出其实际支持的透明通道取值，auto 不在其中。"""
    payload = build_model_info_payload(
        SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro")
    )

    text = build_model_info_text(payload)

    assert "透明通道取值：opaque/transparent" in text


def test_text_summary_declares_absent_background_modes() -> None:
    """doubao-seedream-5.0 所属的 5.0 Lite 家族不支持透明通道。

    摘要取值行如实显示无，不支持行列出透明背景。
    """
    payload = build_model_info_payload(SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0"))

    caps = payload["capabilities"]
    assert caps["transparent_background"] is False
    assert caps["background_modes"] == []
    text = build_model_info_text(payload)
    assert "透明通道取值：无" in text
    assert (
        "不支持：图层拆分（layer_decomposition）、"
        "图层拆分配置与富文本 OCR（layer_decomposition_options）、"
        "透明背景（background）、透明通道全形态（background）、"
        "终端用户标识（safety_identifier）"
    ) in text


def test_text_summary_for_unrecognized_model_declares_fallback() -> None:
    """未知模型的文本摘要首行声明生成参数能力全放行兜底，终端用户标识列入不支持。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k", model_id="ep-20260101-test"))
    text = build_model_info_text(payload)

    assert "当前模型：ep-20260101-test（未识别家族，生成参数能力按全放行兜底）" in text
    assert "不支持：终端用户标识（safety_identifier）" in text


async def test_tool_call_returns_structured_snapshot(
    reset_lifespan_singletons: None,
) -> None:
    """端到端调用返回结构化快照与文本摘要，工具不报错。

    无请求上下文时配置取全局活动配置，fixture 注入默认 5.5 模型。
    """
    del reset_lifespan_singletons
    result = await mcp.call_tool("get_model_info", {})
    assert isinstance(result, CallToolResult)

    assert not result.is_error
    structured = result.structured_content
    assert structured is not None
    assert structured["tool"] == "get_model_info"
    assert structured["success"] is True
    assert structured["model_id"] == "doubao-seedream-5-5-261028"
    assert structured["capabilities"]["size_presets"] == ["1K", "1.5K", "2K"]
    first_block = result.content[0]
    assert isinstance(first_block, TextContent)
    assert first_block.text.startswith("当前模型：")


async def test_tool_call_returns_structured_error_on_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons: None,
) -> None:
    """载荷组装未预期异常时降级为结构化错误结果，不向调用方抛出。"""
    import seedream_mcp.tools.handlers.get_model_info as impl_module

    def _boom(config: Any) -> dict[str, Any]:
        del config
        raise RuntimeError("payload assembly exploded")

    monkeypatch.setattr(impl_module, "build_model_info_payload", _boom)
    result = await mcp.call_tool("get_model_info", {})
    assert isinstance(result, CallToolResult)

    assert result.is_error
    structured = result.structured_content
    assert structured is not None
    assert structured["tool"] == "get_model_info"
    assert structured["success"] is False
    assert structured["status"] == "failed"
    assert structured["error"]["type"] == "model_info_failed"
    assert "payload assembly exploded" in structured["error"]["message"]
    first_block = result.content[0]
    assert isinstance(first_block, TextContent)
    # 两通道同源：可见文本取结构化 error.message 的净化产物，边界不做二次净化。
    assert first_block.text == structured["error"]["message"]


# ==================== 工具描述派生守护 ====================


# get_model_info 注册描述的逐字定稿。
_MODEL_INFO_DESCRIPTION_LOCKED = (
    "查询当前配置模型的能力快照。\n"
    "\n"
    "返回当前模型 ID、别名与能力字段：提示词优化档位、参考图上限、透明通道取值、尺寸档位与像素区间，\n"
    "以及图层拆分（layer_decomposition）、"
    "图层拆分配置与富文本 OCR（layer_decomposition_options）、"
    "透明背景（background）、透明通道全形态（background）、输出格式（output_format）、流式输出（stream）、"
    "联网搜索（tools）、组图生成（sequential_generation）、"
    "终端用户标识（safety_identifier）的支持情况。\n"
    "生成工具的模型相关参数是否可用以本工具结果为准。不支持时生成工具会报错并附支持该能力的模型清单。"
    "终端用户标识由服务器自动附加，不支持时不附加。\n"
    "模型由服务器配置决定，更换模型请告知用户调整服务器配置。"
)


async def test_get_model_info_description_matches_locked_text() -> None:
    """注册描述与定稿逐字一致，改标签或改骨架未同步审视定稿时本用例失败。"""
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    assert by_name["get_model_info"].description == _MODEL_INFO_DESCRIPTION_LOCKED


async def test_get_model_info_description_enumerations_match_label_tables() -> None:
    """注册描述的两段列举与标签序一致，手写描述绕开派生即失同步。"""
    tools = await mcp.list_tools()
    description = next(tool.description or "" for tool in tools if tool.name == "get_model_info")
    value_fields = "、".join(label for label, _ in _VALUE_FIELD_LABELS)
    capabilities = "、".join(entry.label for entry in CAPABILITY_LABELS)
    assert f"能力字段：{value_fields}，\n" in description
    assert f"以及{capabilities}的支持情况。" in description


def test_label_tables_cover_payload_capability_fields() -> None:
    """两张标签表的字段并集恰等于 payload 能力字段集，新增字段漏配标签即失败。"""
    payload = build_model_info_payload(SeedreamConfig(api_key="k"))
    labeled = {field for _, fields in _VALUE_FIELD_LABELS for field in fields}
    labeled |= {entry.payload_key for entry in CAPABILITY_LABELS}
    assert labeled == set(payload["capabilities"])


def test_capability_labels_shared_by_gate_errors_and_snapshot_text() -> None:
    """能力标签两处取值同源：门控报错与快照文本渲染同一标签表的标签。"""
    model_id = "doubao-seedream-4-5-251128"
    payload = build_model_info_payload(SeedreamConfig(api_key="k", model_id=model_id))
    text = build_model_info_text(payload)
    for entry in CAPABILITY_LABELS:
        assert entry.label in text
        error = capability_gate_error(
            field=entry.param, model_id=model_id, capability=entry.capability
        )
        assert f"不支持{entry.label}" in error.message
