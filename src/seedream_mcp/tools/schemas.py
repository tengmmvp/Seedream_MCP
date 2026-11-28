"""Seedream MCP 工具输入模型。

本模块是工具参数校验的单一来源。
inputSchema 由 server/_registration.py 的平铺签名生成，平铺签名镜像本模块的字段定义，
等价性由 test_tool_parameter_order 锁定。
通用字段抽到 ``_*Input`` 基类，按需多重继承组合为各工具的最终输入模型。
runner 公共调用面的入参契约类型（如 ``EntryBound``）同置于此，消费方免知内部模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Callable, ClassVar, Literal, Protocol, TypeVar, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..definitions.conventions import (
    IMAGE_TO_IMAGE_TOOL_NAME,
    LAYER_DECOMPOSE_TOOL_NAME,
    LAYER_DECOMPOSITION_MAX_IMAGES,
    LAYER_DECOMPOSITION_MIN_IMAGES,
    MAX_IMAGES_MIN,
    MAX_PARALLEL_REQUEST_COUNT,
    MAX_SEQUENTIAL_TOTAL_IMAGES,
    MULTI_IMAGE_FUSION_TOOL_NAME,
    SEQUENTIAL_GENERATION_TOOL_NAME,
)
from ..utils.core.errors import (
    ErrorText,
    SeedreamValidationError,
    as_plain,
    as_plain_list,
    as_text,
    raw_error_text,
)
from ..utils.images.validation import MAX_IMAGE_FILE_SIZE
from ..utils.model.capabilities import (
    SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
    get_model_capabilities,
    layer_decomposition_image_count,
    layer_decomposition_presets_text,
)
from ..utils.core.validators import (
    INTEGER_REQUIRED_TEMPLATE,
    VALID_BACKGROUND_MODES,
    VALID_GENERATION_TOOL_TYPES,
    VALID_OPTIMIZE_MODES,
    VALID_OUTPUT_FORMATS,
    VALID_RESPONSE_FORMATS,
    resolve_sequential_max_images,
    validate_parallel_generation_options,
    validate_sequential_image_limit,
)
from ..utils.io.path import resolved_form_violation

# ==================== 平铺签名共享常量 ====================
# 以下约束与描述常量是双源镜像的单一来源。

# prompt 长度界限为产品决策，上限仅拦截异常超长输入。
PROMPT_MIN_LENGTH = 1
PROMPT_MAX_LENGTH = 100000

# tools 条目数上限为产品决策：合法调用至多一个条目，上限仅拦截异常超量输入。
TOOLS_MAX_ITEMS = 8

# 保存目录与浏览目录的长度上限为产品决策的防御性界限。
SAVE_PATH_MAX_LENGTH = 1024
DIRECTORY_MAX_LENGTH = 1024

# 文件名前缀长度上限按字符计数取 255，量级对齐文件系统单文件名的常见上限。
CUSTOM_NAME_MAX_LENGTH = 255

# 多图融合参考图最少两张为上游 API 限制。
MULTI_IMAGE_MIN_ITEMS = 2

# 组图参考图最少一张：单字符串形态归一为单元素列表。
SEQUENTIAL_IMAGE_MIN_ITEMS = 1

REQUEST_COUNT_MIN = 1
PARALLELISM_MIN = 1

# 浏览工具的深度、数量与偏移界限为产品决策，防止单次扫描或翻页过深。
MAX_DEPTH_MIN = 1
MAX_DEPTH_MAX = 10
LIMIT_MIN = 1
LIMIT_MAX = 200
OFFSET_MIN = 0
OFFSET_MAX = 100000

# 后缀单项长度上限按补点后终值计，防止错误消息整体回显超大字符串。
FORMAT_FILTER_ITEM_MAX_LENGTH = 16

# 后缀条目数上限：其余列表字段均已有界，无界列表在大请求体下造成遍历与回显放大。
FORMAT_FILTER_MAX_ITEMS = 32

# 非空语义镜像的 pattern：输入模型经 str_strip_whitespace 剥离空白后校验，纯空白
# 输入被拒；平铺签名无 strip 配置，以该 pattern 声明至少一个非空白字符的等价约束，
# 带内边距的合法值不受影响，strip 仍由函数体内组装输入模型时完成。
NON_BLANK_PATTERN = r"\S"

# 五个生成工具的 prompt 描述。
TEXT_TO_IMAGE_PROMPT_DESCRIPTION = (
    "用于生成图片的提示词，建议不超过300个汉字或600个英文单词。"
    "例如：一只戴墨镜的猫坐在月球上，写实风格。"
)
IMAGE_TO_IMAGE_PROMPT_DESCRIPTION = (
    "图片修改或风格转换的指令，建议不超过300个汉字或600个英文单词。"
    "例如：把背景换成雪山、将照片转为水彩画风格。"
)
MULTI_IMAGE_FUSION_PROMPT_DESCRIPTION = (
    "融合目标或风格描述，建议不超过300个汉字或600个英文单词。"
    "请使用“图X”指定图像（如：将图1的服装换为图2的服装）。"
)
SEQUENTIAL_PROMPT_DESCRIPTION = (
    "连贯的组图提示，需明确数量与内容，不超过300个汉字或600个英文单词。"
    "例如：生成4格漫画分镜，主角是戴红帽子的女孩，依次出现在咖啡馆、街道、公园、家中。"
)
LAYER_DECOMPOSE_PROMPT_DESCRIPTION = (
    "图层拆分意图，建议不超过300个汉字或600个英文单词；"
    "缺省时由模型自动识别图片主要元素并拆分。"
    "例如：将图片进行精确图层分离，需分离的文字坐标为 <bbox>180 64 812 198</bbox>。"
)

# 生成工具共享字段的描述。
OPTIMIZE_PROMPT_OPTIONS_DESCRIPTION = (
    "提示词优化配置，仅支持 standard 或 fast，fast 需当前模型支持。"
)

# 本地路径读取规则，参考图与浏览目录的字段描述共用。
_LOCAL_PATH_READ_RULE = "本地文件路径须在读取范围内，越界会被拒绝。其中相对路径仅限图片保存目录内。"


def _image_description(head: str, tool_name: str) -> str:
    """拼出参考图字段描述，示例路径落在该工具的保存子目录。"""
    return (
        f"{head}{_LOCAL_PATH_READ_RULE}"
        f"例如：https://example.com/a.png 或 2026-08-15/{tool_name}/b.jpeg。"
    )


_SINGLE_IMAGE_HEAD = "参考图片，支持图像 URL、本地文件路径或 Base64 图片数据。"
SINGLE_IMAGE_DESCRIPTION = _image_description(_SINGLE_IMAGE_HEAD, IMAGE_TO_IMAGE_TOOL_NAME)
LAYER_DECOMPOSE_IMAGE_DESCRIPTION = _image_description(
    _SINGLE_IMAGE_HEAD, LAYER_DECOMPOSE_TOOL_NAME
)
MULTI_IMAGE_DESCRIPTION = _image_description(
    f"输入图像，数量 2-{SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES} 张，部分模型上限更低；"
    "每张支持图像 URL、本地文件路径或 Base64 图片数据。",
    MULTI_IMAGE_FUSION_TOOL_NAME,
)
SEQUENTIAL_IMAGE_DESCRIPTION = _image_description(
    f"可选的参考图片，单张或多张，最多 {SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES} 张；"
    "每张支持图像 URL、本地文件路径或 Base64 图片数据。",
    SEQUENTIAL_GENERATION_TOOL_NAME,
)
# 组图与图层拆分的规则文本：容器字段描述与嵌套字段描述同源引用，约束调整单点维护。
_MAX_IMAGES_RULE = (
    f"本次请求允许生成的最大图片数量，范围 {MAX_IMAGES_MIN}-{MAX_SEQUENTIAL_TOTAL_IMAGES}；"
    f"未提供时按参考图数量推导为 {MAX_SEQUENTIAL_TOTAL_IMAGES} 减参考图张数，"
    f"参考图数量与其之和不得超过 {MAX_SEQUENTIAL_TOTAL_IMAGES}。"
)
SEQUENTIAL_IMAGE_GENERATION_OPTIONS_DESCRIPTION = (
    f"组图配置，仅组图生成支持；max_images 为{_MAX_IMAGES_RULE}"
)
_LAYER_MAX_IMAGES_RULE = (
    f"含底图的最大产出图片数（{LAYER_DECOMPOSITION_MIN_IMAGES}-"
    f"{LAYER_DECOMPOSITION_MAX_IMAGES}，未指定时由模型默认）"
)
LAYER_DECOMPOSITION_OPTIONS_DESCRIPTION = (
    f"图层拆分配置，max_images 为{_LAYER_MAX_IMAGES_RULE}，"
    "text_recognition 开启后对规整文字图层返回富文本 OCR 结果；仅部分模型支持。"
)
SIZE_DESCRIPTION = "生成图片尺寸，可选 1K/1.5K/2K/3K/4K 或 <宽>x<高> 像素值，需当前模型支持；未提供时使用全局默认值。例如：2K 或 2048x2048。"
SIZE_WITH_AUTO_DESCRIPTION = (
    "输出图片尺寸，仅支持分辨率档位或 auto（按输入图尺寸与宽高比自适应），"
    f"档位为 {layer_decomposition_presets_text()}，需当前模型支持；"
    "未提供时默认 auto。"
)
BACKGROUND_DESCRIPTION = (
    "图片透明通道，需当前模型支持；"
    "transparent 生成透明背景图（部分模型要求输入图片带透明通道且仅图生场景可用），"
    "opaque 生成常规实体背景图，"
    "auto 由模型按提示词与输出格式自动判断（仅部分模型支持）。"
)
WATERMARK_DESCRIPTION = "是否添加水印；未提供时沿用全局默认值（默认不添加）。"
TOOLS_DESCRIPTION = "模型工具配置，目前仅支持联网搜索（web_search），需当前模型支持。"
OUTPUT_FORMAT_DESCRIPTION = "输出图片格式，可选 jpeg 或 png，需当前模型支持。"
RESPONSE_FORMAT_DESCRIPTION = "响应格式，url 返回可下载链接，b64_json 返回 base64 数据。"
STREAM_DESCRIPTION = (
    "是否启用流式输出，需当前模型支持；开启后将以事件流返回生成进度，且 request_count 必须为 1。"
)
REQUEST_COUNT_DESCRIPTION = "同一提示并行发起的独立生成次数，每次各产出一张图；适合一次获取多张候选图，与组图工具的 max_images 无关。"
REQUEST_COUNT_SEQUENTIAL_DESCRIPTION = (
    "同一提示并行发起的独立生成次数，每次各产出一组图片，组内图片数量由模型"
    "按提示词决定，最多 max_images 张；适合一次获取多组独立的组图结果。"
)
REQUEST_COUNT_LAYER_DESCRIPTION = (
    "同一图片并行发起的独立拆分次数，每次各产出一组「底图与图层」图片，组内"
    "数量上限由 layer_decomposition_options.max_images 指定（仅部分模型支持，"
    "不支持时为模型固定拆分）；适合一次获取多组独立的拆分结果。"
)
PARALLELISM_DESCRIPTION = (
    "并行度上限（1-10），不得超过 request_count；"
    "未提供时自动取 min(request_count, 10)，一般无需手动指定。"
)
AUTO_SAVE_DESCRIPTION = "是否自动保存到本地；未提供时遵循全局配置（默认开启）。"
SAVE_PATH_DESCRIPTION = "自定义保存目录，未提供时使用自动保存配置的默认路径。"
CUSTOM_NAME_DESCRIPTION = "自定义文件名前缀，未提供时根据提示词自动生成。"

# 浏览工具字段的描述。
DIRECTORY_DESCRIPTION = (
    "要浏览的目录路径，默认浏览图片保存目录。"
    f"{_LOCAL_PATH_READ_RULE}返回的条目为本地文件路径，可直接填入参考图参数。"
)
RECURSIVE_DESCRIPTION = "是否递归查找子目录。"
MAX_DEPTH_DESCRIPTION = "递归查找的最大深度（1-10）。"
LIMIT_DESCRIPTION = "返回的最大文件数量（1-200）。"
OFFSET_DESCRIPTION = "分页偏移量（从第几张开始返回，0-100000），默认 0；配合 limit 翻页。"
FORMAT_FILTER_DESCRIPTION = (
    "需要过滤的图片后缀列表，如 ['.jpeg', '.png']；仅保留受支持的后缀。"
    "空列表或全部后缀不受支持时跳过扫描并返回校验错误，支持的后缀见错误信息。"
)
SHOW_DETAILS_DESCRIPTION = "是否展示文件大小、修改时间等详细信息。"


class ResponseFormat(str, Enum):
    """图片生成响应格式枚举。"""

    URL = "url"
    B64_JSON = "b64_json"


class OutputFormat(str, Enum):
    """图片文件输出格式枚举。"""

    JPEG = "jpeg"
    PNG = "png"


class GenerationToolType(str, Enum):
    """模型工具类型枚举。"""

    WEB_SEARCH = "web_search"


class BackgroundMode(str, Enum):
    """图片透明通道模式枚举。"""

    TRANSPARENT = "transparent"
    OPAQUE = "opaque"
    AUTO = "auto"


# 各枚举字段的合法值排序表，从 validators 白名单单源派生。
_RESPONSE_FORMAT_ALLOWED = sorted(VALID_RESPONSE_FORMATS)
_OUTPUT_FORMAT_ALLOWED = sorted(VALID_OUTPUT_FORMATS)
_GENERATION_TOOL_TYPE_ALLOWED = sorted(VALID_GENERATION_TOOL_TYPES)
_BACKGROUND_MODE_ALLOWED = sorted(VALID_BACKGROUND_MODES)
_OPTIMIZE_PROMPT_MODE_ALLOWED = sorted(VALID_OPTIMIZE_MODES)


def _enum_normalizer(allowed: list[str], label: str) -> Callable[[object], object]:
    """按排序表生成 strip 与 lower 归一的 before-validator，非法值以中文文案拒绝。"""

    @field_validator(label, mode="before")
    def _normalize(value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized not in allowed:
                raise ValueError(
                    raw_error_text(
                        "{label} 仅支持 {allowed}",
                        label=as_plain(label),
                        allowed=as_plain_list(allowed),
                    )
                )
            return normalized
        return value

    return _normalize


class OptimizePromptOptions(BaseModel):
    """提示词优化配置模型。

    配置提示词优化策略，平衡生成质量与响应速度。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: Literal["standard", "fast"] = Field(
        default="standard",
        description="提示词优化模式：standard 高质量，fast 优先速度，fast 需当前模型支持。",
    )

    normalize_mode = _enum_normalizer(_OPTIMIZE_PROMPT_MODE_ALLOWED, "mode")


