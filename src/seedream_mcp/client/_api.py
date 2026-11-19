"""Seedream MCP 客户端模块。

定义 :class:`SeedreamClient`，封装火山引擎 Seedream 系列图像生成 API 的调用。
该类同时作为公共库 API 与 MCP 工具后端，提供文生图、图文生图、多图融合、
组图输出四种入口，入口处重新校验参数，调用方无需预校验。HTTP 传输与响应
归一化机械位于 ``_http``，批内共享请求计划位于 ``request_plan``。
"""

import asyncio
import hashlib
import re
import types
from collections.abc import Sequence
from typing import Any

import httpx

from ._http import _ClientHTTPMixin
from ..config import SeedreamConfig, get_active_config
from ..definitions.conventions import (
    IMAGE_TO_IMAGE_TOOL_NAME,
    MULTI_IMAGE_FUSION_TOOL_NAME,
    SEQUENTIAL_GENERATION_TOOL_NAME,
    TEXT_TO_IMAGE_TOOL_NAME,
)
from .request_plan import _ACTIVE_REQUEST_PLAN, _build_request_data
from ..utils.core.errors import SeedreamValidationError, as_plain, error_text, raw_error_text
from ..utils.core.logs import get_logger
from ..utils.core.safety import resolve_safety_identifier
from ..utils.model.capabilities import (
    SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
    get_model_capabilities,
    layer_decomposition_layer_limits_text,
    layer_decomposition_presets_text,
    limited_reference_families_annotation,
    supported_family_display_names,
)
from ..utils.core.validators import (
    VALID_BACKGROUND_MODES,
    ValidatedCommonParams,
    ensure_utf8_encodable,
    resolve_effective_generation_defaults,
    resolve_sequential_max_images,
    validate_background,
    validate_common_generation_params,
    validate_layer_decomposition,
    validate_layer_decomposition_options,
    validate_max_images,
    validate_reference_image_count,
    validate_sequential_generation_support,
    validate_sequential_image_generation_options,
    validate_sequential_image_limit,
)
from ..utils.images.prepare import ImagePreparer

# 生成方法 docstring 的家族清单、图层档位、图层数上限与参考图上限自能力表派生；
# docstring 为字符串字面量（f-string 不能成为 __doc__），派生值经下方回填替换进入。
# background 段落双变体：拆分子句仅图文生图可达，其余三方法无该场景；
# 其家族清单在定义时内插（段落是替换值而非 docstring 字面量），值保持平文本，
# 回填单遍替换即可完成。取值词表复用校验白名单（字母稳定序），新增模式随表更新。
_BACKGROUND_MODE_VALUES = sorted(VALID_BACKGROUND_MODES)
_BACKGROUND_MODES_DOC = (
    "、".join(f'"{mode}"' for mode in _BACKGROUND_MODE_VALUES[:-1])
    + f' 或 "{_BACKGROUND_MODE_VALUES[-1]}"'
)
_BACKGROUND_DOC_PREFIX = (
    f"background: 图片透明通道，取值为 {_BACKGROUND_MODES_DOC}，\n"
    "                可用取值以当前模型为准，仅 "
    f"{supported_family_display_names('allowed_background_modes')}"
    " 支持（transparent 在部分家族\n"
    "                仅单参考图的图生场景可用）；transparent 生成透明背景图（部分模型\n"
    '                要求输入图片带透明通道），与 output_format="jpeg" 互斥'
)
_BACKGROUND_DOC = _BACKGROUND_DOC_PREFIX + "。"
_BACKGROUND_DOC_DECOMPOSITION = (
    _BACKGROUND_DOC_PREFIX + "，图层拆分\n                场景不支持该参数。"
)

