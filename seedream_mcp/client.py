"""Seedream MCP 客户端模块。

定义 :class:`SeedreamClient`，封装火山引擎 Seedream 系列图像生成 API 的调用。
该类同时作为公共库 API 与 MCP 工具后端，提供文生图、图文生图、多图融合、
组图生成四种入口，入口处重新校验参数，调用方无需预校验。HTTP 传输与响应
归一化机械位于 ``_client_http``，批内共享请求计划位于 ``request_plan``。
"""

import asyncio
import hashlib
import types
from typing import Any, Sequence

import httpx

from ._client_http import _ClientHTTPMixin
from .config import SeedreamConfig, get_active_config
from .request_plan import _ACTIVE_REQUEST_PLAN, _build_request_data
from .utils.core.errors import SeedreamValidationError
from .utils.core.logs import get_logger
from .utils.model.model_capabilities import get_max_reference_images
from .utils.core.validators import (
    ValidatedCommonParams,
    ensure_utf8_encodable,
    resolve_effective_generation_defaults,
    resolve_sequential_max_images,
    validate_background,
    validate_common_generation_params,
    validate_layer_decomposition,
    validate_max_images,
    validate_sequential_generation_support,
    validate_sequential_image_limit,
)
from .utils.images.image_prepare import ImagePreparer