class GenerationTool(BaseModel):
    """模型工具配置。"""

    model_config = ConfigDict(extra="forbid")

    type: GenerationToolType = Field(
        ...,
        description="工具类型，目前仅支持 web_search。",
    )

    normalize_type = _enum_normalizer(_GENERATION_TOOL_TYPE_ALLOWED, "type")


def _reject_bool_integer(value: object, info: ValidationInfo) -> object:
    """整数字段拒绝布尔的 before-validator：布尔是 int 子类，pydantic 宽松模式会静默强转为整数。"""

    if isinstance(value, bool):
        raise ValueError(raw_error_text(INTEGER_REQUIRED_TEMPLATE, field=as_plain(info.field_name)))
    return value


# 整数字段的类型别名，输入模型与平铺签名共用。
# 可空形态整体包裹联合类型，校验器落在联合成员内会使 Field 约束以 ge/le 原名进入 JSON schema。
IntegerType = Annotated[int, BeforeValidator(_reject_bool_integer)]
NullableIntegerType = Annotated[int | None, BeforeValidator(_reject_bool_integer)]


class SequentialImageGenerationOptions(BaseModel):
    """组图配置模型。

    与官方 sequential_image_generation_options 参数同名同构。
    """

    model_config = ConfigDict(extra="forbid")

    max_images: NullableIntegerType = Field(
        default=None,
        ge=MAX_IMAGES_MIN,
        le=MAX_SEQUENTIAL_TOTAL_IMAGES,
        description=_MAX_IMAGES_RULE,
    )