_DOCSTRING_HINT_PLACEHOLDERS = {
    "{output_format_families}": supported_family_display_names("supports_output_format"),
    "{stream_families}": supported_family_display_names("supports_stream"),
    "{tools_families}": supported_family_display_names("supports_tools"),
    "{sequential_families}": supported_family_display_names("supports_sequential_generation"),
    "{layer_families}": supported_family_display_names("supports_layer_decomposition"),
    "{layer_options_families}": supported_family_display_names(
        "supports_layer_decomposition_options"
    ),
    "{background_doc}": _BACKGROUND_DOC,
    "{background_doc_decomposition}": _BACKGROUND_DOC_DECOMPOSITION,
    "{reference_limit_annotation}": limited_reference_families_annotation(),
    "{layer_presets}": layer_decomposition_presets_text(),
    "{layer_limits}": layer_decomposition_layer_limits_text(),
    "{max_reference_images}": str(SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES),
}


def _fill_docstring_hints(docstring: str) -> str:
    """把占位符替换为能力表派生值。"""
    for placeholder, value in _DOCSTRING_HINT_PLACEHOLDERS.items():
        docstring = docstring.replace(placeholder, value)
    return docstring


# 残留守卫的嗅探形态：任意纯词元花括号，词典全部键均命中；生成方法 docstring 的
# 合法花括号仅 tools 示例（含引号与冒号，词元形态不匹配），回填后残留即报错。
_PLACEHOLDER_RESIDUE_PATTERN = re.compile(r"\{[A-Za-z0-9_]+\}")


