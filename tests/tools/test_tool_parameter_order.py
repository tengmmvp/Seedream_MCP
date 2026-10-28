"""生成工具 Input schema 字段顺序与 MCP 平铺 inputSchema 等价性守护。

锁定两层契约：schemas 输入模型的字段顺序以 prompt 居首，以及平铺工具签名生成
的 inputSchema 与模型 schema 在字段顺序、required、逐字段定义上的等价性，任何
一侧改动未同步即失败。另锁定封闭性：顶层 additionalProperties 为 false、拼错
参数在运行时被 ToolError 拒绝。等价性延伸到描述维度：逐字段 description 非空
且镜像模型字段描述，嵌套 $defs 全等，描述文案的数值区间与默认值同实际约束一致。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any, cast, get_args, get_origin

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from pydantic import BaseModel, BeforeValidator, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from _lib.generation_payloads import GENERATION_BASE_PAYLOADS
from _lib.no_param_tools import NO_PARAM_TOOLS

from seedream_mcp.server.runtime import mcp
from seedream_mcp.tools.schemas import (
    BrowseImagesInput,
    FORMAT_FILTER_ITEM_MAX_LENGTH,
    ImageToImageInput,
    multi_image_field_type,
    MULTI_IMAGE_MIN_ITEMS,
    MultiImageFusionInput,
    OPTIMIZE_PROMPT_OPTIONS_DESCRIPTION,
    OUTPUT_FORMAT_DESCRIPTION,
    SIZE_DESCRIPTION,
    SIZE_WITH_LAYER_DESCRIPTION,
    STREAM_DESCRIPTION,
    SequentialGenerationInput,
    sequential_image_field_type,
    TextToImageInput,
    TOOLS_DESCRIPTION,
)
from seedream_mcp.utils.model.capabilities import (
    MODEL_CAPABILITIES,
    MODEL_FAMILY_UNKNOWN,
    SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
)

# MCP 注册工具名到输入模型的映射，平铺 inputSchema 等价性断言的数据源。
_TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "text_to_image": TextToImageInput,
    "image_to_image": ImageToImageInput,
    "multi_image_fusion": MultiImageFusionInput,
    "sequential_generation": SequentialGenerationInput,
    "browse_images": BrowseImagesInput,
}


def test_text_to_image_parameter_order() -> None:
    """text_to_image 输入模型字段顺序，prompt 居首。"""
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
    """image_to_image 输入模型字段顺序，prompt 居首。"""
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
    """multi_image_fusion 输入模型字段顺序，prompt 居首。"""
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
    """sequential_generation 输入模型字段顺序，max_images 位于 size 之后。"""
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
    """browse_images 输入模型字段顺序，directory 居首。"""
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


async def test_tool_registration_independent_of_collection_order() -> None:
    """单文件独立运行时共享 mcp 单例上六工具均已注册。

    工具注册发生在 seedream_mcp.server 导入期，由 tests/conftest.py 顶层导入兜底；
    移除该导入时本用例单独运行即失败。get_model_info 无输入模型，不参与平铺
    inputSchema 等价性断言。
    """
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == set(_TOOL_INPUT_MODELS) | NO_PARAM_TOOLS


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


def _schema_allows_null(prop: dict[str, Any]) -> bool:
    """判定属性的 anyOf 声明是否含 null 分支。"""
    return any(branch.get("type") == "null" for branch in prop.get("anyOf", ()))


def _annotation_accepts_none(annotation: Any) -> bool:
    """判定字段注解是否接受 None，顶层 Annotated 取底层类型。"""
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return type(None) in get_args(annotation)


async def test_flat_input_schema_field_definitions_match_model() -> None:
    """逐字段 schema 定义与模型 json schema 全等，锁定描述与约束不漂移。

    刻意差异仅一类：非空语义字段以 pattern 镜像，比对时剔除 pattern 键另行断言。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        schema = by_name[name].input_schema
        model_schema = model.model_json_schema()
        for field in model.model_fields:
            tool_prop = schema["properties"][field]
            model_prop = model_schema["properties"][field]
            if (name, field) in _NON_BLANK_MIRROR_FIELDS:
                assert _contains_pattern(tool_prop, _NON_BLANK_PATTERN), (name, field)
                assert _strip_pattern_keys(tool_prop) == _strip_pattern_keys(model_prop), (
                    name,
                    field,
                )
            else:
                assert not _contains_pattern(tool_prop, _NON_BLANK_PATTERN), (name, field)
                assert tool_prop == model_prop, (name, field)


