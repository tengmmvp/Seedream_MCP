"""生成工具 Input schema 字段顺序与 MCP 平铺 inputSchema 等价性守护。

锁定两层契约：schemas 输入模型的字段顺序（prompt 居首），以及平铺工具签名生成
的 inputSchema 与模型 schema 在字段顺序、required、逐字段定义上的等价性，任何
一侧改动未同步即失败。另锁定封闭性：顶层 additionalProperties 为 false、拼错
参数在运行时被 ToolError 拒绝。等价性延伸到描述维度：逐字段 description 非空
且镜像模型字段描述，嵌套 $defs 全等，描述文案的数值区间与默认值同实际约束一致。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError
from pydantic.fields import FieldInfo

from seedream_mcp.resources import mcp
from seedream_mcp.tools.core.schemas import (
    BrowseImagesInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.utils.model.model_capabilities import SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES

# MCP 注册工具名到输入模型的映射，平铺 inputSchema 等价性断言的数据源。
_TOOL_INPUT_MODELS = {
    "text_to_image": TextToImageInput,
    "image_to_image": ImageToImageInput,
    "multi_image_fusion": MultiImageFusionInput,
    "sequential_generation": SequentialGenerationInput,
    "browse_images": BrowseImagesInput,
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
        "layer_decomposition",
        "background",
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


def test_browse_images_parameter_order() -> None:
    assert list(BrowseImagesInput.model_fields.keys()) == [
        "directory",
        "recursive",
        "max_depth",
        "limit",
        "offset",
        "format_filter",
        "show_details",
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
        schema = by_name[name].input_schema
        assert list(schema["properties"]) == list(model.model_fields), name
        assert "params" not in schema["properties"], name
        if name != "browse_images":
            assert list(schema["properties"])[0] == "prompt", name


async def test_flat_input_schema_required_matches_model_fields() -> None:
    """required 列表与模型中无默认值的字段全等且顺序一致。"""
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].input_schema
        expected = [field for field, info in model.model_fields.items() if info.is_required()]
        assert schema.get("required", []) == expected, name


# 平铺签名非空语义镜像的字段清单：模型层经 str_strip_whitespace 加 min_length 或
# 非空校验器拒绝纯空白输入，平铺参数模型不含 strip 配置，等价约束以 pattern 表达。
# 清单与 server._NON_BLANK_PATTERN 的应用范围一一对应，新增非空语义字段未同步
# 镜像时，下方等价断言因缺 pattern 转红。
_NON_BLANK_MIRROR_FIELDS = {
    ("text_to_image", "prompt"),
    ("text_to_image", "save_path"),
    ("text_to_image", "custom_name"),
    ("image_to_image", "prompt"),
    ("image_to_image", "image"),
    ("image_to_image", "save_path"),
    ("image_to_image", "custom_name"),
    ("multi_image_fusion", "prompt"),
    ("multi_image_fusion", "image"),
    ("multi_image_fusion", "save_path"),
    ("multi_image_fusion", "custom_name"),
    ("sequential_generation", "prompt"),
    ("sequential_generation", "image"),
    ("sequential_generation", "save_path"),
    ("sequential_generation", "custom_name"),
    ("browse_images", "directory"),
}

# 非空语义镜像的 pattern 取值，与 server._NON_BLANK_PATTERN 保持一致。
_NON_BLANK_PATTERN = r"\S"


def _strip_pattern_keys(node: Any) -> Any:
    """递归剔除 schema 节点中的 pattern 键，供平铺与模型 schema 的等价比对。"""
    if isinstance(node, dict):
        return {key: _strip_pattern_keys(value) for key, value in node.items() if key != "pattern"}
    if isinstance(node, list):
        return [_strip_pattern_keys(item) for item in node]
    return node


def _contains_pattern(node: Any, pattern: str) -> bool:
    """递归判定 schema 节点中是否声明了指定 pattern 约束。"""
    if isinstance(node, dict):
        if node.get("pattern") == pattern:
            return True
        return any(_contains_pattern(value, pattern) for value in node.values())
    if isinstance(node, list):
        return any(_contains_pattern(item, pattern) for item in node)
    return False


async def test_flat_input_schema_field_definitions_match_model() -> None:
    """逐字段 schema 定义与模型 json schema 全等，锁定描述与约束不漂移。

    两类刻意差异：max_images 平铺侧声明为 int | None 以区分未提供与显式传入，
    仅比对整数分支；非空语义字段以 pattern 镜像，比对时剔除 pattern 键另行断言。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].input_schema
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
                continue
            if (name, field) in _NON_BLANK_MIRROR_FIELDS:
                assert _contains_pattern(tool_prop, _NON_BLANK_PATTERN), (name, field)
                assert _strip_pattern_keys(tool_prop) == _strip_pattern_keys(model_prop), (
                    name,
                    field,
                )
            else:
                assert not _contains_pattern(tool_prop, _NON_BLANK_PATTERN), (name, field)
                assert tool_prop == model_prop, (name, field)