class LayerDecompositionOptions(BaseModel):
    """图层拆分配置模型。

    仅 5.5 家族支持；未给定的子字段不进入请求体，由 API 按官方默认取值。
    """

    model_config = ConfigDict(extra="forbid")

    max_images: NullableIntegerType = Field(
        default=None,
        ge=LAYER_DECOMPOSITION_MIN_IMAGES,
        le=LAYER_DECOMPOSITION_MAX_IMAGES,
        description=f"{_LAYER_MAX_IMAGES_RULE}。",
    )
    text_recognition: bool | None = Field(
        default=None,
        description="是否对规整文字图层返回富文本 OCR 结果。",
    )


class _PromptAndOptimizeInput(BaseModel):
    """提示词与提示词优化参数。"""

    # prompt 在基类声明以锚定字段顺序：模型字段顺序是 server/_registration.py 平铺签名的镜像来源，
    # prompt 须居首；图层拆分子类覆盖为 str | None 以支持拆分意图缺省。
    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description=TEXT_TO_IMAGE_PROMPT_DESCRIPTION,
    )
    optimize_prompt_options: OptimizePromptOptions | None = Field(
        default=None,
        description=OPTIMIZE_PROMPT_OPTIONS_DESCRIPTION,
    )


# image 输入的字符数防御界：合法单图 base64 约 4/3 倍文件大小，取两倍文件上限
# 留余量，仅拦截异常超大载荷，stdio 传输无请求体上限时避免拒绝前先付全量拷贝。
MAX_IMAGE_INPUT_CHARS = MAX_IMAGE_FILE_SIZE * 2