async def test_signature_nullable_fields_are_model_optional() -> None:
    """平铺签名允许 null 的字段在输入模型必须 Optional，两入口 null 语义不因此分叉。

    MCP 入口剥离签名默认 None，Web 入口显式 null 直接进模型校验；
    签名允许 null 而模型字段非 Optional 时 Web 侧显式 null 被拒 400 与 MCP 缺省分叉。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name, model in _TOOL_INPUT_MODELS.items():
        for field, prop in by_name[name].input_schema["properties"].items():
            if _schema_allows_null(prop):
                assert _annotation_accepts_none(model.model_fields[field].annotation), (
                    name,
                    field,
                )


@pytest.mark.parametrize("model", list(GENERATION_BASE_PAYLOADS))
def test_generation_input_assignment_keeps_sibling_fields(model: type[BaseModel]) -> None:
    """构造后属性赋值成功且全部字段仍可读，防 mode=before 的 model_validator 随赋值重跑剥除 None 兄弟字段。"""
    instance = model(**GENERATION_BASE_PAYLOADS[model])
    setattr(instance, "size", "2K")
    assert getattr(instance, "size") == "2K"
    for field in model.model_fields:
        getattr(instance, field)


# ==================== 参数 description 镜像与文案约束一致性 ====================

# 描述文案中的数字区间形态，如「1-15」，用于比对字段实际约束的闭区间边界。
_DESCRIPTION_RANGE_PATTERN = re.compile(r"(\d+)\s*-\s*(\d+)")
# 描述文案中的默认值数字形态，如「默认 0」，用于比对字段声明的默认值。
_DESCRIPTION_DEFAULT_PATTERN = re.compile(r"默认\s*(\d+)")
# 描述文案中保存路径示例的 ISO 日期层（如 2026-08-15），不是数值区间，扫描前剥除。
_DESCRIPTION_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


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
            range_text = _DESCRIPTION_DATE_PATTERN.sub("", description)
            for match in _DESCRIPTION_RANGE_PATTERN.finditer(range_text):
                assert (int(match.group(1)), int(match.group(2))) == (lower, upper), (
                    name,
                    field,
                    match.group(0),
                )
            for match in _DESCRIPTION_DEFAULT_PATTERN.finditer(description):
                assert int(match.group(1)) == info.default, (name, field, match.group(0))


# ==================== 平铺 inputSchema 封闭性 ====================


async def test_flat_input_schema_forbids_additional_properties() -> None:
    """六工具 inputSchema 顶层 additionalProperties 恒为 False，声明平铺字段封闭集合。

    平铺签名使 MCPServer 生成的 schema 不再继承输入模型的 extra=forbid 声明；
    server 注册期的 _tighten_flat_tool_schemas 负责补偿，含无参的 get_model_info
    在内，本断言锁定补偿不缺失。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name in [*_TOOL_INPUT_MODELS, *NO_PARAM_TOOLS]:
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
                "image": [
                    "https://example.com/cat.png",
                    "https://example.com/cat2.png",
                ],
                "responce_format": "url",
            },
        ),
        ("sequential_generation", {"prompt": "a cat", "max_imagess": 4}),
        ("browse_images", {"directory": ".", "recursve": True}),
        ("get_model_info", {"modell_id": "doubao-seedream-5.0-pro"}),
    ],
)
async def test_flat_tool_rejects_unknown_parameter_names(
    tool_name: str, typo_args: dict[str, Any]
) -> None:
    """六工具的平铺签名在运行时拒绝拼错参数名，不被静默丢弃。

    平铺参数模型默认忽略未知键，server 注册期替换为 extra=forbid 子类补偿，服务端
    与 inputSchema 本地校验同样拒绝。其余参数均取合法值，确保报错仅源于未知键。
    """
    # get_model_info 无输入模型，任意传入键均为未知键。
    known = _TOOL_INPUT_MODELS[tool_name].model_fields if tool_name in _TOOL_INPUT_MODELS else ()
    typo_key = next(key for key in typo_args if key not in known)
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
async def test_flat_tool_rejects_blank_string_inputs(
    tool_name: str, blank_args: dict[str, Any]
) -> None:
    """纯空白字符串在平铺签名层被拒，不进入工具体后才失败。

    模型层经 strip 加非空校验拒绝，平铺参数模型不含 strip 配置，server 以 pattern
    镜像该语义在协议层拒绝。其余参数均取合法值，确保报错仅源于空白输入。
    """
    with pytest.raises(ToolError):
        await mcp.call_tool(tool_name, blank_args)


