"""生成工具 Input schema 字段顺序与 MCP 平铺 inputSchema 等价性守护。

锁定两层契约：schemas 输入模型的字段顺序（prompt 居首），以及 server 平铺工具
签名生成的 inputSchema 与模型 schema 的等价性（字段顺序、required、逐字段定义）。
平铺字段的名称、类型、默认值、约束与描述镜像自输入模型，任何一侧改动未同步时
本文件失败即暴露漂移。另锁定平铺 schema 的封闭性：顶层 additionalProperties 为
false 且拼错参数在运行时被 ToolError 拒绝，恢复输入模型 extra=forbid 的
「被拒自纠」语义。
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from seedream_mcp.resources import mcp
from seedream_mcp.tools.core.schemas import (
    BrowseImagesInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)

# MCP 注册工具名到输入模型的映射，平铺 inputSchema 等价性断言的数据源。
_TOOL_INPUT_MODELS = {
    "seedream_text_to_image": TextToImageInput,
    "seedream_image_to_image": ImageToImageInput,
    "seedream_multi_image_fusion": MultiImageFusionInput,
    "seedream_sequential_generation": SequentialGenerationInput,
    "seedream_browse_images": BrowseImagesInput,
}


def test_text_to_image_parameter_order() -> None:
    assert list(TextToImageInput.model_fields.keys()) == [
        "prompt",
        "optimize_prompt_options",
        "size",
        "watermark",
        "response_format",
        "output_format",
        "stream",
        "tools",
        "request_count",
        "parallelism",
        "auto_save",
        "save_path",
        "custom_name",
    ]


def test_image_to_image_parameter_order() -> None:
    assert list(ImageToImageInput.model_fields.keys()) == [
        "prompt",
        "optimize_prompt_options",
        "image",
        "size",
        "watermark",
        "response_format",
        "output_format",
        "stream",
        "tools",
        "request_count",
        "parallelism",
        "auto_save",
        "save_path",
        "custom_name",
    ]


def test_multi_image_fusion_parameter_order() -> None:
    assert list(MultiImageFusionInput.model_fields.keys()) == [
        "prompt",
        "optimize_prompt_options",
        "image",
        "size",
        "watermark",
        "response_format",
        "output_format",
        "stream",
        "tools",
        "request_count",
        "parallelism",
        "auto_save",
        "save_path",
        "custom_name",
    ]


def test_sequential_generation_parameter_order() -> None:
    assert list(SequentialGenerationInput.model_fields.keys()) == [
        "prompt",
        "optimize_prompt_options",
        "image",
        "size",
        "watermark",
        "max_images",
        "response_format",
        "output_format",
        "stream",
        "tools",
        "request_count",
        "parallelism",
        "auto_save",
        "save_path",
        "custom_name",
    ]


# ==================== 平铺 inputSchema 与模型 schema 等价性 ====================


async def test_flat_input_schema_property_order_matches_model_fields() -> None:
    """五工具平铺 inputSchema 顶层字段名与顺序和模型字段全等，prompt 居首。

    同时锁定平铺形态本身：不得退回单一 params 嵌套对象字段，否则客户端以平铺
    键名调用会被 required params 校验拒绝。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].inputSchema
        assert list(schema["properties"]) == list(model.model_fields), name
        assert "params" not in schema["properties"], name
        if name != "seedream_browse_images":
            assert list(schema["properties"])[0] == "prompt", name


async def test_flat_input_schema_required_matches_model_fields() -> None:
    """required 列表与模型中无默认值的字段全等且顺序一致。"""
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].inputSchema
        expected = [field for field, info in model.model_fields.items() if info.is_required()]
        assert schema.get("required", []) == expected, name


async def test_flat_input_schema_field_definitions_match_model() -> None:
    """逐字段 schema 定义与模型 json schema 全等，锁定描述与约束不漂移。

    唯一刻意差异是组图的 max_images：平铺侧声明为 int | None，None 表示未提供，
    组图据此区分「未提供时自动推导」与「显式传入」；该字段仅比对整数分支的
    约束与描述。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].inputSchema
        model_schema = model.model_json_schema()
        for field in model.model_fields:
            tool_prop = schema["properties"][field]
            model_prop = model_schema["properties"][field]
            if field == "max_images":
                int_branch = next(
                    branch for branch in tool_prop["anyOf"] if branch.get("type") == "integer"
                )
                assert int_branch["minimum"] == model_prop["minimum"], name
                assert int_branch["maximum"] == model_prop["maximum"], name
                assert tool_prop["description"] == model_prop["description"], name
            else:
                assert tool_prop == model_prop, (name, field)


# ==================== 平铺 inputSchema 封闭性 ====================


async def test_flat_input_schema_forbids_additional_properties() -> None:
    """五工具 inputSchema 顶层 additionalProperties 恒为 False，声明平铺字段封闭集合。

    平铺签名使 FastMCP 生成的 schema 不再继承输入模型的 extra=forbid 声明；
    server 注册期的 _tighten_flat_tool_schemas 负责补偿，本断言锁定补偿不缺失。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name in _TOOL_INPUT_MODELS:
        schema = by_name[name].inputSchema
        assert schema.get("additionalProperties") is False, name


@pytest.mark.parametrize(
    "typo_args",
    [
        {"prompt": "a cat", "watermarkss": True},
        {"prompt": "a cat", "sze": "2K"},
    ],
)
async def test_flat_tool_rejects_unknown_parameter_names(typo_args: dict) -> None:
    """拼错参数名的调用被 ToolError 拒绝，不被静默丢弃。

    输入模型以 extra=forbid 拒绝未知键，嵌套 params 形态下拼错参数在模型校验即
    失败；平铺签名经 FastMCP 参数模型默认忽略未知键，server 注册期把参数模型替换
    为 extra=forbid 子类补偿。客户端可据 inputSchema 在本地拒绝，服务端运行时
    同样拒绝，模型收到明确报错后可自行纠正参数名。
    """
    with pytest.raises(ToolError, match="watermarkss|sze"):
        await mcp.call_tool("seedream_text_to_image", typo_args)