# ==================== 参数 description 镜像与文案约束一致性 ====================

# 描述文案中的数字区间形态，如「1-15」，用于比对字段实际约束的闭区间边界。
_DESCRIPTION_RANGE_PATTERN = re.compile(r"(\d+)\s*-\s*(\d+)")
# 描述文案中的默认值数字形态，如「默认 0」，用于比对字段声明的默认值。
_DESCRIPTION_DEFAULT_PATTERN = re.compile(r"默认\s*(\d+)")


def _field_bounds(info: FieldInfo) -> tuple[int | None, int | None]:
    """提取字段约束的闭区间边界，数值边界与长度边界同权，缺该方向约束时为 None。"""
    lower: int | None = None
    upper: int | None = None
    for constraint in info.metadata:
        ge = getattr(constraint, "ge", None)
        min_length = getattr(constraint, "min_length", None)
        le = getattr(constraint, "le", None)
        max_length = getattr(constraint, "max_length", None)
        if isinstance(ge, int):
            lower = ge
        elif isinstance(min_length, int):
            lower = min_length
        if isinstance(le, int):
            upper = le
        elif isinstance(max_length, int):
            upper = max_length
    return lower, upper


async def test_flat_input_schema_field_descriptions_mirror_model() -> None:
    """每个平铺参数的 inputSchema description 非空且与模型字段描述全等。

    MCPServer 不解析工具 docstring 的参数说明，参数 description 唯一来源是平铺
    签名的 Field 默认值，工具 docstring 仅承担工具级描述。逐字段等价断言在两侧
    同时缺失 description 时会空过，本断言补上非空要求。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].input_schema
        for field, info in model.model_fields.items():
            description = schema["properties"][field].get("description")
            assert isinstance(description, str) and description.strip(), (name, field)
            assert description == info.description, (name, field)


async def test_flat_input_schema_nested_definitions_match_model() -> None:
    """inputSchema 的嵌套定义与模型 schema 全等，锁定嵌套模型描述与枚举成员。

    逐字段断言只比对顶层属性，嵌套结构位于 $defs，须单独锁定。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].input_schema
        assert schema.get("$defs", {}) == model.model_json_schema().get("$defs", {}), name


async def test_flat_schema_description_tokens_match_model_constraints() -> None:
    """inputSchema 描述文案中的数值区间与默认值必须与模型字段实际约束一致。

    描述镜像只保证两侧文案相同；约束调整后残留硬编码数值的文案须由本断言发现。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].input_schema
        for field, info in model.model_fields.items():
            description = schema["properties"][field].get("description")
            assert isinstance(description, str), (name, field)
            lower, upper = _field_bounds(info)
            for match in _DESCRIPTION_RANGE_PATTERN.finditer(description):
                assert (int(match.group(1)), int(match.group(2))) == (lower, upper), (
                    name,
                    field,
                    match.group(0),
                )
            for match in _DESCRIPTION_DEFAULT_PATTERN.finditer(description):
                assert int(match.group(1)) == info.default, (name, field, match.group(0))


# ==================== 平铺 inputSchema 封闭性 ====================


async def test_flat_input_schema_forbids_additional_properties() -> None:
    """五工具 inputSchema 顶层 additionalProperties 恒为 False，声明平铺字段封闭集合。

    平铺签名使 MCPServer 生成的 schema 不再继承输入模型的 extra=forbid 声明；
    server 注册期的 _tighten_flat_tool_schemas 负责补偿，本断言锁定补偿不缺失。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name in _TOOL_INPUT_MODELS:
        schema = by_name[name].input_schema
        assert schema.get("additionalProperties") is False, name