def _reject_blank_or_oversized_image_items(items: list[str]) -> list[str]:
    """逐项 strip 归一后拒绝空白与超防御界条目并返回归一列表。"""
    normalized: list[str] = []
    for item in items:
        stripped = item.strip()
        if not stripped:
            raise ValueError("image 不能为空字符串")
        if len(stripped) > MAX_IMAGE_INPUT_CHARS:
            raise ValueError(
                raw_error_text(
                    "image 输入长度 {length} 超过防御上限 {limit} 字符",
                    length=as_plain(len(stripped)),
                    limit=as_plain(MAX_IMAGE_INPUT_CHARS),
                )
            )
        normalized.append(stripped)
    return normalized


def _image_count_validator(minimum: int) -> Callable[[object], object]:
    """生成数量界外拒绝的 before-validator，上限固定为参考图张数上限。"""

    def validate_count(value: object) -> object:
        # 数量界外的中文文案先于字段约束的英文错误到达用户。
        if (
            isinstance(value, list)
            and not minimum <= len(value) <= SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
        ):
            raise ValueError(
                raw_error_text(
                    "参考图片数量需在 {minimum}-{maximum} 之间",
                    minimum=as_plain(minimum),
                    maximum=as_plain(SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES),
                )
            )
        return value

    return validate_count


_ItemT = TypeVar("_ItemT")

# 条目类型参数化的融合族 image 类型：组合形状在此单点，模型侧与平铺侧按条目类型订阅。
MultiImageType = Annotated[
    list[_ItemT],
    BeforeValidator(_image_count_validator(MULTI_IMAGE_MIN_ITEMS)),
    Field(
        min_length=MULTI_IMAGE_MIN_ITEMS,
        max_length=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
    ),
]

# 输入模型侧 image 类型：条目为纯 str，空白与超防御界由字段校验器拒绝。
_MULTI_IMAGE_MODEL_TYPE = MultiImageType[str]


class _SingleImageInput(BaseModel):
    """单图输入参数。"""

    image: str = Field(
        ...,
        description=SINGLE_IMAGE_DESCRIPTION,
    )

    @field_validator("image")
    @classmethod
    def reject_blank_or_oversized_image(cls, value: str) -> str:
        """strip 归一并拒绝空白与超防御界的字符串，使其在 schema 层即报错，
        而非放行到 client 归一化层后退化为 isError 工具结果。"""
        return _reject_blank_or_oversized_image_items([value])[0]

    def reference_images(self) -> list[str]:
        """场景声明：单图形态，参考图恒一张。"""
        return [self.image]


