"""Seedream MCP 客户端模块。

定义 :class:`SeedreamClient`，封装火山引擎 Seedream 系列图像生成 API 的调用逻辑。
该类同时作为公共库 API 与 MCP 工具后端使用，提供文生图、图文生图、多图融合与
组图生成四种生成入口，并在入口处对参数重新校验，与工具层形成 defense-in-depth。

核心能力包括图像预处理 LRU 缓存与 single-flight 去重、流式与非流式响应统一解析、
指数退避重试与 Retry-After 处理，以及请求与响应的安全脱敏与异常分类。
"""

# 标准库导入
import asyncio
import hashlib
import json
import os
import random
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# 第三方库导入
import httpx

# 本地模块导入
from .config import SeedreamConfig, get_active_config
from .utils.errors import (
    SeedreamAPIError,
    SeedreamNetworkError,
    SeedreamTimeoutError,
    SeedreamValidationError,
    handle_api_error,
    parse_retry_after,
)
from .utils.logging import get_logger, log_function_call
from .utils.path_utils import is_path_within_any_base
from .utils.validation import (
    get_max_reference_images,
    is_seedream_50_pro_model,
    resolve_sequential_max_images,
    validate_generation_tools,
    validate_max_images,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_prompt,
    validate_response_format,
    validate_sequential_image_limit,
    validate_size_for_model,
    validate_stream,
    validate_watermark,
)
from .utils.sse_parser import is_sse_response, parse_sse_response

# 预处理缓存键：(image 字符串, workspace_roots 字符串元组, 本地文件 mtime+size 签名)
_PrepareCacheKey = Tuple[str, Tuple[str, ...], Tuple[float, int]]


