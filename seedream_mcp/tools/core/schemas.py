"""
Seedream MCP 工具输入模型

使用 Pydantic 对 MCP 工具的输入参数进行结构化校验，确保参数约束清晰且具备可读的错误提示。
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...utils.errors import SeedreamValidationError
from ...utils.validation import (
    validate_image_list,
    validate_image_url,
    validate_max_images,
    validate_prompt,
    validate_size,
)


class ResponseFormat(str, Enum):
    """
    图片生成响应格式枚举。

    定义生成结果的返回格式，支持 URL 链接和 Base64 编码两种方式。
    """

    URL = "url"
    B64_JSON = "b64_json"


class OptimizePromptOptions(BaseModel):
    """
    提示词优化配置模型。

    配置提示词优化策略，平衡生成质量与响应速度。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: str = Field(
        default="standard",
        description="提示词优化模式，standard 提供高质量优化，fast 优先速度。",
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        """
        校验并规范化优化模式。

        Args:
            value: 用户输入的优化模式字符串

        Returns:
            规范化后的模式值（小写）

        Raises:
            ValueError: 当模式不在允许范围内时
        """
        normalized = value.strip().lower()
        allowed = {"standard", "fast"}
        if normalized not in allowed:
            raise ValueError(f"mode 仅支持 {sorted(allowed)}")
        return normalized


class BaseGenerationInput(BaseModel):
    """
    图片生成工具的通用输入基类。

    定义所有图片生成类工具共享的配置参数，包括尺寸、水印、响应格式、
    流式输出、提示词优化及自动保存等功能。
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    size: Optional[str] = Field(
        default=None,
        description="生成图片尺寸，可选 1K/2K/4K；未提供时使用全局默认值。",
    )
    watermark: Optional[bool] = Field(
        default=None,
        description="是否添加水印；未提供时沿用全局默认值。",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.URL,
        description="响应格式，url 返回可下载链接，b64_json 返回 base64 数据。",
    )
    stream: bool = Field(
        default=False,
        description="是否启用流式输出；开启后将以事件流返回生成进度。",
    )
    optimize_prompt_options: Optional[OptimizePromptOptions] = Field(
        default=None,
        description="提示词优化配置，仅支持 standard 或 fast。",
    )
    auto_save: Optional[bool] = Field(
        default=None,
        description="是否自动保存到本地；未提供时遵循全局配置。",
    )
    save_path: Optional[str] = Field(
        default=None,
        description="自定义保存目录，未提供时使用自动保存配置的默认路径。",
    )
    custom_name: Optional[str] = Field(
        default=None,
        description="自定义文件名前缀，未提供时根据提示词自动生成。",
    )

    @field_validator("size")
    @classmethod
    def validate_size_field(cls, value: Optional[str]) -> Optional[str]:
        """
        校验图片尺寸参数。

        Args:
            value: 用户指定的尺寸值

        Returns:
            规范化后的尺寸值，None 时跳过校验

        Raises:
            ValueError: 当尺寸格式不符合要求时
        """
        if value is None:
            return value
        try:
            return validate_size(value)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("save_path", "custom_name")
    @classmethod
    def validate_non_empty(cls, value: Optional[str]) -> Optional[str]:
        """
        校验字符串字段非空。

        Args:
            value: 待校验的字符串值

        Returns:
            去除首尾空格后的字符串，None 时跳过校验

        Raises:
            ValueError: 当字符串为空或仅含空格时
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("该字段不能为空字符串")
        return normalized


class TextToImageInput(BaseGenerationInput):
    """
    文生图：通过提供清晰准确的文字指令，即可快速获得符合描述的高质量单张图片。
    """

    prompt: str = Field(
        ...,
        min_length=1,
        description="用于生成图片的提示词，建议不超过300个汉字或600个英文单词。",
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt_field(cls, value: str) -> str:
        """
        校验并规范化提示词。

        Args:
            value: 用户输入的提示词

        Returns:
            规范化后的提示词

        Raises:
            ValueError: 当提示词格式或长度不符合要求时
        """
        try:
            return validate_prompt(value)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc


class ImageToImageInput(BaseGenerationInput):
    """
    图文生图：基于已有图片，结合文字指令进行图像编辑，包括图像元素增删、风格转化、材质替换、色调迁移、改变背景/视角/尺寸等。
    """

    prompt: str = Field(
        ...,
        min_length=1,
        description="图片修改或风格转换的指令，建议不超过300个汉字或600个英文单词。",
    )
    image: str = Field(
        ...,
        description="待转换的图片，支持 URL、本地文件路径。",
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt_field(cls, value: str) -> str:
        """
        校验并规范化提示词。

        Args:
            value: 用户输入的提示词

        Returns:
            规范化后的提示词

        Raises:
            ValueError: 当提示词格式或长度不符合要求时
        """
        try:
            return validate_prompt(value)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("image")
    @classmethod
    def validate_image_field(cls, value: str) -> str:
        """
        校验图片来源。

        Args:
            value: 图片 URL、文件路径

        Returns:
            规范化后的图片标识

        Raises:
            ValueError: 当图片来源格式不合法时
        """
        try:
            return validate_image_url(value)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc


class MultiImageFusionInput(BaseGenerationInput):
    """
    多图融合：根据输入的文本描述和多张参考图片，融合它们的风格、元素等特征来生成新图像。如衣裤鞋帽与模特图融合成穿搭图，人物与风景融合为人物风景图等。
    """

    prompt: str = Field(
        ...,
        min_length=1,
        description="融合目标或风格描述，建议不超过300个汉字或600个英文单词。请使用“图X”指定图像（如：将图1的服装换为图2的服装）。",
    )
    image: List[str] = Field(
        ...,
        min_items=2,
        max_items=5,
        description="参与融合的图片列表，支持 URL、本地路径，数量2-5张。",
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt_field(cls, value: str) -> str:
        """
        校验并规范化提示词。

        Args:
            value: 用户输入的提示词

        Returns:
            规范化后的提示词

        Raises:
            ValueError: 当提示词格式或长度不符合要求时
        """
        try:
            return validate_prompt(value)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("image")
    @classmethod
    def validate_images_field(cls, value: List[str]) -> List[str]:
        """
        校验图片列表。

        Args:
            value: 图片来源列表

        Returns:
            规范化后的图片列表

        Raises:
            ValueError: 当图片数量或格式不符合要求时
        """
        try:
            return validate_image_list(value, min_count=2, max_count=5)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc


class SequentialGenerationInput(BaseGenerationInput):
    """
    组图输出：支持通过一张或者多张图片和文字信息，生成漫画分镜、品牌视觉等一组内容关联的图片。
    """

    prompt: str = Field(
        ...,
        min_length=1,
        description="连贯的组图提示，需明确数量与内容，不超过300个汉字或600个英文单词。",
    )
    max_images: int = Field(
        default=4,
        ge=1,
        le=15,
        description="本次请求允许生成的最大图片数量，范围 1-15。",
    )
    image: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="可选的参考图片，支持单张或多张。",
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt_field(cls, value: str) -> str:
        """
        校验并规范化提示词。

        Args:
            value: 用户输入的提示词

        Returns:
            规范化后的提示词

        Raises:
            ValueError: 当提示词格式或长度不符合要求时
        """
        try:
            return validate_prompt(value)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("max_images")
    @classmethod
    def validate_max_images_field(cls, value: int) -> int:
        """
        校验最大图片数量。

        Args:
            value: 用户指定的最大生成数量

        Returns:
            校验通过的数量值

        Raises:
            ValueError: 当数量超出允许范围时
        """
        try:
            return validate_max_images(value)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc

    @field_validator("image")
    @classmethod
    def validate_reference_images(
        cls, value: Optional[Union[str, List[str]]]
    ) -> Optional[List[str]]:
        """
        校验参考图片列表。

        Args:
            value: 单张图片或图片列表，None 时跳过校验

        Returns:
            规范化后的图片列表，None 时返回 None

        Raises:
            ValueError: 当图片数量或格式不符合要求时
        """
        if value is None:
            return None
        if isinstance(value, str):
            images = [value]
        else:
            images = value
        try:
            return validate_image_list(images, min_count=1, max_count=5)
        except SeedreamValidationError as exc:
            raise ValueError(exc.message) from exc


class BrowseImagesInput(BaseModel):
    """
    本地图片浏览：浏览工作目录中的图片文件，便于用户选择参考图或查看已生成内容。
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    directory: Optional[str] = Field(
        default=None,
        description="要浏览的目录路径，默认使用当前工作目录。",
    )
    recursive: bool = Field(
        default=True,
        description="是否递归查找子目录。",
    )
    max_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description="递归查找的最大深度（1-10）。",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="返回的最大文件数量（1-200）。",
    )
    format_filter: Optional[List[str]] = Field(
        default=None,
        description="需要过滤的图片后缀列表，如 ['.jpeg', '.png']。",
    )
    show_details: bool = Field(
        default=False,
        description="是否展示文件大小、修改时间等详细信息。",
    )

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: Optional[str]) -> Optional[str]:
        """
        校验目录路径。

        Args:
            value: 用户指定的目录路径

        Returns:
            规范化后的路径，None 时跳过校验

        Raises:
            ValueError: 当路径为空字符串时
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("目录路径不能为空字符串")
        return normalized

    @field_validator("format_filter")
    @classmethod
    def normalize_suffixes(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        """
        规范化文件后缀列表。

        Args:
            value: 用户提供的后缀列表

        Returns:
            规范化后的后缀列表（小写，含点前缀），None 时跳过
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

    @model_validator(mode="after")
    def validate_limits(self) -> "BrowseImagesInput":
        """
        校验数量限制参数的逻辑一致性。

        Returns:
            校验通过的模型实例

        Raises:
            ValueError: 当 limit 小于 1 时
        """
        if self.limit < 1:
            raise ValueError("limit 必须大于等于 1")
        return self