class _MultiImageInput(BaseModel):
    """多图输入参数。"""

    image: _MULTI_IMAGE_MODEL_TYPE = Field(
        ...,
        description=MULTI_IMAGE_DESCRIPTION,
    )

    @field_validator("image")
    @classmethod
    def reject_blank_or_oversized_items(cls, value: list[str]) -> list[str]:
        """逐项 strip 归一并拒绝空白与超防御界的字符串，使其在 schema 层即报错，而非放行到
        client 归一化层后退化为 isError 工具结果。"""
        return _reject_blank_or_oversized_image_items(value)

    def reference_images(self) -> list[str]:
        """场景声明：多图形态，image 必填恒为列表。"""
        return self.image


# 条目类型参数化的组图族 image 类型：单串与列表分支的组合形状在此单点。
SequentialImageType = Annotated[
    _ItemT
    | Annotated[
        list[_ItemT],
        Field(
            min_length=SEQUENTIAL_IMAGE_MIN_ITEMS,
            max_length=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
        ),
    ]
    | None,
    BeforeValidator(_image_count_validator(SEQUENTIAL_IMAGE_MIN_ITEMS)),
]

# 输入模型侧 image 类型：条目为纯 str，单字符串形态经字段校验器归一为单元素列表。
_SEQUENTIAL_IMAGE_MODEL_TYPE = SequentialImageType[str]


class _SequentialImageInput(BaseModel):
    """组图参考图输入参数。"""

    # 列表分支声明数量上下界使 inputSchema 携带 minItems/maxItems，
    # 客户端本地校验即可拒绝空列表与超量输入。
    image: _SEQUENTIAL_IMAGE_MODEL_TYPE = Field(
        default=None,
        description=SEQUENTIAL_IMAGE_DESCRIPTION,
    )

    @field_validator("image", mode="before")
    @classmethod
    def normalize_reference_images(cls, value: str | list[str] | None) -> list[str] | None:
        """归一单字符串为单元素列表并拒绝非法形态，数量校验由外层 BeforeValidator 前置执行。"""
        if value is None:
            return None
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            raise ValueError("image 必须是字符串或字符串列表")
        return value

    @field_validator("image")
    @classmethod
    def reject_blank_or_oversized_items(cls, value: list[str] | None) -> list[str] | None:
        """逐项 strip 归一并拒绝空白与超防御界的字符串，使其在 schema 层即报错，
        而非放行到 client 归一化层后退化为 isError 工具结果。"""
        if value is None:
            return None
        return _reject_blank_or_oversized_image_items(value)

    def reference_images(self) -> list[str] | None:
        """场景声明：组图参考可选，字段校验器已归一为列表。"""
        return cast("list[str] | None", self.image)


class _BackgroundInput(BaseModel):
    """透明通道参数，文生图、图文生图、多图融合与组图输出工具暴露（图层拆分
    工具不暴露），支持取值与调用形态由能力表判定，门控在 context 构建与 client
    重校验两处执行。"""

    background: BackgroundMode | None = Field(
        default=None,
        description=BACKGROUND_DESCRIPTION,
    )

    normalize_background = _enum_normalizer(_BACKGROUND_MODE_ALLOWED, "background")


class _SizeInput(BaseModel):
    """输出尺寸参数。"""

    size: str | None = Field(
        default=None,
        description=SIZE_DESCRIPTION,
    )


class _WatermarkInput(BaseModel):
    """水印参数。"""

    watermark: bool | None = Field(
        default=None,
        description=WATERMARK_DESCRIPTION,
    )


class _LayerOptionsInput(BaseModel):
    """图层拆分配置参数，仅图层拆分工具暴露，支持与否由能力表统一判定，
    门控在 context 构建与 client 重校验两处执行。"""

    layer_decomposition_options: LayerDecompositionOptions | None = Field(
        default=None,
        description=LAYER_DECOMPOSITION_OPTIONS_DESCRIPTION,
    )


class _ToolsInput(BaseModel):
    """模型工具配置参数，如联网搜索。"""

    tools: list[GenerationTool] | None = Field(
        default=None,
        max_length=TOOLS_MAX_ITEMS,
        description=TOOLS_DESCRIPTION,
    )


class _OutputFormatInput(BaseModel):
    """输出图片格式参数。"""

    output_format: OutputFormat | None = Field(
        default=None,
        description=OUTPUT_FORMAT_DESCRIPTION,
    )

    normalize_output_format = _enum_normalizer(_OUTPUT_FORMAT_ALLOWED, "output_format")


class _SequentialOptionsInput(BaseModel):
    """组图配置参数，仅组图输出工具暴露。

    options 内 max_images 为 Optional 字段使显式 null 与缺省同走按参考图数量
    推导；字段仅承载显式输入，生效值经宿主模型的 effective_max_images 现算。
    """

    sequential_image_generation_options: SequentialImageGenerationOptions | None = Field(
        default=None,
        description=SEQUENTIAL_IMAGE_GENERATION_OPTIONS_DESCRIPTION,
    )