class SeedreamClient:
    """
    Seedream MCP API 客户端类

    本类同时作为公共库 API 与 MCP 工具后端使用。各生成方法在入口对参数重新校验，
    与工具层（tools）的校验形成 defense-in-depth，确保两种调用路径行为一致。

    Attributes:
        config: 客户端配置对象
        logger: 日志记录器实例
    """

    def __init__(self, config: Optional[SeedreamConfig] = None):
        """
        初始化 Seedream API 客户端

        Args:
            config: 配置对象，若为 None 则使用全局默认配置
        """
        self.config = config or get_active_config()
        self.logger = get_logger(__name__)
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()
        self._timeout: Optional[httpx.Timeout] = None
        self._image_prepare_concurrency = self.config.image_prepare_concurrency
        # 预处理结果按输入原文缓存，避免并行请求对同一参考图重复读取与编码；
        # 命中时移至末尾实现 LRU，超限淘汰最久未用条目
        self._prepare_cache: OrderedDict[_PrepareCacheKey, str] = OrderedDict()
        self._prepare_cache_max = self.config.prepare_cache_max
        # 并发 miss 去重：同一缓存键的在途预处理复用同一 task，避免重复读+编码
        self._prepare_inflight: dict[_PrepareCacheKey, asyncio.Task[str]] = {}

    async def __aenter__(self) -> "SeedreamClient":
        """
        异步上下文管理器入口

        创建并初始化 HTTP 客户端连接。

        Returns:
            SeedreamClient: 当前客户端实例
        """
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        异步上下文管理器出口

        清理资源并关闭客户端连接。

        Args:
            exc_type: 异常类型
            exc_val: 异常值
            exc_tb: 异常追踪信息
        """
        await self.close()

    def _build_common_request(
        self,
        *,
        prompt: str,
        size: str,
        watermark: bool,
        response_format: str,
        output_format: Optional[str],
        stream: bool,
        tools: Optional[List[Any]],
        validated_opts: Optional[Dict[str, Any]],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """构建各生成方法共享的请求参数基础字典。

        size/watermark/response_format/output_format/stream/tools 的组装逻辑在四个生成
        方法中完全相同，集中于此避免漂移；方法特有的字段如参考图、组图选项等通过 extra 并入。
        """
        request_data: Dict[str, Any] = {
            "model": self.config.model_id,
            "prompt": prompt,
        }
        if validated_opts:
            request_data["optimize_prompt_options"] = validated_opts
        update_payload: Dict[str, Any] = {
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

    @log_function_call
    async def text_to_image(
        self,
        prompt: str,
        optimize_prompt_options: Optional[Dict[str, Any]] = None,
        size: str = "2K",
        watermark: bool = False,
        response_format: str = "url",
        output_format: Optional[str] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        文生图功能

        通过给模型提供清晰准确的文字指令，即可快速获得符合描述的高质量单张图片。

        Args:
            prompt: 文本提示词，描述要生成的图像内容
            optimize_prompt_options: 提示词优化选项，可选配置字典
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，默认为 "2K"
            watermark: 是否添加水印，默认为 False
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            output_format: 输出图片格式，仅 5.0 系列 Pro/Lite 支持 "jpeg" 或 "png"
            stream: 是否使用流式传输，默认为 False；5.0 Pro 不支持
            tools: 模型工具配置，仅 5.0 Lite 支持，如 [{"type": "web_search"}]

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等

        Raises:
            SeedreamAPIError: API 调用失败
            SeedreamValidationError: 参数验证失败
        """
        prompt = validate_prompt(prompt)
        validated_opts = validate_optimize_prompt_options(
            optimize_prompt_options, self.config.model_id
        )
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)
        output_format = validate_output_format(output_format, self.config.model_id)
        stream = validate_stream(stream, self.config.model_id)
        tools = validate_generation_tools(tools, self.config.model_id)

        self.logger.opt(lazy=True).info(
            "开始文生图任务: prompt_meta={}, size={}",
            lambda: self._summarize_prompt(prompt),
            lambda: size,
        )

        try:
            request_data = self._build_common_request(
                prompt=prompt,
                size=size,
                watermark=watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                validated_opts=validated_opts,
            )

            response = await self._call_api("text_to_image", request_data)

            self.logger.info("文生图任务完成")
            return response

        except Exception as e:
            self.logger.error("文生图任务失败: {}", e)
            raise self._handle_api_error(e)

    @log_function_call
    async def image_to_image(
        self,
        prompt: str,
        optimize_prompt_options: Optional[Dict[str, Any]] = None,
        image: Optional[str] = None,
        size: str = "2K",
        watermark: bool = False,
        response_format: str = "url",
        output_format: Optional[str] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        图文生图功能

        基于已有图片，结合文字指令进行图像编辑。

        Args:
            prompt: 文本提示词，描述要对输入图像进行的修改或转换
            optimize_prompt_options: 提示词优化选项，可选配置字典
            image: 输入图像的 URL 或本地文件路径
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，默认为 "2K"
            watermark: 是否添加水印，默认为 False
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            output_format: 输出图片格式，仅 5.0 系列 Pro/Lite 支持 "jpeg" 或 "png"
            stream: 是否使用流式传输，默认为 False；5.0 Pro 不支持
            tools: 模型工具配置，仅 5.0 Lite 支持，如 [{"type": "web_search"}]

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败
        """
        prompt = validate_prompt(prompt)
        validated_opts = validate_optimize_prompt_options(
            optimize_prompt_options, self.config.model_id
        )
        image = self._normalize_single_image(image)
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)
        output_format = validate_output_format(output_format, self.config.model_id)
        stream = validate_stream(stream, self.config.model_id)
        tools = validate_generation_tools(tools, self.config.model_id)

        self.logger.opt(lazy=True).info(
            "开始图文生图任务: prompt_meta={}, size={}",
            lambda: self._summarize_prompt(prompt),
            lambda: size,
        )

        try:
            image_data = await self._prepare_image_input(image)

            request_data = self._build_common_request(
                prompt=prompt,
                size=size,
                watermark=watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                validated_opts=validated_opts,
                extra={"image": image_data},
            )

            response = await self._call_api("image_to_image", request_data)

            self.logger.info("图文生图任务完成")
            return response

        except Exception as e:
            self.logger.error("图文生图任务失败: {}", e)
            raise self._handle_api_error(e)

    @log_function_call
    async def multi_image_fusion(
        self,
        prompt: str,
        optimize_prompt_options: Optional[Dict[str, Any]] = None,
        image: Optional[List[str]] = None,
        size: str = "2K",
        watermark: bool = False,
        response_format: str = "url",
        output_format: Optional[str] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        多图融合功能

        根据输入的文本描述和多张参考图片，融合它们的风格、元素等特征来生成新图像。

        Args:
            prompt: 文本提示词，描述要对输入图像进行的融合操作
            optimize_prompt_options: 提示词优化选项，可选配置字典
            image: 输入图像的 URL 或本地文件路径列表，数量范围为 2-14 张；5.0 Pro 最多 10 张
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，默认为 "2K"
            watermark: 是否添加水印，默认为 False
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            output_format: 输出图片格式，仅 5.0 系列 Pro/Lite 支持 "jpeg" 或 "png"
            stream: 是否使用流式传输，默认为 False；5.0 Pro 不支持
            tools: 模型工具配置，仅 5.0 Lite 支持，如 [{"type": "web_search"}]

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败
        """
        prompt = validate_prompt(prompt)
        validated_opts = validate_optimize_prompt_options(
            optimize_prompt_options, self.config.model_id
        )
        max_reference = get_max_reference_images(self.config.model_id)
        image = self._normalize_image_sequence(
            image, min_count=2, max_count=max_reference, field_name="image"
        )
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)
        output_format = validate_output_format(output_format, self.config.model_id)
        stream = validate_stream(stream, self.config.model_id)
        tools = validate_generation_tools(tools, self.config.model_id)

        self.logger.opt(lazy=True).info(
            "开始多图融合任务: prompt_meta={}, image_count={}, size={}",
            lambda: self._summarize_prompt(prompt),
            lambda: len(image),
            lambda: size,
        )

        try:
            image_data_list = await self._prepare_images_in_parallel(image)

            request_data = self._build_common_request(
                prompt=prompt,
                size=size,
                watermark=watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                validated_opts=validated_opts,
                extra={"image": image_data_list, "sequential_image_generation": "disabled"},
            )

            response = await self._call_api("multi_image_fusion", request_data)

            self.logger.info("多图融合任务完成")
            return response

        except Exception as e:
            self.logger.error("多图融合任务失败: {}", e)
            raise self._handle_api_error(e)

    @log_function_call
    async def sequential_generation(
        self,
        prompt: str,
        optimize_prompt_options: Optional[Dict[str, Any]] = None,
        image: Optional[Union[str, Sequence[str]]] = None,
        size: str = "2K",
        watermark: bool = False,
        max_images: Optional[int] = None,
        response_format: str = "url",
        output_format: Optional[str] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        组图输出功能，仅 5.0 Lite/4.5/4.0 支持，5.0 Pro 不支持组图

        支持通过一张或者多张图片和文字信息，生成漫画分镜、品牌视觉等一组内容关联的图片。

        支持三种输入模式：
        1. 文生组图：仅使用文本提示词
        2. 单图生组图：使用单张参考图像和文本提示词
        3. 多图生组图：使用多张参考图像和文本提示词

        Args:
            prompt: 文本提示词，描述要生成的图像内容
            optimize_prompt_options: 提示词优化选项，可选配置字典
            image: 可选的参考图像，支持单张图像 URL/路径或多张图像 URL/路径列表；参考图数量与生成数量之和不超过 15
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，默认为 "2K"
            watermark: 是否添加水印，默认为 False
            max_images: 最大生成图像数量，范围为 1-15；未传入时无参考图默认 15，有参考图时自动扣减以满足总量上限
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            output_format: 输出图片格式，仅 5.0 系列 Pro/Lite 支持 "jpeg" 或 "png"
            stream: 是否使用流式传输，默认为 False；5.0 Pro 不支持
            tools: 模型工具配置，仅 5.0 Lite 支持，如 [{"type": "web_search"}]

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败
        """
        # 5.0 Pro 不支持 sequential_image_generation 组图生成
        if is_seedream_50_pro_model(self.config.model_id):
            raise SeedreamValidationError(
                "doubao-seedream-5.0-pro 不支持组图生成，"
                "请将模型切换为 doubao-seedream-5.0/5.0-lite/4.5/4.0",
                field="model",
                value=self.config.model_id,
            )

        prompt = validate_prompt(prompt)
        validated_opts = validate_optimize_prompt_options(
            optimize_prompt_options, self.config.model_id
        )
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        output_format = validate_output_format(output_format, self.config.model_id)
        tools = validate_generation_tools(tools, self.config.model_id)

        processed_image: Optional[Union[str, List[str]]] = None
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

        response_format = validate_response_format(response_format)
        stream = validate_stream(stream, self.config.model_id)

        try:
            if reference_images is not None:
                if len(reference_images) == 1:
                    processed_image = await self._prepare_image_input(reference_images[0])
                else:
                    processed_image = await self._prepare_images_in_parallel(reference_images)

            self.logger.opt(lazy=True).info(
                "开始组图输出任务: prompt_meta={}, max_images={}, size={}",
                lambda: self._summarize_prompt(prompt),
                lambda: resolved_max_images,
                lambda: size,
            )

            extra: Dict[str, Any] = {
                "sequential_image_generation": "auto",
                "sequential_image_generation_options": {"max_images": resolved_max_images},
            }
            if processed_image is not None:
                extra["image"] = processed_image
            request_data = self._build_common_request(
                prompt=prompt,
                size=size,
                watermark=watermark,
                response_format=response_format,
                output_format=output_format,
                stream=stream,
                tools=tools,
                validated_opts=validated_opts,
                extra=extra,
            )

            response = await self._call_api("sequential_generation", request_data)

            self.logger.info("组图输出任务完成")
            return response

        except Exception as e:
            self.logger.error("组图输出任务失败: {}", e)
            raise self._handle_api_error(e)

    async def close(self) -> None:
        """关闭 HTTP 客户端连接，释放资源。

        持 _client_lock 与 _ensure_client 串行，避免并发关闭与首次创建交错，
        导致后续请求拿到 None 或已关闭的客户端。
        """
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()

    def _build_http_timeout(self) -> httpx.Timeout:
        """
        构建并缓存统一超时策略。首次构建后缓存到实例，避免每次请求重复构造。

        - `timeout`：连接建立/连接池获取/请求写入阶段
        - `api_timeout`：响应读取阶段与总超时上限
        """
        if self._timeout is None:
            base_timeout = float(self.config.timeout)
            api_timeout = float(self.config.api_timeout)
            self._timeout = httpx.Timeout(
                timeout=api_timeout,
                connect=base_timeout,
                read=api_timeout,
                write=base_timeout,
                pool=base_timeout,
            )
        return self._timeout

    async def _ensure_client(self) -> None:
        """
        确保 HTTP 客户端已创建

        如果客户端未初始化，则创建新的 AsyncClient 实例，
        并配置请求头和超时设置。首次创建用双检查锁串行化，避免并发请求
        重复创建 httpx.AsyncClient 导致资源泄漏。

        Raises:
            SeedreamAPIError: 客户端创建失败或配置无效
        """
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    try:
                        headers = self._get_headers()
                        if not headers:
                            raise SeedreamAPIError("无法生成请求头：配置可能无效")

                        # trust_env=False 防止 HTTP_PROXY 等环境变量绕过 SSRF 防护或截获 API Key
                        self._client = httpx.AsyncClient(
                            timeout=self._build_http_timeout(),
                            headers=headers,
                            trust_env=False,
                        )
                        self.logger.debug("HTTP 客户端创建成功")

                    except Exception as e:
                        self.logger.error("HTTP 客户端创建失败: {}", e)
                        self._client = None
                        raise SeedreamAPIError(f"HTTP 客户端初始化失败: {str(e)}") from e

    def _get_headers(self) -> Dict[str, str]:
        """
        获取 API 请求头

        构建包含认证信息的 HTTP 请求头。

        Returns:
            包含 Authorization 和 Content-Type 的请求头字典

        Raises:
            SeedreamAPIError: 配置对象为空或 API 密钥为空
        """
        if not self.config:
            raise SeedreamAPIError("配置对象为空")

        if not self.config.api_key:
            raise SeedreamAPIError("API 密钥为空，请检查环境变量 ARK_API_KEY")

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        self.logger.debug("生成请求头: Authorization=Bearer ***")
        return headers

    @staticmethod
    def _summarize_prompt(prompt: str) -> str:
        """
        生成提示词日志摘要
        """
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        return f"len={len(prompt)}, sha256={digest}"

    @staticmethod
    def _normalize_single_image(image: Optional[str]) -> str:
        """
        校验并规范化单张图片输入
        """
        if not isinstance(image, str):
            raise SeedreamValidationError("image 参数必须是字符串", field="image", value=image)

        normalized = image.strip()
        if not normalized:
            raise SeedreamValidationError("image 参数不能为空字符串", field="image", value=image)
        return normalized

    @staticmethod
    def _normalize_image_sequence(
        images: Optional[Sequence[str]],
        *,
        min_count: int,
        max_count: int,
        field_name: str,
    ) -> List[str]:
        """
        校验并规范化图片列表输入
        """
        if not isinstance(images, (list, tuple)):
            raise SeedreamValidationError(
                f"{field_name} 参数必须是字符串列表",
                field=field_name,
                value=images,
            )

        normalized_images: List[str] = []
        for index, image in enumerate(images, start=1):
            if not isinstance(image, str):
                raise SeedreamValidationError(
                    f"{field_name}[{index}] 必须是字符串",
                    field=f"{field_name}[{index}]",
                    value=image,
                )
            normalized_image = image.strip()
            if not normalized_image:
                raise SeedreamValidationError(
                    f"{field_name}[{index}] 不能为空字符串",
                    field=f"{field_name}[{index}]",
                    value=image,
                )
            normalized_images.append(normalized_image)

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

    @staticmethod
    def _summarize_image_field(image_value: Any) -> Any:
        """
        汇总 image 字段用于安全日志
        """
        if isinstance(image_value, list):
            return {
                "type": "list",
                "count": len(image_value),
                "samples": [
                    SeedreamClient._summarize_image_field(item) for item in image_value[:3]
                ],
                "truncated": len(image_value) > 3,
            }

        if not isinstance(image_value, str):
            return f"<{type(image_value).__name__}>"

        value = image_value.strip()
        if value.startswith(("http://", "https://")):
            return "<image_url>"
        if value.lower().startswith("data:image/"):
            return f"<data_uri:{len(value)} chars>"
        return "<local_image_path>"

    def _sanitize_request_for_logging(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """对请求体做日志脱敏：浅拷贝后替换 prompt 与 image 字段，其余原样引用。"""
        safe_data = dict(request_data)
        prompt = safe_data.get("prompt")
        if prompt is not None:
            prompt_length = len(prompt) if isinstance(prompt, str) else 0
            safe_data["prompt"] = f"<redacted:{prompt_length} chars>"
        image = safe_data.get("image")
        if image is not None:
            safe_data["image"] = self._summarize_image_field(image)
        return safe_data

    def _get_http_client(self) -> httpx.AsyncClient:
        """
        获取并校验 HTTP 客户端实例。
        """
        if self._client is None:
            raise SeedreamAPIError("HTTP 客户端未正确初始化")
        if not callable(getattr(self._client, "post", None)):
            raise SeedreamAPIError("HTTP 客户端的 post 方法不可用")
        return self._client

    def _build_generation_url(self) -> str:
        """
        构建图片生成接口 URL。
        """
        return f"{self.config.base_url}/images/generations"

    def _log_request_attempt(
        self,
        *,
        endpoint: str,
        attempt: int,
        total_attempts: int,
        url: str,
        safe_request_data: Dict[str, Any],
    ) -> None:
        """
        记录单次请求尝试日志。
        """
        self.logger.debug(
            "{} API 调用尝试 {}/{}",
            endpoint,
            attempt + 1,
            total_attempts,
        )
        self.logger.debug("请求 URL: {}", url)
        self.logger.debug("请求数据(脱敏): {}", safe_request_data)

    def _build_api_result(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """统一归一化 API 返回结果结构。

        success 仅代表 HTTP 层成功，即已收到 200 响应；body 级的部分失败或空数据
        由 status 与 data 共同表达，status 取值为 completed/partial/failed，
        调用方应同时检查 status 而非仅依赖 success。
        """
        data = payload.get("data")
        if isinstance(data, list):
            data_count = len(data)
        elif data is None:
            data_count = 0
        else:
            data_count = 1

        # 检测部分图片失败 → 标记 partial 状态；
        # 与 sse_parser 保持一致，仅当 status 为 None 或 completed 时改写为 partial，
        # 避免顶层 status=completed 且 data 含 error 时漏标 partial
        status = payload.get("status")
        if (
            status in (None, "completed")
            and isinstance(data, list)
            and any(isinstance(item, dict) and "error" in item for item in data)
        ):
            status = "partial"

        self.logger.debug(
            "解析 JSON 成功: status={}, data_count={}",
            status,
            data_count,
        )
        return {
            "success": True,
            "data": payload.get("data", []),
            "usage": payload.get("usage", {}),
            "status": status,
            "tools": payload.get("tools"),
        }

    @staticmethod
    def _retry_after_or_none(status_code: int, headers: Any) -> Optional[float]:
        """对可重试状态码（429/5xx）解析 Retry-After，其余返回 None。"""
        if status_code == 429 or status_code >= 500:
            return parse_retry_after(headers)
        return None

    def _raise_for_response_status(self, response: httpx.Response) -> None:
        """
        将非 200 状态码转换为统一 API 异常。
        """
        if response.status_code == 200:
            return

        try:
            error_data = response.json()
        except Exception:
            error_data = {"message": response.text}
        retry_after = self._retry_after_or_none(response.status_code, response.headers)
        raise handle_api_error(response.status_code, error_data, retry_after=retry_after)

    async def _raise_for_stream_response_status(self, response: httpx.Response) -> None:
        """
        将流式响应中的非 200 状态码转换为统一 API 异常。
        """
        if response.status_code == 200:
            return

        error_text = (await response.aread()).decode("utf-8", errors="ignore")
        try:
            error_data = json.loads(error_text)
        except Exception:
            error_data = {"message": error_text}
        retry_after = self._retry_after_or_none(response.status_code, response.headers)
        raise handle_api_error(response.status_code, error_data, retry_after=retry_after)

    async def _send_stream_request(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        request_data: Dict[str, Any],
        request_timeout: httpx.Timeout,
    ) -> Dict[str, Any]:
        """
        发送流式请求并解析响应。
        """
        # 大请求体 JSON 序列化与编码移至工作线程，避免阻塞事件循环；
        # 直接产出 bytes，httpx 收到 bytes 即跳过事件循环内的 encode
        json_bytes = await asyncio.to_thread(lambda: json.dumps(request_data).encode("utf-8"))
        async with client.stream(
            "POST", url, content=json_bytes, timeout=request_timeout
        ) as response:
            self.logger.debug("收到响应: 状态码={}", response.status_code)
            await self._raise_for_stream_response_status(response)

            if is_sse_response(response):
                return await parse_sse_response(
                    response,
                    model_id=self.config.model_id,
                    chunk_size=self.config.stream_chunk_size,
                    buffer_max_size=self.config.stream_buffer_max_size,
                    log=self.logger,
                )

            try:
                raw_body = await response.aread()
                # JSON 解析为同步 CPU 操作，移至工作线程避免阻塞事件循环；
                # 直接传入 bytes，json.loads 自 3.6 起接受 bytes 并在工作线程内完成 decode
                payload = await asyncio.to_thread(json.loads, raw_body)
            except Exception as exc:
                raise SeedreamAPIError(f"JSON 解析失败: {str(exc)}") from exc
            return self._build_api_result(payload)

    async def _send_standard_request(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        request_data: Dict[str, Any],
        request_timeout: httpx.Timeout,
    ) -> Dict[str, Any]:
        """
        发送非流式请求并解析响应。
        """
        # 多图融合的 base64 请求体可达数十 MB，其 JSON 序列化与编码移至工作线程以避免阻塞事件循环；
        # 直接产出 bytes，httpx 收到 bytes 即跳过事件循环内的 encode
        json_bytes = await asyncio.to_thread(lambda: json.dumps(request_data).encode("utf-8"))
        response = await client.post(url, content=json_bytes, timeout=request_timeout)

        self.logger.debug("收到响应: 状态码={}", response.status_code)
        self._raise_for_response_status(response)

        try:
            # response.json() 为同步 CPU 解析，大响应体可能阻塞事件循环，移至工作线程
            payload = await asyncio.to_thread(response.json)
        except Exception as exc:
            raise SeedreamAPIError(f"JSON 解析失败: {str(exc)}") from exc
        return self._build_api_result(payload)

    async def _call_api(self, endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用 Seedream API

        按 request_data 是否含 stream 标志分发到流式或非流式发送路径。失败时按错误类型
        分类处理：4xx 客户端错误（429 除外）立即抛出，429 与 5xx、超时及网络错误按指数
        退避或服务端 Retry-After 重试，重试次数用尽后抛出对应的 Seedream 异常。
        """
        await self._ensure_client()
        client = self._get_http_client()

        url = self._build_generation_url()
        safe_request_data = self._sanitize_request_for_logging(request_data)
        request_timeout = self._build_http_timeout()
        # max_retries 表示首次失败后的重试次数，故总尝试次数为其加一，与下载重试语义一致
        total_attempts = max(1, self.config.max_retries + 1)

        is_stream = bool(request_data.get("stream"))
        for attempt in range(total_attempts):
            pending_retry_after: Optional[float] = None
            try:
                self._log_request_attempt(
                    endpoint=endpoint,
                    attempt=attempt,
                    total_attempts=total_attempts,
                    url=url,
                    safe_request_data=safe_request_data,
                )

                if is_stream:
                    return await self._send_stream_request(
                        client=client,
                        url=url,
                        request_data=request_data,
                        request_timeout=request_timeout,
                    )

                return await self._send_standard_request(
                    client=client,
                    url=url,
                    request_data=request_data,
                    request_timeout=request_timeout,
                )
            except SeedreamAPIError as exc:
                status_code = exc.status_code or 0
                # 429 表示限流，退避后重试；其余 4xx 客户端错误不可重试直接抛出
                if 400 <= status_code < 500 and status_code != 429:
                    self.logger.warning(
                        "{} API 调用失败（状态码={}），不再重试: {}",
                        endpoint,
                        status_code,
                        exc.message,
                    )
                    raise

                self.logger.warning(
                    "{} API 调用失败 (尝试 {}/{}): {}",
                    endpoint,
                    attempt + 1,
                    total_attempts,
                    exc.message,
                )
                pending_retry_after = exc.retry_after
                if attempt == total_attempts - 1:
                    raise
            except httpx.TimeoutException as exc:
                self.logger.warning(
                    "{} API 调用超时 (尝试 {}/{}): {}",
                    endpoint,
                    attempt + 1,
                    total_attempts,
                    str(exc),
                )
                if attempt == total_attempts - 1:
                    raise SeedreamTimeoutError(f"{endpoint} API 调用超时") from exc
            except httpx.RequestError as exc:
                self.logger.warning(
                    "{} 网络错误 (尝试 {}/{}): {}",
                    endpoint,
                    attempt + 1,
                    total_attempts,
                    str(exc),
                )
                if attempt == total_attempts - 1:
                    raise SeedreamNetworkError(f"{endpoint} 网络连接失败: {str(exc)}") from exc
            except Exception as exc:
                # 编程 bug、序列化失败、值错误等非可重试意外错误直接抛出，不浪费退避等待。
                # 前三个分支已精确覆盖可重试场景：429/5xx 业务状态码、超时、网络/传输错误。
                self.logger.warning(
                    "{} API 调用出现非预期错误，不再重试 (尝试 {}/{}): {}",
                    endpoint,
                    attempt + 1,
                    total_attempts,
                    str(exc),
                )
                raise

            if attempt < total_attempts - 1:
                # 优先采用服务器 Retry-After 建议，否则指数退避；均叠加抖动避免并发限流时同步重试。
                # 注意：超时与网络错误重试可能触发服务端重复处理与计费，因生成 API 非幂等且当前未发送幂等键
                base = pending_retry_after if pending_retry_after is not None else float(2**attempt)
                # 单次退避上限 60 秒，避免 Retry-After 接近 300s 时单次 sleep 过久
                await asyncio.sleep(min(base + random.uniform(0, 1), 60))

        # 循环不会正常结束：每次迭代成功则 return，末次迭代失败时各 except 分支均 raise；
        # 此 raise 仅满足类型检查器对全路径返回的要求，运行时不可达
        raise SeedreamAPIError(f"{endpoint} API 调用意外结束")

    @staticmethod
    def _local_file_signature(image: str, workspace_roots: Tuple[str, ...]) -> Tuple[float, int]:
        """本地文件返回 (mtime, size) 参与缓存键，内容替换后失效避免返回陈旧编码；
        URL 与 data URI 内容由字符串决定、无法定位文件时返回 (0.0, 0)。相对路径按
        workspace_roots 解析后再 stat，与 _prepare_local_image 的实际读取路径一致。"""
        lowered = image.lower()
        if lowered.startswith(("http://", "https://", "data:image/")):
            return (0.0, 0)
        # 绝对路径直接作为候选；相对路径按工作区根解析，避免 CWD 与工作区不一致时 stat 错误文件。
        # 候选路径在 exists/stat 前统一做越界守卫，避免对工作区外文件成为存在性 oracle
        base_dirs = tuple(Path(r) for r in workspace_roots)
        candidate: Optional[Path] = None
        if os.path.isabs(image):
            candidate = Path(image)
        else:
            for root in base_dirs:
                resolved = root / image
                if not is_path_within_any_base(resolved, base_dirs):
                    continue
                if resolved.exists():
                    candidate = resolved
                    break
        if candidate is None:
            return (0.0, 0)
        if not is_path_within_any_base(candidate, base_dirs):
            return (0.0, 0)
        try:
            stat_result = os.stat(candidate)
        except OSError:
            return (0.0, 0)
        return (stat_result.st_mtime, stat_result.st_size)

    async def _prepare_image_input(
        self, image: str, _roots_key: Optional[Tuple[str, ...]] = None
    ) -> str:
        """
        准备图像输入数据。

        将图像 URL 或本地文件路径转换为 API 所需格式。结果按 (输入, workspace_roots,
        本地文件签名) 缓存，避免并行请求对同一参考图重复读取与编码，并以工作区隔离键
        避免跨租户命中；本地文件纳入 mtime+size 防内容替换返回陈旧编码。缓存超限按 LRU
        淘汰；同一键的并发 miss 复用同一在途 task（single-flight）。实现委托
        :mod:`seedream_mcp.utils.image_input`。
        """
        if _roots_key is None:
            from .utils.path_utils import get_workspace_roots

            _roots_key = tuple(str(r) for r in get_workspace_roots())
        cache_key: _PrepareCacheKey = (
            image,
            _roots_key,
            self._local_file_signature(image, _roots_key),
        )

        cached = self._prepare_cache.get(cache_key)
        if cached is not None:
            self._prepare_cache.move_to_end(cache_key)
            return cached

        inflight = self._prepare_inflight.get(cache_key)
        if inflight is not None:
            # shield 隔离取消传播：等待者被取消时仅取消其自身 await 的 outer，
            # 底层共享 task 继续运行，保护其他等待者与缓存写入
            return await asyncio.shield(inflight)

        task = asyncio.ensure_future(self._prepare_and_cache(image, cache_key))
        self._prepare_inflight[cache_key] = task
        # shield 隔离取消传播：创建者被取消时仅取消其自身 await 的 outer，底层共享
        # task 继续运行至完成，_prepare_inflight 由 task 完成时的 finally 清理，
        # 保护其他等待者不被连带取消，缓存正常写入
        return await asyncio.shield(task)

    async def _prepare_and_cache(self, image: str, cache_key: _PrepareCacheKey) -> str:
        """执行图像预处理并写入 LRU 缓存，供 single-flight 去重复用。

        inflight 在本 task 完成时清理；创建者被取消时 task 继续运行直至完成，
        保护共享同一 task 的其他等待者，避免连带取消。
        """
        from .utils.image_input import prepare_image_input

        try:
            prepared = await prepare_image_input(image)
            self._prepare_cache[cache_key] = prepared
            if len(self._prepare_cache) > self._prepare_cache_max:
                self._prepare_cache.popitem(last=False)
            return prepared
        finally:
            self._prepare_inflight.pop(cache_key, None)

    async def _prepare_images_in_parallel(self, images: Sequence[str]) -> List[str]:
        """
        受限并发预处理多张图片
        """
        from .utils.path_utils import get_workspace_roots

        concurrency = max(1, self._image_prepare_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        # 批内预计算一次工作区键，避免每图重复读取 ContextVar 与构造元组
        roots_key = tuple(str(r) for r in get_workspace_roots())

        async def _prepare_with_limit(image: str) -> str:
            async with semaphore:
                return await self._prepare_image_input(image, roots_key)

        tasks = [_prepare_with_limit(image) for image in images]
        return await asyncio.gather(*tasks)

    def _handle_api_error(self, error: Exception) -> Exception:
        """
        处理 API 错误

        将通用异常转换为特定的 Seedream 错误类型，
        根据错误信息自动识别超时、网络等特定错误。

        Args:
            error: 原始异常对象

        Returns:
            处理后的 Seedream 特定异常对象
        """
        if isinstance(
            error,
            (
                SeedreamAPIError,
                SeedreamValidationError,
                SeedreamTimeoutError,
                SeedreamNetworkError,
            ),
        ):
            return error

        # _call_api 内部已将 httpx 异常归类为 Seedream 超时/网络错误，此处兜底包装其余异常；
        # 附 __cause__ 保持异常链
        wrapped = SeedreamAPIError(f"API 调用失败: {error}")
        wrapped.__cause__ = error
        return wrapped
