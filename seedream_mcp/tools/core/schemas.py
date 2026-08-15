"""Seedream MCP 工具输入模型。

作为参数校验与 MCP inputSchema 的单一来源：FastMCP 依据本模块的 pydantic 模型生成
各工具入参 schema，impl 层 handler 不重复描述字段规则。通用字段抽到 ``_*Input`` 基类
按需多重继承组合为各工具的最终输入模型。
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...utils.core.errors import SeedreamValidationError
from ...utils.model.model_capabilities import SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
from ...utils.core.validators import (
    MAX_PARALLEL_REQUEST_COUNT,
    MAX_SEQUENTIAL_TOTAL_IMAGES,
    VALID_OPTIMIZE_MODES,
    resolve_sequential_max_images,
    validate_parallel_generation_options,
    validate_sequential_image_limit,
)

# prompt 字段长度约束，四个生成工具共享，集中声明避免散落多处。
PROMPT_MIN_LENGTH = 1
PROMPT_MAX_LENGTH = 100000


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


class OptimizePromptOptions(BaseModel):
    """提示词优化配置模型。

    配置提示词优化策略，平衡生成质量与响应速度。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: str = Field(
        default="standard",
        description="提示词优化模式：standard 高质量（全模型），fast 优先速度（仅 4.0 支持）。",
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        """校验并规范化优化模式。

        Args:
            value: 用户输入的优化模式字符串。

        Returns:
            规范化后的模式值。

        Raises:
            ValueError: 模式不在允许范围内。
        """
        normalized = value.strip().lower()
        if normalized not in VALID_OPTIMIZE_MODES:
            raise ValueError(f"mode 仅支持 {sorted(VALID_OPTIMIZE_MODES)}")
        return normalized


class GenerationTool(BaseModel):
    """模型工具配置。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: GenerationToolType = Field(
        ...,
        description="工具类型，目前仅支持 web_search。",
    )


class _PromptAndOptimizeInput(BaseModel):
    """提示词与提示词优化参数。"""

    # prompt 在基类声明以确立字段顺序：模型字段顺序是 server.py 平铺签名的镜像来源，
    # prompt 须居首，平铺契约的等价性由 test_tool_call_assembly 锁定。
    # 基类定义仅锚定字段顺序，长度约束与描述以各子类的覆盖为准；子类覆盖 prompt 时复用
    # 同一长度常量，避免约束散落多处。
    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description="用于生成图片的提示词，建议不超过300个汉字或600个英文单词。例如：一只戴墨镜的猫坐在月球上，写实风格。",
    )
    optimize_prompt_options: OptimizePromptOptions | None = Field(
        default=None,
        description="提示词优化配置，仅支持 standard 或 fast。",
    )


class _SingleImageInput(BaseModel):
    """单图输入参数。"""

    image: str = Field(
        ...,
        description="参考图片，支持 URL、data URI（data:image/*;base64,...）、本地文件路径。"
        "例如：https://example.com/ref.png 或 ./images/portrait.jpg。",
    )

    @field_validator("image")
    @classmethod
    def reject_blank_image(cls, value: str) -> str:
        """拒绝空白字符串，与多图输入的校验深度一致。

        空白值若放行到 client 归一化层才报错，错误会从 schema 级参数错误退化为
        isError 工具结果，报错层次与其他参数不一致。
        """
        if not value.strip():
            raise ValueError("image 不能为空字符串")
        return value


class _MultiImageInput(BaseModel):
    """多图输入参数。"""

    image: list[str] = Field(
        ...,
        min_length=2,
        max_length=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
        description=(
            f"图片列表，支持 URL、data URI（data:image/*;base64,...）或本地路径，"
            f"数量 2-{SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES} 张（5.0 Pro 最多 10 张）。"
            '例如：["https://example.com/a.png", "./images/b.jpg"]。'
        ),
    )

    @field_validator("image")
    @classmethod
    def reject_blank_items(cls, value: list[str]) -> list[str]:
        """逐项拒绝空白字符串，与组图输入的校验深度一致。

        空白项若放行到 client 归一化层才报错，错误会从 schema 级参数错误退化为
        isError 工具结果，报错层次与文案与其他参数不一致。
        """
        if any(not item.strip() for item in value):
            raise ValueError("image 列表中的每一项都必须是非空字符串")
        return value


class _SequentialImageInput(BaseModel):
    """组图参考图输入参数。"""

    # 运行时接受单字符串并经 before-validator 归一为单元素列表，声明与该行为一致。
    image: str | list[str] | None = Field(
        default=None,
        description=(
            f"可选的参考图片，支持 URL、data URI（data:image/*;base64,...）或本地路径，"
            f"单张或多张，单字符串视为单元素列表，最多 {SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES} 张。"
        ),
    )


class _SizeAndWatermarkInput(BaseModel):
    """尺寸与水印参数。"""

    size: str | None = Field(
        default=None,
        description="生成图片尺寸，可选 1K/2K/3K/4K 或 <宽>x<高> 像素值；未提供时使用全局默认值。例如：2K 或 1920x1080。",
    )
    watermark: bool | None = Field(
        default=None,
        description="是否添加水印；未提供时沿用全局默认值。",
    )


class _SequentialMaxImagesInput(BaseModel):
    """组图最大生成数量参数。"""

    max_images: int = Field(
        default=MAX_SEQUENTIAL_TOTAL_IMAGES,
        ge=1,
        le=MAX_SEQUENTIAL_TOTAL_IMAGES,
        description=f"本次请求允许生成的最大图片数量，范围 1-{MAX_SEQUENTIAL_TOTAL_IMAGES}。",
    )


class _ResponseAndExecutionInput(BaseModel):
    """响应格式、执行策略与自动保存参数。"""

    response_format: ResponseFormat = Field(
        default=ResponseFormat.URL,
        description="响应格式，url 返回可下载链接，b64_json 返回 base64 数据。",
    )
    output_format: OutputFormat | None = Field(
        default=None,
        description="输出图片格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 jpeg 或 png。",
    )
    stream: bool = Field(
        default=False,
        description="是否启用流式输出；开启后将以事件流返回生成进度（5.0 Pro 不支持）。",
    )
    tools: list[GenerationTool] | None = Field(
        default=None,
        description="模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持联网搜索（web_search）。",
    )
    request_count: int = Field(
        default=1,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="并行请求次数，1 表示单次请求；可用于一次发起多次生成以减少等待。",
    )
    parallelism: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="并行度上限；未提供时自动使用 min(request_count, 并行上限)。",
    )
    auto_save: bool | None = Field(
        default=None,
        description="是否自动保存到本地；未提供时遵循全局配置。",
    )
    save_path: str | None = Field(
        default=None,
        max_length=1024,
        description="自定义保存目录，未提供时使用自动保存配置的默认路径。",
    )
    custom_name: str | None = Field(
        default=None,
        max_length=255,
        description="自定义文件名前缀，未提供时根据提示词自动生成。",
    )


class BaseGenerationInput(BaseModel):
    """图片生成工具的通用输入校验基类。

    仅提供共享模型配置与校验逻辑，具体字段顺序由各工具输入模型定义。
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    @field_validator("save_path", "custom_name", check_fields=False)
    @classmethod
    def validate_non_empty(cls, value: str | None) -> str | None:
        """校验字符串字段非空。

        Args:
            value: 待校验的字符串值。

        Returns:
            去除首尾空格后的字符串，None 时跳过校验。

        Raises:
            ValueError: 字符串为空或仅含空格。
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("该字段不能为空字符串")
        return normalized

    @model_validator(mode="after")
    def validate_parallel_options(self) -> "BaseGenerationInput":
        """校验并行执行参数组合。"""
        request_count = getattr(self, "request_count", 1)
        parallelism = getattr(self, "parallelism", None)
        stream = bool(getattr(self, "stream", False))
        try:
            validate_parallel_generation_options(
                request_count=request_count,
                parallelism=parallelism,
                stream=stream,
                max_request_count=MAX_PARALLEL_REQUEST_COUNT,
            )
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc
        return self


class TextToImageInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _SizeAndWatermarkInput,
    _PromptAndOptimizeInput,
):
    """文生图：通过提供清晰准确的文字指令，即可快速获得符合描述的高质量单张图片。"""

    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description="用于生成图片的提示词，建议不超过300个汉字或600个英文单词。例如：一只戴墨镜的猫坐在月球上，写实风格。",
    )


class ImageToImageInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _SizeAndWatermarkInput,
    _SingleImageInput,
    _PromptAndOptimizeInput,
):
    """图文生图：基于已有图片，结合文字指令进行图像编辑，包括图像元素增删、风格转化、材质替换、色调迁移、改变背景/视角/尺寸等。"""

    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description="图片修改或风格转换的指令，建议不超过300个汉字或600个英文单词。例如：把背景换成雪山、将照片转为水彩画风格。",
    )


class MultiImageFusionInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _SizeAndWatermarkInput,
    _MultiImageInput,
    _PromptAndOptimizeInput,
):
    """多图融合：根据输入的文本描述和多张参考图片，融合它们的风格、元素等特征来生成新图像。如衣裤鞋帽与模特图融合成穿搭图，人物与风景融合为人物风景图等。"""

    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description="融合目标或风格描述，建议不超过300个汉字或600个英文单词。请使用“图X”指定图像（如：将图1的服装换为图2的服装）。",
    )


class SequentialGenerationInput(
    BaseGenerationInput,
    _ResponseAndExecutionInput,
    _SequentialMaxImagesInput,
    _SizeAndWatermarkInput,
    _SequentialImageInput,
    _PromptAndOptimizeInput,
):
    """组图输出：支持通过一张或者多张图片和文字信息，生成漫画分镜、品牌视觉等一组内容关联的图片。

    request_count 表示并行生成多组独立的组图结果，而非扩大单组内的图片数量；单组图片数量
    由 max_images 控制，二者相互独立。
    """

    prompt: str = Field(
        ...,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        description="连贯的组图提示，需明确数量与内容，不超过300个汉字或600个英文单词。例如：生成4格漫画分镜，主角是戴红帽子的女孩，依次出现在咖啡馆、街道、公园、家中。",
    )

    @field_validator("image", mode="before")
    @classmethod
    def validate_reference_images(cls, value: str | list[str] | None) -> list[str] | None:
        """校验参考图片列表。

        Args:
            value: 单张图片或图片列表，None 时跳过校验。

        Returns:
            规范化后的图片列表，None 时返回 None。

        Raises:
            ValueError: 图片数量或格式不符合要求。
        """
        if value is None:
            return None
        if isinstance(value, str):
            images = [value]
        else:
            images = value

        if not isinstance(images, list):
            raise ValueError("image 必须是字符串或字符串列表")
        if len(images) < 1 or len(images) > SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES:
            raise ValueError(f"参考图片数量需在 1-{SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES} 之间")

        normalized: list[str] = []
        for item in images:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("image 列表中的每一项都必须是非空字符串")
            normalized.append(item.strip())
        return normalized

    @model_validator(mode="after")
    def validate_total_image_limit(self) -> "SequentialGenerationInput":
        """校验参考图数量与生成数量的总和限制。"""
        # 字段声明含 str 形态以对齐 inputSchema，但 before-validator 已把单字符串归一为
        # 列表，after 阶段的运行时值恒为 list[str] | None，此处收窄供下游计数消费。
        images = cast("list[str] | None", self.image)
        # max_images 未显式传入时，按参考图数量自动推导，取生成总上限减去参考图数量。
        # 派生写入用 object.__setattr__ 绕过 validate_assignment 并从 model_fields_set
        # 剔除：普通赋值会把派生值登记进 fields_set 且再触发一轮本 after-validator，
        # 使派生与显式传入不可区分，误导依据 fields_set 判断显式传入的逻辑（如
        # exclude_unset 序列化与审计）。
        if "max_images" not in self.model_fields_set:
            object.__setattr__(self, "max_images", resolve_sequential_max_images(None, images))
            self.__pydantic_fields_set__.discard("max_images")

        try:
            validate_sequential_image_limit(self.max_images, images)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc
        return self


class BrowseImagesInput(BaseModel):
    """本地图片浏览：浏览工作目录中的图片文件，便于用户选择参考图或查看已生成内容。

    字段默认值的单一来源：各字段 Field 默认值引用类上声明的 ClassVar 常量，避免魔法值
    漂移；impl handler 以类型化属性读取，缺省字段直接携带默认值。
    """

    # ClassVar 声明使 pydantic 将其排除出模型字段，仅作为默认值单一来源供 Field 引用。
    DEFAULT_RECURSIVE: ClassVar[bool] = True
    DEFAULT_MAX_DEPTH: ClassVar[int] = 3
    DEFAULT_LIMIT: ClassVar[int] = 50
    DEFAULT_OFFSET: ClassVar[int] = 0
    DEFAULT_SHOW_DETAILS: ClassVar[bool] = False

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    directory: str | None = Field(
        default=None,
        description=(
            "要浏览的目录路径，默认浏览工作区根目录，即 MCP Roots 授权的首个根；"
            "无 Roots 时回退 SEEDREAM_WORKSPACE_ROOT 配置的本地工作区根，"
            "均未设置时回退进程当前工作目录。"
        ),
    )
    recursive: bool = Field(
        default=DEFAULT_RECURSIVE,
        description="是否递归查找子目录。",
    )
    max_depth: int = Field(
        default=DEFAULT_MAX_DEPTH,
        ge=1,
        le=10,
        description="递归查找的最大深度（1-10）。",
    )
    limit: int = Field(
        default=DEFAULT_LIMIT,
        ge=1,
        le=200,
        description="返回的最大文件数量（1-200）。",
    )
    offset: int = Field(
        default=DEFAULT_OFFSET,
        ge=0,
        le=100000,
        description="分页偏移量（从第几张开始返回，0-100000），默认 0；配合 limit 翻页。"
        "上限防止无界偏移触发全量扫描。",
    )
    format_filter: list[str] | None = Field(
        default=None,
        description=(
            "需要过滤的图片后缀列表，如 ['.jpeg', '.png']；仅保留受支持的后缀。"
            "空列表或全部后缀不受支持时视为无有效后缀：跳过扫描返回空结果并回显原始输入。"
        ),
    )
    show_details: bool = Field(
        default=DEFAULT_SHOW_DETAILS,
        description="是否展示文件大小、修改时间等详细信息。",
    )

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: str | None) -> str | None:
        """校验目录路径。

        Args:
            value: 用户指定的目录路径。

        Returns:
            规范化后的路径，None 时跳过校验。

        Raises:
            ValueError: 路径为空字符串。
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("目录路径不能为空字符串")
        return normalized

    @field_validator("format_filter")
    @classmethod
    def normalize_suffixes(cls, value: list[str] | None) -> list[str] | None:
        """规范化文件后缀列表。

        Args:
            value: 用户提供的后缀列表。

        Returns:
            规范化后的后缀列表，统一小写并补齐点前缀；输入为 None 时返回 None。
        """
        if value is None:
            return None
        normalized = []
        for suffix in value:
            cleaned = suffix.strip().lower()
            if not cleaned.startswith("."):
                cleaned = f".{cleaned}"
            normalized.append(cleaned)
        return normalized


class GenerationInputParams(Protocol):
    """四个生成工具输入模型共享字段的协议类型。

    供 core 流水线以类型化属性访问读取共享字段，替代弱类型 dict.get；各生成工具的
    具体输入模型经结构化子类型自动满足本协议，无需显式继承。
    """

    prompt: str
    optimize_prompt_options: OptimizePromptOptions | None
    size: str | None
    watermark: bool | None
    response_format: ResponseFormat
    output_format: OutputFormat | None
    stream: bool
    tools: list[GenerationTool] | None
    request_count: int
    parallelism: int | None
    auto_save: bool | None
    save_path: str | None
    custom_name: str | None