class _ResponseAndExecutionInput(BaseModel):
    """响应送达与执行编排参数。"""

    response_format: ResponseFormat = Field(
        default=ResponseFormat.URL,
        description=RESPONSE_FORMAT_DESCRIPTION,
    )
    stream: bool = Field(
        default=False,
        description=STREAM_DESCRIPTION,
    )
    request_count: IntegerType = Field(
        default=1,
        ge=REQUEST_COUNT_MIN,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description=REQUEST_COUNT_DESCRIPTION,
    )
    parallelism: NullableIntegerType = Field(
        default=None,
        ge=PARALLELISM_MIN,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description=PARALLELISM_DESCRIPTION,
    )

    normalize_response_format = _enum_normalizer(_RESPONSE_FORMAT_ALLOWED, "response_format")

    @model_validator(mode="after")
    def validate_parallel_options(self) -> "_ResponseAndExecutionInput":
        """校验并行执行参数组合并把缺省 parallelism 掐尖为生效值。

        校验器随字段声明置于本基类，混入方漏字段时在首次实例化即抛 AttributeError
        暴露遗漏。缺省推导公式在 validate_parallel_generation_options 单点维护，
        context 直接消费生效值。
        """
        try:
            _, parallelism = validate_parallel_generation_options(
                request_count=self.request_count,
                parallelism=self.parallelism,
                stream=self.stream,
                max_request_count=MAX_PARALLEL_REQUEST_COUNT,
            )
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc
        object.__setattr__(self, "parallelism", parallelism)
        return self


