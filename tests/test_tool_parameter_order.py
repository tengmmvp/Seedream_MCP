"""生成工具 Input schema 字段顺序与 MCP 平铺 inputSchema 等价性守护。

锁定两层契约：schemas 输入模型的字段顺序（prompt 居首），以及 server 平铺工具
签名生成的 inputSchema 与模型 schema 的等价性（字段顺序、required、逐字段定义）。
平铺字段的名称、类型、默认值、约束与描述镜像自输入模型，任何一侧改动未同步时
本文件失败即暴露漂移。另锁定平铺 schema 的封闭性：顶层 additionalProperties 为
false 且拼错参数在运行时被 ToolError 拒绝，恢复输入模型 extra=forbid 的
「被拒自纠」语义。等价性延伸到描述维度：逐字段 description 非空且镜像模型字段
描述，嵌套 $defs 定义全等，描述文案陈述的数值区间与默认值同字段实际约束一致。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
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
        if name != "seedream_browse_images":
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
    ("seedream_text_to_image", "prompt"),
    ("seedream_text_to_image", "save_path"),
    ("seedream_text_to_image", "custom_name"),
    ("seedream_image_to_image", "prompt"),
    ("seedream_image_to_image", "image"),
    ("seedream_image_to_image", "save_path"),
    ("seedream_image_to_image", "custom_name"),
    ("seedream_multi_image_fusion", "prompt"),
    ("seedream_multi_image_fusion", "image"),
    ("seedream_multi_image_fusion", "save_path"),
    ("seedream_multi_image_fusion", "custom_name"),
    ("seedream_sequential_generation", "prompt"),
    ("seedream_sequential_generation", "image"),
    ("seedream_sequential_generation", "save_path"),
    ("seedream_sequential_generation", "custom_name"),
    ("seedream_browse_images", "directory"),
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

    两类刻意差异：组图的 max_images 平铺侧声明为 int | None，None 表示未提供，
    组图据此区分「未提供时自动推导」与「显式传入」，仅比对整数分支的约束与描述；
    声明非空语义的字段在平铺侧补 pattern 镜像，比对时剔除 pattern 键并另行断言
    pattern 存在且取值正确，未声明非空语义的字段不得携带 pattern。
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
    """提取字段约束的闭区间边界，数值边界与长度边界同权，缺该向约束时为 None。"""
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

    MCPServer 不解析工具 docstring 的参数说明，inputSchema 的参数 description
    唯一来源是平铺签名的 Field 默认值，工具 docstring 仅承担工具级描述。逐字段
    定义等价断言在两侧同时缺失 description 时会空过，本断言补上非空要求与显式
    相等，任一侧清空或单独改写描述即失败。
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

    逐字段定义断言只比对顶层属性，optimize_prompt_options、tools 与枚举字段的
    实际结构位于 $defs，嵌套字段描述或枚举取值漂移不会被顶层比对发现。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].input_schema
        assert schema.get("$defs", {}) == model.model_json_schema().get("$defs", {}), name


async def test_flat_schema_description_tokens_match_model_constraints() -> None:
    """inputSchema 描述文案中的数值区间与默认值必须与模型字段实际约束一致。

    描述镜像只保证两侧文案相同，不保证文案与规则相符；约束调整后残留硬编码区间
    或默认值的文案不会被镜像断言发现。本断言提取 inputSchema 描述中的数字区间
    与默认值数字，与模型字段声明的 ge/le、min_length/max_length 及默认值比对，
    文案陈述的规则必须真实存在且数值相等。
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
        ("seedream_text_to_image", {"prompt": "a cat", "watermarkss": True}),
        ("seedream_text_to_image", {"prompt": "a cat", "sze": "2K"}),
        (
            "seedream_image_to_image",
            {"prompt": "a cat", "image": "https://example.com/cat.png", "sze": "2K"},
        ),
        (
            "seedream_multi_image_fusion",
            {
                "prompt": "a cat",
                "image": ["https://example.com/cat.png"],
                "responce_format": "url",
            },
        ),
        ("seedream_sequential_generation", {"prompt": "a cat", "max_imagess": 4}),
        ("seedream_browse_images", {"directory": ".", "recursve": True}),
    ],
)
async def test_flat_tool_rejects_unknown_parameter_names(tool_name: str, typo_args: dict) -> None:
    """五工具的平铺签名在运行时拒绝拼错参数名，不被静默丢弃。

    输入模型以 extra=forbid 拒绝未知键，嵌套 params 形态下拼错参数在模型校验即
    失败；平铺签名经 MCPServer 参数模型默认忽略未知键，server 注册期把参数模型替换
    为 extra=forbid 子类补偿。客户端可据 inputSchema 在本地拒绝，服务端运行时
    同样拒绝，模型收到明确报错后可自行纠正参数名。其余参数均取合法值，确保
    报错仅源于未知键。
    """
    typo_key = next(
        key for key in typo_args if key not in _TOOL_INPUT_MODELS[tool_name].model_fields
    )
    with pytest.raises(ToolError, match=typo_key):
        await mcp.call_tool(tool_name, typo_args)


@pytest.mark.parametrize(
    ("tool_name", "blank_args"),
    [
        ("seedream_text_to_image", {"prompt": "   "}),
        ("seedream_text_to_image", {"prompt": "a cat", "save_path": "   "}),
        ("seedream_text_to_image", {"prompt": "a cat", "custom_name": ""}),
        ("seedream_image_to_image", {"prompt": "a cat", "image": "   "}),
        (
            "seedream_multi_image_fusion",
            {"prompt": "a cat", "image": ["https://example.com/a.png", "   "]},
        ),
        ("seedream_sequential_generation", {"prompt": "a cat", "image": "   "}),
        ("seedream_sequential_generation", {"prompt": "a cat", "image": ["   "]}),
        ("seedream_browse_images", {"directory": "   "}),
    ],
)
async def test_flat_tool_rejects_blank_string_inputs(tool_name: str, blank_args: dict) -> None:
    """纯空白字符串在平铺签名层被拒，不进入工具体后才失败。

    模型层经 str_strip_whitespace 加 min_length 或非空校验器拒绝纯空白输入；
    平铺参数模型不含 strip 配置，server 以 pattern 镜像该语义，使这些输入在
    协议层即被拒绝，而非进入工具体后被 SDK 包成英文前缀的 ToolError。其余参数
    均取合法值，确保报错仅源于空白输入。
    """
    with pytest.raises(ToolError):
        await mcp.call_tool(tool_name, blank_args)


# ==================== 组图参考图列表上限的 schema 层声明 ====================


async def test_sequential_image_list_branch_declares_max_items() -> None:
    """组图 image 列表分支的 inputSchema 声明 maxItems 14，与多图融合口径一致。

    上限此前仅存在于模型 before-validator，inputSchema 不携带 maxItems，客户端
    本地校验无法拒绝超量输入。镜像等价断言只锁定两侧一致，两侧同时丢失声明时
    仍相等，本断言独立锁定声明存在。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    schema = by_name["seedream_sequential_generation"].input_schema
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
        await mcp.call_tool("seedream_sequential_generation", {"prompt": "a cat", "image": images})
    message = str(exc_info.value)
    assert "参考图片数量" not in message
    assert "14" in message