# ==================== 参考图列表数量上限的 schema 层声明与前置拒绝 ====================


@pytest.mark.parametrize("minimum", [1, 2, 3])
def test_image_count_field_factories_couple_validator_message_with_schema_bounds(
    minimum: int,
) -> None:
    """计数字段工厂按同一 minimum 派生中文计数文案与 schema 数量界，同界是去重单源的成立前提。

    以任意 minimum 参数化，工厂任一侧硬编码界使闭包文案或 minItems/maxItems 偏离参数即失败。
    """
    maximum = SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
    for factory in (multi_image_field_type, sequential_image_field_type):
        field_type = factory(str, minimum)
        # BeforeValidator.func 在类型上允许带 ValidationInfo 的双参形态，闭包实际为单参。
        validator = cast(
            "Callable[[object], object]",
            next(arg.func for arg in get_args(field_type) if isinstance(arg, BeforeValidator)),
        )
        schema = TypeAdapter(field_type).json_schema()
        array_branch = next(
            branch for branch in schema.get("anyOf", [schema]) if branch.get("type") == "array"
        )
        assert array_branch["minItems"] == minimum
        assert array_branch["maxItems"] == maximum
        for count in (minimum - 1, maximum + 1):
            with pytest.raises(ValueError, match=f"参考图片数量需在 {minimum}-{maximum} 之间"):
                validator([f"img{i}" for i in range(count)])