class BaseGenerationInput(BaseModel):
    """图片生成工具的通用输入校验基类。

    提供共享模型配置、存储参数、校验逻辑与单请求产图数的单点派生，
    其余字段顺序由各工具输入模型的 mixin 排列定义。
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    auto_save: bool | None = Field(
        default=None,
        description=AUTO_SAVE_DESCRIPTION,
    )
    save_path: str | None = Field(
        default=None,
        max_length=SAVE_PATH_MAX_LENGTH,
        description=SAVE_PATH_DESCRIPTION,
    )
    custom_name: str | None = Field(
        default=None,
        max_length=CUSTOM_NAME_MAX_LENGTH,
        description=CUSTOM_NAME_DESCRIPTION,
    )

    @field_validator("save_path", "custom_name", check_fields=False)
    @classmethod
    def validate_non_empty(cls, value: str | None) -> str | None:
        """去除首尾空格并拒绝空串，None 跳过校验。"""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("该字段不能为空字符串")
        return normalized

    @classmethod
    def per_request_image_count(
        cls,
        *,
        image_count: int,
        max_images: int | None,
        model_id: str,
        layer_max_images: int | None = None,
    ) -> int:
        """单请求产图数：按场景定义 option mixin 的组合单点派生产图形态，子类不覆写。

        入参为请求体字段的投影值，供不经模型实例化的调用方消费。
        核心投影键均必传，漏接调用点即刻 TypeError 而非静默按缺省档低估，
        layer_max_images 为可选投影。
        派发随 option mixin：混入 _LayerOptionsInput 者
        经 layer_decomposition_image_count 单点投影（显式值 → 官方默认 → 能力表满额，不支持拆分者为 0），
        混入 _SequentialOptionsInput 者先能力门控（不支持家族产 0，与拆分投影对称，
        调用点免做外层判断）再与 effective_max_images 共用同一推导函数，
        未混入者恒产一图。
        产图数值与流水线真实形态的逐值对账由 webapp parity 守护锁定。
        """
        if issubclass(cls, _LayerOptionsInput):
            return layer_decomposition_image_count(layer_max_images, model_id)
        if issubclass(cls, _SequentialOptionsInput):
            if not get_model_capabilities(model_id).supports_sequential_generation:
                return 0
            return resolve_sequential_max_images(image_count, max_images)
        return 1

    @classmethod
    def worst_case_projection(cls, *, model_id: str) -> int:
        """单请求产图数的最坏情形投影：各场景的最坏入参随场景在此单点声明，
        满档等待预算聚合（webapp）免逐工具手工挑参。

        数值为 per_request_image_count 在该场景投影域上的最大值：拆分取配置
        值域上界（不支持拆分配置的家族自门控取能力表满额），组图取总数上界
        （不支持家族自门控归零），其余场景恒一图。
        """
        if issubclass(cls, _LayerOptionsInput):
            return cls.per_request_image_count(
                image_count=0,
                max_images=None,
                model_id=model_id,
                layer_max_images=LAYER_DECOMPOSITION_MAX_IMAGES,
            )
        if issubclass(cls, _SequentialOptionsInput):
            return cls.per_request_image_count(
                image_count=0,
                max_images=MAX_SEQUENTIAL_TOTAL_IMAGES,
                model_id=model_id,
            )
        return cls.per_request_image_count(
            image_count=0,
            max_images=None,
            model_id=model_id,
        )


class TextToImageInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _OutputFormatInput,
    _ToolsInput,
    _WatermarkInput,
    _BackgroundInput,
    _SizeInput,
    _PromptAndOptimizeInput,
):
    """文生图：通过提供清晰准确的文字指令，即可快速获得符合描述的高质量单张图片。"""

    def reference_images(self) -> list[str] | None:
        """场景声明：文生图无参考图。"""
        return None


class ImageToImageInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _OutputFormatInput,
    _ToolsInput,
    _WatermarkInput,
    _BackgroundInput,
    _SizeInput,
    _SingleImageInput,
    _PromptAndOptimizeInput,
):
    """图文生图：基于已有图片，结合文字指令进行图像编辑。

    支持图像元素增删、风格转化、材质替换、色调迁移、改变背景/视角/尺寸等操作。
    """

    # 覆盖共享基类的 prompt 描述以表达编辑指令语义，覆盖声明不改变字段顺序。
    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description=IMAGE_TO_IMAGE_PROMPT_DESCRIPTION,
    )


class MultiImageFusionInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _OutputFormatInput,
    _ToolsInput,
    _WatermarkInput,
    _BackgroundInput,
    _SizeInput,
    _MultiImageInput,
    _PromptAndOptimizeInput,
):
    """多图融合：根据文本描述与多张参考图片，融合风格与元素生成新图像。

    如衣裤鞋帽与模特图融合成穿搭图，人物与风景融合为人物风景图等。
    """

    # 覆盖共享基类的 prompt 描述以表达融合场景语义，覆盖声明不改变字段顺序。
    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description=MULTI_IMAGE_FUSION_PROMPT_DESCRIPTION,
    )


class SequentialGenerationInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _OutputFormatInput,
    _ToolsInput,
    _WatermarkInput,
    _BackgroundInput,
    _SizeInput,
    _SequentialOptionsInput,
    _SequentialImageInput,
    _PromptAndOptimizeInput,
):
    """组图输出：以一张或多张参考图与文字信息，生成一组内容关联的图片。

    支持漫画分镜、品牌视觉等场景；request_count 表示并行生成多组独立的组图结果，
    而非扩大单组内的图片数量；单组图片数量由组图配置的 max_images 控制，二者相互独立。
    """

    # 覆盖共享基类的 prompt 描述以表达组图提示语义，覆盖声明不改变字段顺序。
    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description=SEQUENTIAL_PROMPT_DESCRIPTION,
    )

    # 覆盖共享基类的 request_count 描述：单次请求产出一组图片而非单张，共享描述的
    # 「每次各产出一张图」在本工具不成立；覆盖声明不改变字段顺序。
    request_count: IntegerType = Field(
        default=1,
        ge=REQUEST_COUNT_MIN,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description=REQUEST_COUNT_SEQUENTIAL_DESCRIPTION,
    )

    @model_validator(mode="after")
    def validate_total_image_limit(self) -> "SequentialGenerationInput":
        """校验参考图数量与生成数量的总和限制。"""
        # before-validator 已归一，运行时值恒为 list[str] | None，cast 收窄供传参。
        images = cast("list[str] | None", self.image)
        # 推导不回写 max_images，显式与否经 options 字段值本身区分。
        try:
            validate_sequential_image_limit(images, self.effective_max_images)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc
        return self

    @property
    def effective_max_images(self) -> int:
        """生效的生成数量上限：显式值透传，缺省按参考图张数推导。"""
        images = cast("list[str] | None", self.image)
        options = self.sequential_image_generation_options
        explicit = options.max_images if options is not None else None
        return resolve_sequential_max_images(len(images or []), explicit)


class LayerDecomposeInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _OutputFormatInput,
    _ToolsInput,
    _WatermarkInput,
    _SizeInput,
    _LayerOptionsInput,
    _SingleImageInput,
    _PromptAndOptimizeInput,
):
    """图层拆分：将单张图片拆解为 1 张底图与多个带透明通道的 PNG 图层。

    需当前模型支持；图层条目额外返回叠放顺序、名称、描述与边界框，
    支持 5.5 的拆分数量控制与富文本 OCR。
    """

    # 图层拆分意图可缺省；基类锚定为必填 str 以固定字段顺序，本类宽化覆盖为可选，
    # 缺省放行由 client 公共校验的 layer_decomposition 场景参数承担。
    prompt: str | None = Field(  # type: ignore[assignment]
        default=None,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description=LAYER_DECOMPOSE_PROMPT_DESCRIPTION,
    )

    # 覆盖共享基类的 image 描述以给出图层结果的保存子目录，覆盖声明不改变字段顺序。
    image: str = Field(
        ...,
        description=LAYER_DECOMPOSE_IMAGE_DESCRIPTION,
    )

    # 覆盖共享基类的 size 描述以表达档位与 auto 语义，覆盖声明不改变字段顺序。
    size: str | None = Field(
        default=None,
        description=SIZE_WITH_AUTO_DESCRIPTION,
    )

    # 覆盖共享基类的 request_count 描述：单次请求产出一组「底图与图层」而非单张，
    # 共享描述的「每次各产出一张图」在本工具不成立；覆盖声明不改变字段顺序。
    request_count: IntegerType = Field(
        default=1,
        ge=REQUEST_COUNT_MIN,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description=REQUEST_COUNT_LAYER_DESCRIPTION,
    )


def _normalize_format_filter_item(value: object) -> object:
    """strip 与小写归一并补齐缺失的点前缀，空白条目拒绝。"""

    if not isinstance(value, str):
        return value
    cleaned = value.strip().lower()
    if not cleaned:
        raise ValueError("format_filter 条目不能为空白")
    if not cleaned.startswith("."):
        cleaned = f".{cleaned}"
    return cleaned


class BrowseImagesInput(BaseModel):
    """本地图片浏览：浏览工作目录中的图片文件，便于选择参考图或查看已生成内容。

    默认值以 ClassVar 声明为单一来源。
    """

    DEFAULT_RECURSIVE: ClassVar[bool] = True
    DEFAULT_MAX_DEPTH: ClassVar[int] = 3
    DEFAULT_LIMIT: ClassVar[int] = 50
    DEFAULT_OFFSET: ClassVar[int] = 0
    DEFAULT_SHOW_DETAILS: ClassVar[bool] = False

    @property
    def effective_directory(self) -> str:
        """浏览目录缺省与空串归一为 "."。"""
        return self.directory if self.directory else "."

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    directory: str | None = Field(
        default=None,
        max_length=DIRECTORY_MAX_LENGTH,
        description=DIRECTORY_DESCRIPTION,
    )
    recursive: bool = Field(
        default=DEFAULT_RECURSIVE,
        description=RECURSIVE_DESCRIPTION,
    )
    max_depth: IntegerType = Field(
        default=DEFAULT_MAX_DEPTH,
        ge=MAX_DEPTH_MIN,
        le=MAX_DEPTH_MAX,
        description=MAX_DEPTH_DESCRIPTION,
    )
    limit: IntegerType = Field(
        default=DEFAULT_LIMIT,
        ge=LIMIT_MIN,
        le=LIMIT_MAX,
        description=LIMIT_DESCRIPTION,
    )
    offset: IntegerType = Field(
        default=DEFAULT_OFFSET,
        ge=OFFSET_MIN,
        le=OFFSET_MAX,
        description=OFFSET_DESCRIPTION,
    )
    # 条目经 before-validator 归一后入核心 schema，maxLength 校验最终存储形态；
    # 对外发布的 maxLength 对无点原始条目仍偏宽一位，客户端无法预知服务端补点。
    format_filter: (
        list[
            Annotated[
                str,
                Field(max_length=FORMAT_FILTER_ITEM_MAX_LENGTH),
                BeforeValidator(_normalize_format_filter_item),
            ]
        ]
        | None
    ) = Field(
        default=None,
        max_length=FORMAT_FILTER_MAX_ITEMS,
        description=FORMAT_FILTER_DESCRIPTION,
    )
    show_details: bool = Field(
        default=DEFAULT_SHOW_DETAILS,
        description=SHOW_DETAILS_DESCRIPTION,
    )


@dataclass(frozen=True)
class EntryBound:
    """浏览请求目录与条目共用的已 resolve 替代界。

    Attributes:
        path: 界路径，词法形态违约在构造期抛 ValueError，判定单源于
            resolved_form_violation（冒号与 Windows 保留名仅 win32 拒绝）。
        denial_message: 请求目录越出界时的同界门拒绝文案（ErrorText），界语义口径由声明方自带。
    """

    path: Path
    denial_message: ErrorText

    def __post_init__(self) -> None:
        """构造门与 AutoSaveResult 同类：坏形态界路径与读权限求交时静默失配产出空结果，
        构造期即拒绝；违约形态判定随 resolved_form_violation 平台口径单源演化。"""
        if resolved_form_violation(str(self.path)) is not None:
            raise ValueError(
                raw_error_text(
                    "EntryBound.path 须为已 resolve 的绝对路径: {path}",
                    path=as_text(self.path),
                )
            )


# 生成工具输入模型注册：Web 等待预算最坏候选等按全量派生，新增生成工具须登记，
# 名单由 test_generation_input_models_registry_matches_five_tools 对账。
GENERATION_INPUT_MODELS: tuple[type[BaseGenerationInput], ...] = (
    TextToImageInput,
    ImageToImageInput,
    MultiImageFusionInput,
    LayerDecomposeInput,
    SequentialGenerationInput,
)


class GenerationInputParams(Protocol):
    """五个生成工具输入模型共享字段的协议类型。

    各工具的具体输入模型经结构化子类型自动满足本协议，无需显式继承。
    """

    # prompt 经只读 property 声明：各工具实现分别为 str 或 str | None，只读协变
    # 使各类实现均满足本协议。
    @property
    def prompt(self) -> str | None: ...

    optimize_prompt_options: OptimizePromptOptions | None
    size: str | None
    watermark: bool | None
    tools: list[GenerationTool] | None
    output_format: OutputFormat | None
    response_format: ResponseFormat
    stream: bool
    request_count: int
    parallelism: int | None
    auto_save: bool | None
    save_path: str | None
    custom_name: str | None

    # 参考图场景声明：输入模型经 image mixin 或自身实现（文生图为 None），
    # context 构建消费，未声明的工具调用即 AttributeError 而非静默跳过数量校验。
    def reference_images(self) -> list[str] | None: ...