class SeedreamClient(_ClientHTTPMixin):
    """Seedream API 客户端。

    各生成方法在入口重新校验参数，公共库 API 与 MCP 工具后端两种调用路径行为一致。
    四个生成方法保持同构的显式模板而非抽象为公共骨架：方法签名是公共库 API 的
    直读契约，图片字段归一化与请求差异以平铺代码表达，引入间接层只会转移而不会
    消除复杂度。

    仅模型支持时各生成请求自动附加 safety_identifier 终端用户标识，取值经部署方
    显式配置或由系统派生，是否附加由配置开关决定。

    Attributes:
        config: 客户端配置对象。
        logger: 日志记录器实例。
    """

    def __init__(self, config: SeedreamConfig | None = None):
        """初始化 Seedream API 客户端。

        Args:
            config: 配置对象，若为 None 则取当前活动配置。
        """
        self.config = config or get_active_config()
        self.logger = get_logger()
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._timeout: httpx.Timeout | None = None
        # 参考图预处理缓存子系统委托 ImagePreparer：LRU + 单飞去重。
        self._image_preparer = ImagePreparer(
            prepare_cache_max=self.config.prepare_cache_max,
            prepare_cache_max_bytes=self.config.prepare_cache_max_bytes,
            prepare_concurrency=self.config.image_prepare_concurrency,
        )

    async def __aenter__(self) -> "SeedreamClient":
        """进入异步上下文，确保 HTTP 客户端就绪并返回自身。"""
        await self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """退出异步上下文，关闭客户端连接并释放资源。"""
        await self.close()

    def _build_common_request(
        self,
        validated: ValidatedCommonParams,
        *,
        background: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建各生成方法共享的请求参数基础字典，方法特有字段经 extra 并入。

        prompt 为 None 时不写入该键，
        对应图层拆分场景的缺省提示词，由模型自动识别拆分意图。
        """
        request_data: dict[str, Any] = {
            "model": self.config.model_id,
        }
        # 仅能力声明支持的家族附加，避免向其余模型发参被 API 拒绝。
        safety_identifier = resolve_safety_identifier(
            enabled=self.config.safety_identifier_enabled,
            explicit_identifier=self.config.safety_identifier,
        )
        if (
            safety_identifier
            and get_model_capabilities(self.config.model_id).supports_safety_identifier
        ):
            request_data["safety_identifier"] = safety_identifier
        if validated.prompt is not None:
            request_data["prompt"] = validated.prompt
        if validated.optimize_prompt_options:
            request_data["optimize_prompt_options"] = validated.optimize_prompt_options
        update_payload: dict[str, Any] = {
            "size": validated.size,
            "watermark": validated.watermark,
            "response_format": validated.response_format,
        }
        if extra:
            update_payload.update(extra)
        request_data.update(update_payload)
        if validated.output_format is not None:
            request_data["output_format"] = validated.output_format
        if validated.stream:
            request_data["stream"] = True
        if validated.tools:
            request_data["tools"] = validated.tools
        if background is not None:
            request_data["background"] = background
        return request_data

    async def text_to_image(
        self,
        prompt: str | None = None,
        *,
        optimize_prompt_options: dict[str, Any] | None = None,
        size: str | None = None,
        background: str | None = None,
        watermark: bool | None = None,
        tools: list[dict[str, Any]] | None = None,
        output_format: str | None = None,
        response_format: str = "url",
        stream: bool = False,
    ) -> dict[str, Any]:
        """生成符合文本描述的单张图片。

        Args:
            prompt: 文本提示词，描述要生成的图像内容。
            optimize_prompt_options: 提示词优化选项，可选配置字典。
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"1.5K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，未传入时默认取配置 default_size。
            {background_doc}
            watermark: 是否添加水印，未传入时默认取配置 default_watermark。
            tools: 模型工具配置，仅 {tools_families} 支持，如 [{"type": "web_search"}]。
            output_format: 输出图片格式，仅 {output_format_families} 支持 "jpeg" 或 "png"。
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"。
            stream: 是否使用流式传输，默认为 False；仅 {stream_families} 支持。

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等。

        Raises:
            SeedreamAPIError: API 调用失败。
            SeedreamValidationError: 参数验证失败。
            SeedreamTimeoutError: API 调用超时且重试次数耗尽。
            SeedreamNetworkError: 网络错误且重试次数耗尽。
            SeedreamConfigError: API 密钥为空。
        """
        validated = await self.prevalidate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            tools=tools,
            output_format=output_format,
            response_format=response_format,
            stream=stream,
        )
        resolved_background = validate_background(
            background,
            self.config.model_id,
            output_format=validated.output_format,
            layer_decomposition=False,
            image_count=0,
        )

        self.logger.opt(lazy=True).info(
            "开始文生图任务: prompt_meta={}, size={}",
            lambda: self._summarize_prompt(validated.prompt),
            lambda: validated.size,
        )

        async def _build_request() -> dict[str, Any]:
            return self._build_common_request(validated=validated, background=resolved_background)

        try:
            request_data = await _build_request_data(
                _ACTIVE_REQUEST_PLAN.get(), TEXT_TO_IMAGE_TOOL_NAME, _build_request
            )

            response = await self._call_api(TEXT_TO_IMAGE_TOOL_NAME, request_data)

            self._log_task_outcome("文生图", response)
            return response

        except Exception as e:
            raise self._finalize_generation_error("文生图", e)

    async def image_to_image(
        self,
        prompt: str | None = None,
        *,
        optimize_prompt_options: dict[str, Any] | None = None,
        image: str | None = None,
        layer_decomposition: bool | None = None,
        layer_decomposition_options: dict[str, Any] | None = None,
        size: str | None = None,
        background: str | None = None,
        watermark: bool | None = None,
        tools: list[dict[str, Any]] | None = None,
        output_format: str | None = None,
        response_format: str = "url",
        stream: bool = False,
    ) -> dict[str, Any]:
        """编辑已有图片，结合文字指令生成新图片。

        Args:
            prompt: 文本提示词，描述要对输入图像进行的修改或转换；图层拆分场景可
                缺省，由模型自动识别图片主要元素并拆分。
            optimize_prompt_options: 提示词优化选项，可选配置字典。
            image: 输入图像的 URL、data URI（data:image/*;base64,...）或本地文件路径。
            layer_decomposition: 是否开启图层拆分，仅 {layer_families} 支持；开启后单张
                输入图拆解为 1 张底图与{layer_limits}带透明通道的 PNG 图层。
            layer_decomposition_options: 图层拆分配置，仅 {layer_options_families} 支持
                且仅在 layer_decomposition 开启时生效；可配 max_images（含底图的
                产出总数上限，2-100）与 text_recognition（富文本 OCR 开关），
                未给定的子字段由 API 按官方默认取值。
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"1.5K"、"2K"、"3K"、"4K" 或
                "<宽>x<高>" 像素值；图层拆分场景仅支持 {layer_presets} 档位与
                "auto"，且未传入时默认取 "auto"，其余场景未传入时默认取配置
                default_size。
            {background_doc_decomposition}
            watermark: 是否添加水印，未传入时默认取配置 default_watermark。
            tools: 模型工具配置，仅 {tools_families} 支持，如 [{"type": "web_search"}]。
            output_format: 输出图片格式，仅 {output_format_families} 支持 "jpeg" 或 "png"。
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"。
            stream: 是否使用流式传输，默认为 False；仅 {stream_families} 支持。

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等。

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败。
            SeedreamValidationError: 参数验证失败。
            SeedreamTimeoutError: API 调用超时且重试次数耗尽。
            SeedreamNetworkError: 网络错误且重试次数耗尽。
            SeedreamConfigError: API 密钥为空或图像预处理配置缺失。
        """
        image = self._normalize_single_image(image)
        resolved_layer_decomposition = validate_layer_decomposition(
            layer_decomposition, self.config.model_id
        )
        validated = await self.prevalidate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            tools=tools,
            output_format=output_format,
            response_format=response_format,
            stream=stream,
            layer_decomposition=resolved_layer_decomposition,
        )
        resolved_background = validate_background(
            background,
            self.config.model_id,
            output_format=validated.output_format,
            layer_decomposition=resolved_layer_decomposition,
            image_count=1,
        )
        resolved_layer_options = validate_layer_decomposition_options(
            layer_decomposition_options,
            self.config.model_id,
            layer_decomposition=resolved_layer_decomposition,
        )

        # 图层拆分复用本方法请求流，任务标签按场景取值，日志归因不与图文生图混流。
        task_label = "图层拆分" if resolved_layer_decomposition else "图文生图"
        self.logger.opt(lazy=True).info(
            "开始{}任务: prompt_meta={}, size={}",
            lambda: task_label,
            lambda: self._summarize_prompt(validated.prompt),
            lambda: validated.size,
        )

        async def _build_request() -> dict[str, Any]:
            image_data = await self._prepare_image_input(image)
            extra: dict[str, Any] = {"image": image_data}
            if resolved_layer_decomposition:
                extra["layer_decomposition"] = True
            if resolved_layer_options is not None:
                extra["layer_decomposition_options"] = resolved_layer_options
            return self._build_common_request(
                validated=validated, background=resolved_background, extra=extra
            )

        try:
            request_data = await _build_request_data(
                _ACTIVE_REQUEST_PLAN.get(), IMAGE_TO_IMAGE_TOOL_NAME, _build_request
            )

            response = await self._call_api(IMAGE_TO_IMAGE_TOOL_NAME, request_data)

            self._log_task_outcome(task_label, response)
            return response

        except Exception as e:
            raise self._finalize_generation_error(task_label, e)

    async def multi_image_fusion(
        self,
        prompt: str | None = None,
        *,
        optimize_prompt_options: dict[str, Any] | None = None,
        image: list[str] | tuple[str, ...] | None = None,
        size: str | None = None,
        background: str | None = None,
        watermark: bool | None = None,
        tools: list[dict[str, Any]] | None = None,
        output_format: str | None = None,
        response_format: str = "url",
        stream: bool = False,
    ) -> dict[str, Any]:
        """融合多张参考图片的风格与元素生成新图像。

        Args:
            prompt: 文本提示词，描述要对输入图像进行的融合操作。
            optimize_prompt_options: 提示词优化选项，可选配置字典。
            image: 输入图像的 URL、data URI（data:image/*;base64,...）或本地文件路径
                列表，数量范围为 2-{max_reference_images} 张{reference_limit_annotation}。
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"1.5K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，未传入时默认取配置 default_size。
            {background_doc}
            watermark: 是否添加水印，未传入时默认取配置 default_watermark。
            tools: 模型工具配置，仅 {tools_families} 支持，如 [{"type": "web_search"}]。
            output_format: 输出图片格式，仅 {output_format_families} 支持 "jpeg" 或 "png"。
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"。
            stream: 是否使用流式传输，默认为 False；仅 {stream_families} 支持。

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等。

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败。
            SeedreamValidationError: 参数验证失败。
            SeedreamTimeoutError: API 调用超时且重试次数耗尽。
            SeedreamNetworkError: 网络错误且重试次数耗尽。
            SeedreamConfigError: API 密钥为空或图像预处理配置缺失。
        """
        image = self._normalize_image_sequence(
            image, min_count=2, model_id=self.config.model_id, field_name="image"
        )
        validated = await self.prevalidate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            tools=tools,
            output_format=output_format,
            response_format=response_format,
            stream=stream,
        )
        resolved_background = validate_background(
            background,
            self.config.model_id,
            output_format=validated.output_format,
            layer_decomposition=False,
            image_count=len(image),
        )

        self.logger.opt(lazy=True).info(
            "开始多图融合任务: prompt_meta={}, image_count={}, size={}",
            lambda: self._summarize_prompt(validated.prompt),
            lambda: len(image),
            lambda: validated.size,
        )

        async def _build_request() -> dict[str, Any]:
            image_data_list = await self._prepare_images_in_parallel(image)
            # sequential_image_generation 不显式传值：官方口径该参数仅部分模型
            # 支持，服务端缺省即 disabled，全模型恒传会向能力表外模型发参。
            extra: dict[str, Any] = {"image": image_data_list}
            return self._build_common_request(
                validated=validated, background=resolved_background, extra=extra
            )

        try:
            request_data = await _build_request_data(
                _ACTIVE_REQUEST_PLAN.get(), MULTI_IMAGE_FUSION_TOOL_NAME, _build_request
            )

            response = await self._call_api(MULTI_IMAGE_FUSION_TOOL_NAME, request_data)

            self._log_task_outcome("多图融合", response)
            return response

        except Exception as e:
            raise self._finalize_generation_error("多图融合", e)

    async def sequential_generation(
        self,
        prompt: str | None = None,
        *,
        optimize_prompt_options: dict[str, Any] | None = None,
        image: str | list[str] | tuple[str, ...] | None = None,
        sequential_image_generation_options: dict[str, Any] | None = None,
        size: str | None = None,
        background: str | None = None,
        watermark: bool | None = None,
        tools: list[dict[str, Any]] | None = None,
        output_format: str | None = None,
        response_format: str = "url",
        stream: bool = False,
    ) -> dict[str, Any]:
        """生成漫画分镜、品牌视觉等一组内容关联的图片。

        仅 {sequential_families} 支持组图。支持文生组图、单图生
        组图与多图生组图三种输入模式。

        Args:
            prompt: 文本提示词，描述要生成的图像内容。
            optimize_prompt_options: 提示词优化选项，可选配置字典。
            image: 可选的参考图像，支持单张图像或多张图像列表，元素为 URL、
                data URI（data:image/*;base64,...）或本地文件路径；参考图数量与
                生成数量之和不超过 15。
            sequential_image_generation_options: 组图配置，可选配置字典；可配
                max_images（最大生成图像数量，1-15，未传入时无参考图默认 15，
                有参考图时自动扣减以满足总量上限）。
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"1.5K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，未传入时默认取配置 default_size。
            {background_doc}
            watermark: 是否添加水印，未传入时默认取配置 default_watermark。
            tools: 模型工具配置，仅 {tools_families} 支持，如 [{"type": "web_search"}]。
            output_format: 输出图片格式，仅 {output_format_families} 支持 "jpeg" 或 "png"。
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"。
            stream: 是否使用流式传输，默认为 False；仅 {stream_families} 支持。

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等。

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败。
            SeedreamValidationError: 参数验证失败。
            SeedreamTimeoutError: API 调用超时且重试次数耗尽。
            SeedreamNetworkError: 网络错误且重试次数耗尽。
            SeedreamConfigError: API 密钥为空或图像预处理配置缺失。
        """
        validate_sequential_generation_support(self.config.model_id)

        validated = await self.prevalidate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            tools=tools,
            output_format=output_format,
            response_format=response_format,
            stream=stream,
        )

        reference_images: list[str] | None = None
        if image is not None:
            reference_images = self._normalize_image_sequence(
                image,
                min_count=1,
                model_id=self.config.model_id,
                field_name="image",
            )

        reference_count = len(reference_images or [])
        resolved_background = validate_background(
            background,
            self.config.model_id,
            output_format=validated.output_format,
            layer_decomposition=False,
            image_count=reference_count,
        )

        explicit_max_images = validate_sequential_image_generation_options(
            sequential_image_generation_options
        )
        resolved_max_images = validate_max_images(
            resolve_sequential_max_images(reference_count, explicit_max_images)
        )

        if reference_images is not None:
            validate_sequential_image_limit(
                reference_images, resolved_max_images, self.config.model_id
            )

        self.logger.opt(lazy=True).info(
            "开始组图输出任务: prompt_meta={}, max_images={}, size={}",
            lambda: self._summarize_prompt(validated.prompt),
            lambda: resolved_max_images,
            lambda: validated.size,
        )

        async def _build_request() -> dict[str, Any]:
            processed_image: str | list[str] | None = None
            if reference_images is not None:
                if len(reference_images) == 1:
                    processed_image = await self._prepare_image_input(reference_images[0])
                else:
                    processed_image = await self._prepare_images_in_parallel(reference_images)

            extra: dict[str, Any] = {
                "sequential_image_generation": "auto",
                "sequential_image_generation_options": {"max_images": resolved_max_images},
            }
            if processed_image is not None:
                extra["image"] = processed_image
            return self._build_common_request(
                validated=validated, background=resolved_background, extra=extra
            )

        try:
            request_data = await _build_request_data(
                _ACTIVE_REQUEST_PLAN.get(), SEQUENTIAL_GENERATION_TOOL_NAME, _build_request
            )

            response = await self._call_api(SEQUENTIAL_GENERATION_TOOL_NAME, request_data)

            self._log_task_outcome("组图输出", response)
            return response

        except Exception as e:
            raise self._finalize_generation_error("组图输出", e)

    async def prevalidate_common_generation_params(
        self,
        *,
        prompt: str | None,
        optimize_prompt_options: dict[str, Any] | None,
        size: str | None,
        watermark: bool | None,
        tools: list[dict[str, Any]] | None,
        output_format: str | None,
        response_format: str,
        stream: bool,
        layer_decomposition: bool = False,
    ) -> ValidatedCommonParams:
        """集中校验生成类工具的公共参数并返回校验后的各值。

        size 与 watermark 未显式传入时按 config.default_size / default_watermark
        兜底合成；图层拆分场景 size 未显式传入时按官方默认取 auto。各方法特有的
        图片数量与序列校验仍在各自方法内执行。绑定共享请求计划时按输入快照复用
        批内首次校验结果——批次分发前调用一次即完成整批预校验，校验失败立即上抛，
        异常类型与消息和单请求路径一致。
        """
        resolved_size, resolved_watermark = resolve_effective_generation_defaults(
            size=size,
            watermark=watermark,
            default_size=self.config.default_size,
            default_watermark=self.config.default_watermark,
            layer_decomposition=layer_decomposition,
        )
        inputs = (
            prompt,
            optimize_prompt_options,
            resolved_size,
            resolved_watermark,
            tools,
            output_format,
            response_format,
            stream,
            layer_decomposition,
        )

        async def _validate() -> ValidatedCommonParams:
            return await asyncio.to_thread(
                validate_common_generation_params,
                prompt=prompt,
                optimize_prompt_options=optimize_prompt_options,
                size=resolved_size,
                watermark=resolved_watermark,
                tools=tools,
                output_format=output_format,
                response_format=response_format,
                stream=stream,
                model_id=self.config.model_id,
                layer_decomposition=layer_decomposition,
            )

        plan = _ACTIVE_REQUEST_PLAN.get()
        if plan is not None:
            return await plan.get_or_validate(inputs, _validate)
        return await _validate()

    async def close(self) -> None:
        """关闭 HTTP 客户端连接；与 _ensure_client 经 _client_lock 串行，并发安全。"""
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()

    @staticmethod
    def _summarize_prompt(prompt: str | None) -> str:
        """生成提示词的日志摘要，仅含长度与 SHA-256 摘要前 12 位，避免提示词明文进入日志。"""
        if prompt is None:
            return "not-provided"
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        return f"len={len(prompt)}, sha256={digest}"

    @staticmethod
    def _normalize_single_image(image: str | None, *, field_name: str = "image") -> str:
        """校验并规范化单张图片输入，field_name 用于错误消息定位字段。"""
        if not isinstance(image, str):
            raise SeedreamValidationError(
                error_text("{field_name} 参数必须是字符串", field_name=as_plain(field_name)),
                field=field_name,
                value=image,
            )

        normalized = image.strip()
        if not normalized:
            raise SeedreamValidationError(
                error_text("{field_name} 参数不能为空字符串", field_name=as_plain(field_name)),
                field=field_name,
                value=image,
            )
        ensure_utf8_encodable(
            normalized,
            "{field_name} 参数包含无法编码的字符",
            field_name,
            field_name=as_plain(field_name),
        )
        return normalized

    @staticmethod
    def _normalize_image_sequence(
        images: str | Sequence[str] | None,
        *,
        min_count: int,
        model_id: str,
        field_name: str,
    ) -> list[str]:
        """校验并规范化图片序列输入，str 视作单元素列表，逐项规范化并按 min_count 与模型参考图上限校验数量。"""
        if not isinstance(images, (str, list, tuple)):
            raise SeedreamValidationError(
                error_text("{field_name} 参数必须是字符串列表", field_name=as_plain(field_name)),
                field=field_name,
                value=images,
            )

        image_items: Sequence[str] = [images] if isinstance(images, str) else images

        normalized_images: list[str] = []
        for index, image in enumerate(image_items, start=1):
            element_field = f"{field_name}[{index}]"
            normalized_images.append(
                SeedreamClient._normalize_single_image(image, field_name=element_field)
            )

        if len(normalized_images) < min_count:
            raise SeedreamValidationError(
                error_text(
                    "{field_name} 数量不能少于 {min_count}",
                    field_name=as_plain(field_name),
                    min_count=as_plain(min_count),
                ),
                field=field_name,
                value=normalized_images,
            )
        validate_reference_image_count(model_id, normalized_images, field_name=field_name)

        return normalized_images

    async def _prepare_image_input(self, image: str) -> str:
        """准备图像输入数据，委托 ImagePreparer 预处理缓存子系统。"""
        return await self._image_preparer.prepare_image_input(image)

    async def _prepare_images_in_parallel(self, images: Sequence[str]) -> list[str]:
        """受限并发预处理多张图片，委托 ImagePreparer。"""
        return await self._image_preparer.prepare_images_in_parallel(images)


# 类体后统一回填 docstring 占位符为能力表派生值。
for _member in vars(SeedreamClient).values():
    if (
        callable(_member)
        and _member.__doc__
        and _PLACEHOLDER_RESIDUE_PATTERN.search(_member.__doc__)
    ):
        _member.__doc__ = _fill_docstring_hints(_member.__doc__)
        leftover = _PLACEHOLDER_RESIDUE_PATTERN.search(_member.__doc__ or "")
        if leftover is not None:
            raise RuntimeError(
                raw_error_text(
                    "{member} docstring 残留未识别的家族占位符 {residue}",
                    member=as_plain(_member.__name__),
                    residue=as_plain(leftover.group(0)),
                )
            )