class SeedreamClient(_ClientHTTPMixin):
    """Seedream API 客户端。

    各生成方法在入口重新校验参数，公共库 API 与 MCP 工具后端两种调用路径行为一致。
    四个生成方法保持同构的显式模板而非抽象为公共骨架：方法签名是公共库 API 的
    直读契约，图片字段归一化与请求差异以平铺代码表达，引入间接层只会转移而不会
    消除复杂度。

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
        # 参考图预处理缓存子系统委托 ImagePreparer：LRU + single-flight 去重。
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
        *,
        prompt: str | None,
        size: str,
        watermark: bool,
        response_format: str,
        output_format: str | None,
        stream: bool,
        tools: list[Any] | None,
        validated_opts: dict[str, Any] | None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建各生成方法共享的请求参数基础字典，方法特有字段经 extra 并入。

        prompt 为 None 时不写入该键，对应图层拆分场景的缺省提示词，由模型自动
        识别拆分意图。
        """
        request_data: dict[str, Any] = {
            "model": self.config.model_id,
        }
        if prompt is not None:
            request_data["prompt"] = prompt
        if validated_opts:
            request_data["optimize_prompt_options"] = validated_opts
        update_payload: dict[str, Any] = {
            "size": size,
            "watermark": watermark,
            "response_format": response_format,
        }
        if extra:
            update_payload.update(extra)
        request_data.update(update_payload)
        if output_format is not None:
            request_data["output_format"] = output_format
        if stream:
            request_data["stream"] = True
        if tools:
            request_data["tools"] = tools
        return request_data

    async def text_to_image(
        self,
        prompt: str | None = None,
        optimize_prompt_options: dict[str, Any] | None = None,
        size: str | None = None,
        watermark: bool | None = None,
        response_format: str = "url",
        output_format: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """生成符合文本描述的单张图片。

        Args:
            prompt: 文本提示词，描述要生成的图像内容。
            optimize_prompt_options: 提示词优化选项，可选配置字典。
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"1.5K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，未传入时默认取配置 default_size。
            watermark: 是否添加水印，未传入时默认取配置 default_watermark。
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"。
            output_format: 输出图片格式，仅 5.0 系列 Pro/Lite 支持 "jpeg" 或 "png"。
            stream: 是否使用流式传输，默认为 False；5.0 Pro 不支持。
            tools: 模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持，如 [{"type": "web_search"}]。

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等。

        Raises:
            SeedreamAPIError: API 调用失败。
            SeedreamValidationError: 参数验证失败。
            SeedreamTimeoutError: API 调用超时且重试次数耗尽。
            SeedreamNetworkError: 网络错误且重试次数耗尽。
            SeedreamConfigError: API 密钥为空。
        """
        (
            validated_prompt,
            validated_opts,
            size,
            watermark,
            response_format,
            output_format,
            stream,
            tools,
        ) = await self._validate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            response_format=response_format,
            output_format=output_format,
            stream=stream,
            tools=tools,
        )

        self.logger.opt(lazy=True).info(
            "开始文生图任务: prompt_meta={}, size={}",
            lambda: self._summarize_prompt(validated_prompt),
            lambda: size,
        )

        async def _build_request() -> dict[str, Any]:
            return self._build_common_request(
                prompt=validated_prompt,
                size=size,
                watermark=watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                validated_opts=validated_opts,
            )

        try:
            request_data = await _build_request_data(
                _ACTIVE_REQUEST_PLAN.get(), "text_to_image", _build_request
            )

            response = await self._call_api("text_to_image", request_data)

            self._log_task_outcome("文生图", response)
            return response

        except Exception as e:
            raise self._finalize_generation_error("文生图", e)

    async def image_to_image(
        self,
        prompt: str | None = None,
        optimize_prompt_options: dict[str, Any] | None = None,
        image: str | None = None,
        layer_decomposition: bool | None = None,
        background: str | None = None,
        size: str | None = None,
        watermark: bool | None = None,
        response_format: str = "url",
        output_format: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """编辑已有图片，结合文字指令生成新图片。

        Args:
            prompt: 文本提示词，描述要对输入图像进行的修改或转换；图层拆分场景可
                缺省，由模型自动识别图片主要元素并拆分。
            optimize_prompt_options: 提示词优化选项，可选配置字典。
            image: 输入图像的 URL、data URI（data:image/*;base64,...）或本地文件路径。
            layer_decomposition: 是否开启图层拆分，仅 5.0 Pro 支持；开启后单张输入图
                拆解为 1 张底图与最多 16 个带透明通道的 PNG 图层。
            background: 图片透明通道，"transparent" 或 "opaque"，仅 5.0 Pro 支持；
                transparent 需输入单张带透明通道的图片，且与 output_format="jpeg"
                互斥。
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"1.5K"、"2K"、"3K"、"4K" 或
                "<宽>x<高>" 像素值；图层拆分场景仅支持档位与 "auto"，且未传入时默认
                取 "auto"，其余场景未传入时默认取配置 default_size。
            watermark: 是否添加水印，未传入时默认取配置 default_watermark。
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"。
            output_format: 输出图片格式，仅 5.0 系列 Pro/Lite 支持 "jpeg" 或 "png"。
            stream: 是否使用流式传输，默认为 False；5.0 Pro 不支持。
            tools: 模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持，如 [{"type": "web_search"}]。

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
        (
            validated_prompt,
            validated_opts,
            size,
            watermark,
            response_format,
            output_format,
            stream,
            tools,
        ) = await self._validate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            response_format=response_format,
            output_format=output_format,
            stream=stream,
            tools=tools,
            layer_decomposition=resolved_layer_decomposition,
        )
        resolved_background = validate_background(
            background, self.config.model_id, output_format=output_format
        )

        self.logger.opt(lazy=True).info(
            "开始图文生图任务: prompt_meta={}, size={}",
            lambda: self._summarize_prompt(validated_prompt),
            lambda: size,
        )

        async def _build_request() -> dict[str, Any]:
            image_data = await self._prepare_image_input(image)
            extra: dict[str, Any] = {"image": image_data}
            if resolved_layer_decomposition:
                extra["layer_decomposition"] = True
            if resolved_background is not None:
                extra["background"] = resolved_background
            return self._build_common_request(
                prompt=validated_prompt,
                size=size,
                watermark=watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                validated_opts=validated_opts,
                extra=extra,
            )

        try:
            request_data = await _build_request_data(
                _ACTIVE_REQUEST_PLAN.get(), "image_to_image", _build_request
            )

            response = await self._call_api("image_to_image", request_data)

            self._log_task_outcome("图文生图", response)
            return response

        except Exception as e:
            raise self._finalize_generation_error("图文生图", e)

    async def multi_image_fusion(
        self,
        prompt: str | None = None,
        optimize_prompt_options: dict[str, Any] | None = None,
        image: Sequence[str] | None = None,
        size: str | None = None,
        watermark: bool | None = None,
        response_format: str = "url",
        output_format: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """融合多张参考图片的风格与元素生成新图像。

        Args:
            prompt: 文本提示词，描述要对输入图像进行的融合操作。
            optimize_prompt_options: 提示词优化选项，可选配置字典。
            image: 输入图像的 URL、data URI（data:image/*;base64,...）或本地文件路径
                列表，数量范围为 2-14 张；5.0 Pro 最多 10 张。
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"1.5K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，未传入时默认取配置 default_size。
            watermark: 是否添加水印，未传入时默认取配置 default_watermark。
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"。
            output_format: 输出图片格式，仅 5.0 系列 Pro/Lite 支持 "jpeg" 或 "png"。
            stream: 是否使用流式传输，默认为 False；5.0 Pro 不支持。
            tools: 模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持，如 [{"type": "web_search"}]。

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等。

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败。
            SeedreamValidationError: 参数验证失败。
            SeedreamTimeoutError: API 调用超时且重试次数耗尽。
            SeedreamNetworkError: 网络错误且重试次数耗尽。
            SeedreamConfigError: API 密钥为空或图像预处理配置缺失。
        """
        max_reference = get_max_reference_images(self.config.model_id)
        image = self._normalize_image_sequence(
            image, min_count=2, max_count=max_reference, field_name="image"
        )
        (
            validated_prompt,
            validated_opts,
            size,
            watermark,
            response_format,
            output_format,
            stream,
            tools,
        ) = await self._validate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            response_format=response_format,
            output_format=output_format,
            stream=stream,
            tools=tools,
        )

        self.logger.opt(lazy=True).info(
            "开始多图融合任务: prompt_meta={}, image_count={}, size={}",
            lambda: self._summarize_prompt(validated_prompt),
            lambda: len(image),
            lambda: size,
        )

        async def _build_request() -> dict[str, Any]:
            image_data_list = await self._prepare_images_in_parallel(image)
            return self._build_common_request(
                prompt=validated_prompt,
                size=size,
                watermark=watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                validated_opts=validated_opts,
                # sequential_image_generation 不显式传值：官方口径该参数仅部分模型
                # 支持，服务端缺省即 disabled，全模型恒传会向能力表外模型发参。
                extra={"image": image_data_list},
            )

        try:
            request_data = await _build_request_data(
                _ACTIVE_REQUEST_PLAN.get(), "multi_image_fusion", _build_request
            )

            response = await self._call_api("multi_image_fusion", request_data)

            self._log_task_outcome("多图融合", response)
            return response

        except Exception as e:
            raise self._finalize_generation_error("多图融合", e)

    async def sequential_generation(
        self,
        prompt: str | None = None,
        optimize_prompt_options: dict[str, Any] | None = None,
        image: str | Sequence[str] | None = None,
        size: str | None = None,
        watermark: bool | None = None,
        max_images: int | None = None,
        response_format: str = "url",
        output_format: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """生成漫画分镜、品牌视觉等一组内容关联的图片。

        仅 5.0/5.0 Lite/4.5/4.0 支持，5.0 Pro 不支持组图。支持文生组图、单图生
        组图与多图生组图三种输入模式。

        Args:
            prompt: 文本提示词，描述要生成的图像内容。
            optimize_prompt_options: 提示词优化选项，可选配置字典。
            image: 可选的参考图像，支持单张图像或多张图像列表，元素为 URL、
                data URI（data:image/*;base64,...）或本地文件路径；参考图数量与
                生成数量之和不超过 15。
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"1.5K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，未传入时默认取配置 default_size。
            watermark: 是否添加水印，未传入时默认取配置 default_watermark。
            max_images: 最大生成图像数量，范围为 1-15；未传入时无参考图默认 15，有参考图时自动扣减以满足总量上限。
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"。
            output_format: 输出图片格式，仅 5.0 系列 Pro/Lite 支持 "jpeg" 或 "png"。
            stream: 是否使用流式传输，默认为 False；5.0 Pro 不支持。
            tools: 模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持，如 [{"type": "web_search"}]。

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

        (
            validated_prompt,
            validated_opts,
            size,
            watermark,
            response_format,
            output_format,
            stream,
            tools,
        ) = await self._validate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            response_format=response_format,
            output_format=output_format,
            stream=stream,
            tools=tools,
        )

        reference_images = None
        if image is not None:
            if isinstance(image, str):
                reference_images = [image]
            elif isinstance(image, (list, tuple)):
                reference_images = list(image)
            else:
                raise SeedreamValidationError(
                    "image 参数必须是字符串或字符串列表",
                    field="image",
                    value=image,
                )

        if reference_images is not None:
            reference_images = self._normalize_image_sequence(
                reference_images,
                min_count=1,
                max_count=get_max_reference_images(self.config.model_id),
                field_name="image",
            )

        resolved_max_images = resolve_sequential_max_images(max_images, reference_images)
        resolved_max_images = validate_max_images(resolved_max_images)

        if reference_images is not None:
            validate_sequential_image_limit(
                resolved_max_images, reference_images, self.config.model_id
            )

        self.logger.opt(lazy=True).info(
            "开始组图输出任务: prompt_meta={}, max_images={}, size={}",
            lambda: self._summarize_prompt(validated_prompt),
            lambda: resolved_max_images,
            lambda: size,
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
                prompt=validated_prompt,
                size=size,
                watermark=watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                validated_opts=validated_opts,
                extra=extra,
            )

        try:
            request_data = await _build_request_data(
                _ACTIVE_REQUEST_PLAN.get(), "sequential_generation", _build_request
            )

            response = await self._call_api("sequential_generation", request_data)

            self._log_task_outcome("组图输出", response)
            return response

        except Exception as e:
            raise self._finalize_generation_error("组图输出", e)

    async def _validate_common_generation_params(
        self,
        *,
        prompt: str | None,
        optimize_prompt_options: dict[str, Any] | None,
        size: str | None,
        watermark: bool | None,
        response_format: str,
        output_format: str | None,
        stream: bool,
        tools: list[dict[str, Any]] | None,
        layer_decomposition: bool = False,
    ) -> ValidatedCommonParams:
        """集中校验生成类工具的公共参数并返回校验后的各值。

        size 与 watermark 未显式传入时按 config.default_size / default_watermark
        兜底合成；图层拆分场景 size 未显式传入时按官方默认取 auto。各方法特有的
        图片数量与序列校验仍在各自方法内执行。绑定共享请求计划时按输入快照复用
        批内首次校验结果。
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
            response_format,
            output_format,
            stream,
            tools,
            layer_decomposition,
        )

        async def _validate() -> ValidatedCommonParams:
            return await asyncio.to_thread(
                validate_common_generation_params,
                prompt=prompt,
                optimize_prompt_options=optimize_prompt_options,
                size=resolved_size,
                watermark=resolved_watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                model_id=self.config.model_id,
                layer_decomposition=layer_decomposition,
            )

        plan = _ACTIVE_REQUEST_PLAN.get()
        if plan is not None:
            return await plan.get_or_validate(inputs, _validate)
        return await _validate()

    async def prevalidate_common_generation_params(
        self,
        *,
        prompt: str | None,
        optimize_prompt_options: dict[str, Any] | None,
        size: str | None,
        watermark: bool | None,
        response_format: str,
        output_format: str | None,
        stream: bool,
        tools: list[dict[str, Any]] | None,
        layer_decomposition: bool = False,
    ) -> None:
        """批次分发前校验公共参数一次，结果经共享计划供同批各请求复用。

        校验失败立即上抛，异常类型与消息和单请求路径一致；未绑定共享计划时仅执行
        校验，无缓存效果。
        """
        await self._validate_common_generation_params(
            prompt=prompt,
            optimize_prompt_options=optimize_prompt_options,
            size=size,
            watermark=watermark,
            response_format=response_format,
            output_format=output_format,
            stream=stream,
            tools=tools,
            layer_decomposition=layer_decomposition,
        )

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
                f"{field_name} 参数必须是字符串", field=field_name, value=image
            )

        normalized = image.strip()
        if not normalized:
            raise SeedreamValidationError(
                f"{field_name} 参数不能为空字符串", field=field_name, value=image
            )
        ensure_utf8_encodable(normalized, f"{field_name} 参数包含无法编码的字符", field_name)
        return normalized

    @staticmethod
    def _normalize_image_sequence(
        images: Sequence[str] | None,
        *,
        min_count: int,
        max_count: int,
        field_name: str,
    ) -> list[str]:
        """校验并规范化图片列表输入，逐项规范化并按 min_count 与 max_count 校验数量。"""
        if not isinstance(images, (list, tuple)):
            raise SeedreamValidationError(
                f"{field_name} 参数必须是字符串列表",
                field=field_name,
                value=images,
            )

        normalized_images: list[str] = []
        for index, image in enumerate(images, start=1):
            element_field = f"{field_name}[{index}]"
            normalized_images.append(
                SeedreamClient._normalize_single_image(image, field_name=element_field)
            )

        image_count = len(normalized_images)
        if image_count < min_count:
            raise SeedreamValidationError(
                f"{field_name} 数量不能少于 {min_count}",
                field=field_name,
                value=normalized_images,
            )
        if image_count > max_count:
            raise SeedreamValidationError(
                f"{field_name} 数量不能超过 {max_count}",
                field=field_name,
                value=normalized_images,
            )

        return normalized_images

    async def _prepare_image_input(self, image: str) -> str:
        """准备图像输入数据，委托 ImagePreparer 预处理缓存子系统。"""
        return await self._image_preparer.prepare_image_input(image)

    async def _prepare_images_in_parallel(self, images: Sequence[str]) -> list[str]:
        """受限并发预处理多张图片，委托 ImagePreparer。"""
        return await self._image_preparer.prepare_images_in_parallel(images)