async def test_sequential_image_list_branch_declares_item_bounds() -> None:
    """组图 image 列表分支的 inputSchema 声明 minItems 1 与 maxItems 14，maxItems 与多图融合口径一致。

    镜像等价断言在两侧同时丢失声明时仍相等，本断言独立锁定声明存在，客户端本地
    校验才能拒绝空列表与超量输入。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    schema = by_name["sequential_generation"].input_schema
    array_branch = next(
        branch for branch in schema["properties"]["image"]["anyOf"] if branch.get("type") == "array"
    )
    assert array_branch["minItems"] == 1
    assert array_branch["maxItems"] == SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES


async def test_sequential_tool_rejects_oversized_image_list() -> None:
    """组图参考图列表超过 14 张在 SDK 参数模型校验层即被拒绝，不进入工具体。

    平铺签名与输入模型同挂数量前置校验，用户可见文案为中文数量口径而非
    union 分支的英文错误。
    """
    images = [f"https://example.com/{i}.png" for i in range(15)]
    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool("sequential_generation", {"prompt": "a cat", "image": images})
    assert "参考图片数量需在 1-14 之间" in str(exc_info.value)


async def test_sequential_tool_rejects_empty_image_list() -> None:
    """组图参考图空列表在 SDK 参数模型校验层即被拒绝，不进入工具体，与多图融合同层。

    拒绝以 ToolError 而非工具体内的 isError 结果呈现，据此锁定发生在参数模型校验层；
    用户可见文案为中文数量口径而非 union 分支的英文错误。
    """
    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool("sequential_generation", {"prompt": "a cat", "image": []})
    assert "参考图片数量需在 1-14 之间" in str(exc_info.value)


@pytest.mark.parametrize("count", [1, 15])
async def test_fusion_tool_rejects_out_of_bounds_image_list(count: int) -> None:
    """融合参考图数量界外（1 张与 15 张）在 SDK 参数模型校验层即被拒绝，不进入工具体。

    平铺签名与输入模型同挂数量前置校验，用户可见文案为中文数量口径而非
    列表条数的英文约束错误。
    """
    images = [f"https://example.com/{i}.png" for i in range(count)]
    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool("multi_image_fusion", {"prompt": "a cat", "image": images})
    assert "参考图片数量需在 2-14 之间" in str(exc_info.value)


@pytest.mark.parametrize(
    ("tool_name", "model_cls", "minimum"),
    [
        ("multi_image_fusion", MultiImageFusionInput, MULTI_IMAGE_MIN_ITEMS),
        ("sequential_generation", SequentialGenerationInput, 1),
    ],
)
async def test_image_count_validator_shared_by_flat_signature_and_model(
    tool_name: str, model_cls: type[BaseModel], minimum: int
) -> None:
    """计数前置校验器同界同文案挂载于平铺签名与输入模型两侧的 image 字段。

    BeforeValidator 不进入 JSON schema，等价性镜像断言对其不可见；校验器经字段类型工厂
    按界闭包构建，两侧为不同函数对象，身份断言不可用，改经两侧字段元数据各取唯一挂载的
    校验器并以界外输入的中文文案锁定，单边漏挂、异界或另起文案即失败。新增计数受限字段时
    须接入 schemas 的字段类型工厂并同步扩充本参数表。
    """
    maximum = SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
    tool = mcp._tool_manager.get_tool(tool_name)
    assert tool is not None
    flat_field = tool.fn_metadata.arg_model.model_fields["image"]
    for field in (flat_field, model_cls.model_fields["image"]):
        mounted = [m.func for m in field.metadata if isinstance(m, BeforeValidator)]
        assert len(mounted) == 1
        count_validator = cast("Callable[[object], object]", mounted[0])
        for count in (minimum - 1, maximum + 1):
            with pytest.raises(ValueError, match=f"参考图片数量需在 {minimum}-{maximum} 之间"):
                count_validator([f"img{i}" for i in range(count)])


# ==================== 浏览后缀单项上限的 schema 层声明 ====================


async def test_browse_format_filter_items_declare_max_length() -> None:
    """format_filter 数组项的 maxLength 在平铺与模型两层 schema 均声明且同值。

    镜像等价断言在两侧同时丢失声明时仍相等，本断言独立锁定声明存在；条目经
    before-validator 归一后入核心 schema，maxLength 按归一后终值口径执行。
    """
    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    tool_prop = by_name["browse_images"].input_schema["properties"]["format_filter"]
    model_prop = BrowseImagesInput.model_json_schema()["properties"]["format_filter"]
    for prop in (tool_prop, model_prop):
        array_branch = next(branch for branch in prop["anyOf"] if branch.get("type") == "array")
        assert array_branch["items"]["maxLength"] == FORMAT_FILTER_ITEM_MAX_LENGTH


async def test_browse_tool_rejects_bare_item_at_published_max_length() -> None:
    """裸 16 字符条目满足发布的条目 maxLength 而被服务端拒绝，残余偏差在此锁定。

    客户端无法预知服务端补点，inputSchema 对无点原始条目偏宽一位；归一后触界拒绝
    发生在输入模型构造层，经流水线返回 is_error 结果而非 ToolError。
    """
    result = await mcp.call_tool("browse_images", {"format_filter": ["a" * 16]})

    assert isinstance(result, CallToolResult)
    assert result.is_error
    structured = result.structured_content
    assert structured is not None
    assert structured["error"]["type"] == "validation_error"


# ==================== 模型工具列表条目上限 ====================


async def test_generation_tool_rejects_oversized_tools_list() -> None:
    """tools 含 9 项条目被校验拒绝，平铺签名与输入模型两侧各自独立锁定。

    合法调用至多 1 个 web_search 条目，上限 8 仅拦截异常超量输入。等价性断言
    只校验两侧同步，两侧同时丢失上限时仍相等，故分别经工具调用与模型构造两路
    拒绝。
    """
    entries: list[Any] = [{"type": "web_search"}] * 9
    with pytest.raises(ToolError):
        await mcp.call_tool("text_to_image", {"prompt": "a cat", "tools": entries})
    with pytest.raises(ValidationError):
        TextToImageInput(prompt="a cat", tools=entries)


# ==================== 能力相关描述的静态提示契约 ====================

# 能力到其门控参数描述常量的映射，静态提示契约用例的参数化数据源。
_CAPABILITY_DESCRIPTION_SOURCES = {
    "allowed_presets": SIZE_DESCRIPTION,
    "allowed_presets_layered": SIZE_WITH_LAYER_DESCRIPTION,
    "supports_fast_optimize_prompt": OPTIMIZE_PROMPT_OPTIONS_DESCRIPTION,
    "supports_output_format": OUTPUT_FORMAT_DESCRIPTION,
    "supports_stream": STREAM_DESCRIPTION,
    "supports_tools": TOOLS_DESCRIPTION,
}


@pytest.mark.parametrize("capability", sorted(_CAPABILITY_DESCRIPTION_SOURCES))
def test_capability_descriptions_use_static_hint_without_family_enumeration(
    capability: str,
) -> None:
    """能力相关参数描述以静态提示声明模型门控，不枚举家族清单。

    调用者无法行动于可变模型枚举，支持模型由 get_model_info 工具与校验报错提供。
    """
    description = _CAPABILITY_DESCRIPTION_SOURCES[capability]
    assert "需当前模型支持" in description
    for caps in MODEL_CAPABILITIES.values():
        if caps.family != MODEL_FAMILY_UNKNOWN:
            assert caps.display_name not in description