@pytest.mark.parametrize(
    ("tool_name", "typo_args"),
    [
        ("text_to_image", {"prompt": "a cat", "watermarkss": True}),
        ("text_to_image", {"prompt": "a cat", "sze": "2K"}),
        (
            "image_to_image",
            {"prompt": "a cat", "image": "https://example.com/cat.png", "sze": "2K"},
        ),
        (
            "multi_image_fusion",
            {
                "prompt": "a cat",
                "image": ["https://example.com/cat.png"],
                "responce_format": "url",
            },
        ),
        ("sequential_generation", {"prompt": "a cat", "max_imagess": 4}),
        ("browse_images", {"directory": ".", "recursve": True}),
    ],
)
async def test_flat_tool_rejects_unknown_parameter_names(tool_name: str, typo_args: dict) -> None:
    """五工具的平铺签名在运行时拒绝拼错参数名，不被静默丢弃。

    平铺参数模型默认忽略未知键，server 注册期替换为 extra=forbid 子类补偿，服务端
    与 inputSchema 本地校验同样拒绝。其余参数均取合法值，确保报错仅源于未知键。
    """
    typo_key = next(
        key for key in typo_args if key not in _TOOL_INPUT_MODELS[tool_name].model_fields
    )
    with pytest.raises(ToolError, match=typo_key):
        await mcp.call_tool(tool_name, typo_args)


@pytest.mark.parametrize(
    ("tool_name", "blank_args"),
    [
        ("text_to_image", {"prompt": "   "}),
        ("text_to_image", {"prompt": "a cat", "save_path": "   "}),
        ("text_to_image", {"prompt": "a cat", "custom_name": ""}),
        ("image_to_image", {"prompt": "a cat", "image": "   "}),
        (
            "multi_image_fusion",
            {"prompt": "a cat", "image": ["https://example.com/a.png", "   "]},
        ),
        ("sequential_generation", {"prompt": "a cat", "image": "   "}),
        ("sequential_generation", {"prompt": "a cat", "image": ["   "]}),
        ("browse_images", {"directory": "   "}),
    ],
)
async def test_flat_tool_rejects_blank_string_inputs(tool_name: str, blank_args: dict) -> None:
    """纯空白字符串在平铺签名层被拒，不进入工具体后才失败。

    模型层经 strip 加非空校验拒绝，平铺参数模型不含 strip 配置，server 以 pattern
    镜像该语义在协议层拒绝。其余参数均取合法值，确保报错仅源于空白输入。
    """
    with pytest.raises(ToolError):
        await mcp.call_tool(tool_name, blank_args)


# ==================== 组图参考图列表上限的 schema 层声明 ====================


async def test_sequential_image_list_branch_declares_max_items() -> None:
    """组图 image 列表分支的 inputSchema 声明 maxItems 14，与多图融合口径一致。

    镜像等价断言在两侧同时丢失声明时仍相等，本断言独立锁定声明存在，客户端本地
    校验才能拒绝超量输入。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    schema = by_name["sequential_generation"].input_schema
    array_branch = next(
        branch for branch in schema["properties"]["image"]["anyOf"] if branch.get("type") == "array"
    )
    assert array_branch["maxItems"] == SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES


async def test_sequential_tool_rejects_oversized_image_list() -> None:
    """组图参考图列表超过 14 张在 inputSchema 校验层即被拒绝，不进入工具体。

    报错须源自平铺签名列表分支的 max_length 约束而非模型 before-validator 的
    数量文案，据此锁定拒绝发生在 SDK 参数模型校验层。
    """
    images = [f"https://example.com/{i}.png" for i in range(15)]
    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool("sequential_generation", {"prompt": "a cat", "image": images})
    message = str(exc_info.value)
    assert "参考图片数量" not in message
    assert "14" in message


# ==================== 模型工具列表条目上限 ====================


async def test_generation_tool_rejects_oversized_tools_list() -> None:
    """tools 含 9 项条目被校验拒绝，平铺签名与输入模型两侧各自独立锁定。

    合法调用至多 1 个 web_search 条目，上限 8 仅拦截异常超量输入。等价性断言
    只校验两侧同步，两侧同时丢失上限时仍相等，故分别经工具调用与模型构造两路
    拒绝。
    """
    entries = [{"type": "web_search"}] * 9
    with pytest.raises(ToolError):
        await mcp.call_tool("text_to_image", {"prompt": "a cat", "tools": entries})
    with pytest.raises(ValidationError):
        TextToImageInput(prompt="a cat", tools=entries)
