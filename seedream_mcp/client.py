"""
Seedream MCP工具 - 客户端模块
"""

# 标准库导入
import asyncio
import base64
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Sequence, Union, cast
from urllib.parse import urlparse

# 第三方库导入
import httpx

# 本地模块导入
from .config import SeedreamConfig, get_global_config
from .utils.errors import (
    SeedreamAPIError,
    SeedreamNetworkError,
    SeedreamTimeoutError,
    SeedreamValidationError,
    handle_api_error,
)
from .utils.logging import get_logger, log_function_call
from .utils.path_utils import get_workspace_roots, suggest_similar_paths, validate_image_path
from .utils.validation import (
    validate_generation_tools,
    validate_image_url,
    validate_max_images,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_prompt,
    validate_response_format,
    validate_sequential_image_limit,
    validate_size_for_model,
    validate_watermark,
)


class SeedreamClient:
    """
    Seedream MCP API 客户端类

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
        self.config = config or get_global_config()
        self.logger = get_logger(__name__)
        self._client: Optional[httpx.AsyncClient] = None
        self._image_prepare_concurrency = 5

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
            output_format: 输出图片格式，仅 doubao-seedream-5.0 支持 "jpeg" 或 "png"
            stream: 是否使用流式传输，默认为 False
            tools: 模型工具配置，仅 doubao-seedream-5.0 支持，如 [{"type": "web_search"}]

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等

        Raises:
            SeedreamAPIError: API 调用失败
            SeedreamValidationError: 参数验证失败
        """
        # 参数验证
        prompt = validate_prompt(prompt)
        validated_opts = validate_optimize_prompt_options(
            optimize_prompt_options, self.config.model_id
        )
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)
        output_format = validate_output_format(output_format, self.config.model_id)
        tools = validate_generation_tools(tools, self.config.model_id)

        self.logger.info(
            "开始文生图任务: prompt_meta={}, size={}",
            self._summarize_prompt(prompt),
            size,
        )

        try:
            # 构建请求参数
            request_data: Dict[str, Any] = {
                "model": self.config.model_id,
                "prompt": prompt,
            }
            if validated_opts:
                request_data["optimize_prompt_options"] = validated_opts
            request_data.update(
                {
                    "size": size,
                    "watermark": watermark,
                    "response_format": response_format,
                }
            )
            if output_format is not None:
                request_data["output_format"] = output_format
            if stream:
                request_data["stream"] = True
            if tools:
                request_data["tools"] = tools

            # 调用 API
            response = await self._call_api("text_to_image", request_data)

            self.logger.info("文生图任务完成")
            return response

        except Exception as e:
            self.logger.error(f"文生图任务失败: {str(e)}")
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
            output_format: 输出图片格式，仅 doubao-seedream-5.0 支持 "jpeg" 或 "png"
            stream: 是否使用流式传输，默认为 False
            tools: 模型工具配置，仅 doubao-seedream-5.0 支持，如 [{"type": "web_search"}]

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败
        """
        # 参数验证
        prompt = validate_prompt(prompt)
        validated_opts = validate_optimize_prompt_options(
            optimize_prompt_options, self.config.model_id
        )
        image = self._normalize_single_image(image)
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)
        output_format = validate_output_format(output_format, self.config.model_id)
        tools = validate_generation_tools(tools, self.config.model_id)

        self.logger.info(
            "开始图文生图任务: prompt_meta={}, size={}",
            self._summarize_prompt(prompt),
            size,
        )

        try:
            # 处理图像输入
            image_data = await self._prepare_image_input(image)

            # 构建请求参数
            request_data: Dict[str, Any] = {
                "model": self.config.model_id,
                "prompt": prompt,
            }
            if validated_opts:
                request_data["optimize_prompt_options"] = validated_opts
            request_data.update(
                {
                    "image": image_data,
                    "size": size,
                    "watermark": watermark,
                    "response_format": response_format,
                }
            )
            if output_format is not None:
                request_data["output_format"] = output_format
            if stream:
                request_data["stream"] = True
            if tools:
                request_data["tools"] = tools

            # 调用 API
            response = await self._call_api("image_to_image", request_data)

            self.logger.info("图文生图任务完成")
            return response

        except Exception as e:
            self.logger.error(f"图文生图任务失败: {str(e)}")
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
            image: 输入图像的 URL 或本地文件路径列表，数量范围为 2-14 张
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，默认为 "2K"
            watermark: 是否添加水印，默认为 False
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            output_format: 输出图片格式，仅 doubao-seedream-5.0 支持 "jpeg" 或 "png"
            stream: 是否使用流式传输，默认为 False
            tools: 模型工具配置，仅 doubao-seedream-5.0 支持，如 [{"type": "web_search"}]

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败
        """
        # 参数验证
        prompt = validate_prompt(prompt)
        validated_opts = validate_optimize_prompt_options(
            optimize_prompt_options, self.config.model_id
        )
        image = self._normalize_image_sequence(image, min_count=2, max_count=14, field_name="image")
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)
        output_format = validate_output_format(output_format, self.config.model_id)
        tools = validate_generation_tools(tools, self.config.model_id)

        self.logger.info(
            "开始多图融合任务: prompt_meta={}, image_count={}, size={}",
            self._summarize_prompt(prompt),
            len(image),
            size,
        )

        try:
            # 处理图像输入
            image_data_list = await self._prepare_images_in_parallel(image)

            # 构建请求参数
            request_data: Dict[str, Any] = {
                "model": self.config.model_id,
                "prompt": prompt,
            }
            if validated_opts:
                request_data["optimize_prompt_options"] = validated_opts
            request_data.update(
                {
                    "image": image_data_list,
                    "sequential_image_generation": "disabled",
                    "size": size,
                    "watermark": watermark,
                    "response_format": response_format,
                }
            )
            if output_format is not None:
                request_data["output_format"] = output_format
            if stream:
                request_data["stream"] = True
            if tools:
                request_data["tools"] = tools

            # 调用 API
            response = await self._call_api("multi_image_fusion", request_data)

            self.logger.info("多图融合任务完成")
            return response

        except Exception as e:
            self.logger.error(f"多图融合任务失败: {str(e)}")
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
        组图输出功能

        支持通过一张或者多张图片和文字信息，生成漫画分镜、品牌视觉等一组内容关联的图片。

        支持三种输入模式：
        1. 文生组图：仅使用文本提示词
        2. 单图生组图：使用单张参考图像和文本提示词
        3. 多图生组图：使用多张参考图像和文本提示词

        Args:
            prompt: 文本提示词，描述要生成的图像内容
            optimize_prompt_options: 提示词优化选项，可选配置字典
            image: 可选的参考图像，支持单张图像 URL/路径或多张图像 URL/路径列表（参考图数量与生成数量之和不超过 15）
            size: 图像尺寸，支持与当前模型兼容的 "1K"、"2K"、"3K"、"4K" 或 "<宽>x<高>" 像素值，默认为 "2K"
            watermark: 是否添加水印，默认为 False
            max_images: 最大生成图像数量，范围为 1-15；未传入时无参考图默认 15，有参考图时自动扣减以满足总量上限
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            output_format: 输出图片格式，仅 doubao-seedream-5.0 支持 "jpeg" 或 "png"
            stream: 是否使用流式传输，默认为 False
            tools: 模型工具配置，仅 doubao-seedream-5.0 支持，如 [{"type": "web_search"}]

        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等

        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败
        """
        # 参数验证
        prompt = validate_prompt(prompt)
        validated_opts = validate_optimize_prompt_options(
            optimize_prompt_options, self.config.model_id
        )
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        output_format = validate_output_format(output_format, self.config.model_id)
        tools = validate_generation_tools(tools, self.config.model_id)

        # 处理图像输入
        processed_image: Optional[Union[str, List[str]]] = None
        reference_images = None
        if image is not None:
            if isinstance(image, str):
                # 单张图片
                reference_images = [image]
            elif isinstance(image, (list, tuple)):
                # 多张图片
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
                max_count=14,
                field_name="image",
            )

        resolved_max_images = max_images
        if resolved_max_images is None:
            resolved_max_images = 15 - len(reference_images) if reference_images is not None else 15
        resolved_max_images = validate_max_images(resolved_max_images)

        if reference_images is not None:
            validate_sequential_image_limit(resolved_max_images, reference_images)

        response_format = validate_response_format(response_format)

        if reference_images is not None:
            if len(reference_images) == 1:
                processed_image = await self._prepare_image_input(reference_images[0])
            else:
                processed_image = await self._prepare_images_in_parallel(reference_images)

        self.logger.info(
            "开始组图输出任务: prompt_meta={}, max_images={}, size={}",
            self._summarize_prompt(prompt),
            resolved_max_images,
            size,
        )

        try:
            # 构建请求参数
            request_data: Dict[str, Any] = {
                "model": self.config.model_id,
                "prompt": prompt,
                "sequential_image_generation": "auto",
            }
            if validated_opts:
                request_data["optimize_prompt_options"] = validated_opts
            if processed_image is not None:
                request_data["image"] = processed_image
            request_data.update(
                {
                    "size": size,
                    "watermark": watermark,
                    "sequential_image_generation_options": {"max_images": resolved_max_images},
                    "response_format": response_format,
                }
            )
            if output_format is not None:
                request_data["output_format"] = output_format
            if stream:
                request_data["stream"] = True
            if tools:
                request_data["tools"] = tools

            # 调用 API
            response = await self._call_api("sequential_generation", request_data)

            self.logger.info("组图输出任务完成")
            return response

        except Exception as e:
            self.logger.error(f"组图输出任务失败: {str(e)}")
            raise self._handle_api_error(e)

    async def close(self) -> None:
        """
        关闭 HTTP 客户端连接

        释放客户端资源，关闭所有打开的连接。
        """
        if self._client:
            await self._client.aclose()
            self._client = None

    def _build_http_timeout(self) -> httpx.Timeout:
        """
        构建统一超时策略

        - `timeout`：连接建立/连接池获取/请求写入阶段
        - `api_timeout`：响应读取阶段与总超时上限
        """
        base_timeout = float(self.config.timeout)
        api_timeout = float(self.config.api_timeout)
        return httpx.Timeout(
            timeout=api_timeout,
            connect=base_timeout,
            read=api_timeout,
            write=base_timeout,
            pool=base_timeout,
        )

    async def _ensure_client(self) -> None:
        """
        确保 HTTP 客户端已创建

        如果客户端未初始化，则创建新的 AsyncClient 实例，
        并配置请求头和超时设置。

        Raises:
            SeedreamAPIError: 客户端创建失败或配置无效
        """
        if self._client is None:
            try:
                headers = self._get_headers()
                if not headers:
                    raise SeedreamAPIError("无法生成请求头：配置可能无效")

                self._client = httpx.AsyncClient(
                    timeout=self._build_http_timeout(),
                    headers=headers,
                )

                # 验证客户端是否正确创建
                if self._client is None:
                    raise SeedreamAPIError("HTTP 客户端创建失败")

                self.logger.debug("HTTP 客户端创建成功")

            except Exception as e:
                self.logger.error(f"HTTP 客户端创建失败: {str(e)}")
                self._client = None
                raise SeedreamAPIError(f"HTTP 客户端初始化失败: {str(e)}")

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
    def _normalize_single_image(image: object) -> str:
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
        images: object,
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
        """
        对请求体做日志脱敏
        """
        safe_data: Dict[str, Any] = {}
        for key, value in request_data.items():
            if key == "prompt":
                prompt_length = len(value) if isinstance(value, str) else 0
                safe_data[key] = f"<redacted:{prompt_length} chars>"
                continue
            if key == "image":
                safe_data[key] = self._summarize_image_field(value)
                continue
            safe_data[key] = value
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
        """
        统一归一化 API 返回结果结构。
        """
        data = payload.get("data")
        if isinstance(data, list):
            data_count = len(data)
        elif data is None:
            data_count = 0
        else:
            data_count = 1

        self.logger.debug(
            "解析 JSON 成功: status={}, data_count={}",
            payload.get("status"),
            data_count,
        )
        return {
            "success": True,
            "data": payload.get("data", []),
            "usage": payload.get("usage", {}),
            "status": payload.get("status"),
        }

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
        raise handle_api_error(response.status_code, error_data)

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
        raise handle_api_error(response.status_code, error_data)

    @staticmethod
    def _is_sse_response(response: httpx.Response) -> bool:
        """
        判断响应是否为 SSE 事件流。
        """
        content_type = str(response.headers.get("content-type", ""))
        return content_type.startswith("text/event-stream")

    @staticmethod
    def _format_sse_success_event(event: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        """
        将 SSE 成功事件转换为统一图片项结构。
        """
        return {
            "url": event.get("url"),
            "b64_json": event.get("b64_json"),
            "size": event.get("size"),
            "image_index": event.get("image_index"),
            "model": event.get("model", model_id),
            "created": event.get("created", int(time.time())),
            "type": event.get("type", "image_generation.partial_succeeded"),
        }

    @staticmethod
    def _format_sse_failed_event(event: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        """
        将 SSE 失败事件转换为统一图片项结构。
        """
        raw_error = event.get("error")
        error = raw_error if isinstance(raw_error, dict) else {}
        return {
            "error": {
                "code": error.get("code"),
                "message": error.get("message"),
            },
            "image_index": event.get("image_index"),
            "model": event.get("model", model_id),
            "created": event.get("created", int(time.time())),
            "type": event.get("type", "image_generation.partial_failed"),
        }

    def _parse_sse_segment(self, segment: bytes) -> Optional[Dict[str, Any]]:
        """
        解析单个 SSE 事件段，返回事件对象。
        """
        raw_segment = segment.strip()
        if not raw_segment:
            return None

        try:
            event_text = raw_segment.decode("utf-8")
            payload: Optional[str] = None
            for line in reversed(event_text.split("\n")):
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    break
            if not payload or payload == "[DONE]":
                return None
            parsed_payload = json.loads(payload)
            if not isinstance(parsed_payload, dict):
                raise ValueError("SSE 事件数据必须是对象")
            return cast(Dict[str, Any], parsed_payload)
        except Exception as exc:
            self.logger.error("SSE事件解析失败: {}", str(exc))
            self.logger.debug("SSE事件原始段长度: {} bytes", len(raw_segment))
            return None

    async def _parse_sse_response(self, response: httpx.Response) -> Dict[str, Any]:
        """
        解析 SSE 响应为统一结构。
        """
        items: List[Dict[str, Any]] = []
        usage: Dict[str, Any] = {}
        status: Optional[str] = None

        buffer = b""
        max_buffer_size = self.config.stream_buffer_max_size
        processed_bytes = 0

        async for chunk in response.aiter_bytes(self.config.stream_chunk_size):
            if not chunk:
                continue

            buffer += chunk
            processed_bytes += len(chunk)

            if len(buffer) > max_buffer_size:
                self.logger.warning(
                    "缓冲区大小超限 ({} > {})，清理旧数据",
                    len(buffer),
                    max_buffer_size,
                )
                buffer = buffer[-1024 * 1024 :]

            if processed_bytes > 0 and processed_bytes % (1024 * 1024) == 0:
                self.logger.debug("已处理 {} MB 数据", processed_bytes // 1024 // 1024)

            while b"\n\n" in buffer:
                segment, buffer = buffer.split(b"\n\n", 1)
                event = self._parse_sse_segment(segment)
                if event is None:
                    continue

                event_type = event.get("type")
                if event_type == "image_generation.partial_succeeded":
                    items.append(self._format_sse_success_event(event, self.config.model_id))
                elif event_type == "image_generation.partial_failed":
                    items.append(self._format_sse_failed_event(event, self.config.model_id))
                elif event_type == "image_generation.completed":
                    usage = event.get("usage", {}) or {}
                    status = "completed"

        trailing_event = self._parse_sse_segment(buffer)
        if trailing_event is not None:
            event_type = trailing_event.get("type")
            if event_type == "image_generation.partial_succeeded":
                items.append(self._format_sse_success_event(trailing_event, self.config.model_id))
            elif event_type == "image_generation.partial_failed":
                items.append(self._format_sse_failed_event(trailing_event, self.config.model_id))
            elif event_type == "image_generation.completed":
                usage = trailing_event.get("usage", {}) or {}
                status = "completed"

        return {
            "success": True,
            "data": items,
            "usage": usage,
            "status": status,
        }

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
        async with client.stream(
            "POST", url, json=request_data, timeout=request_timeout
        ) as response:
            self.logger.debug("收到响应: 状态码={}", response.status_code)
            await self._raise_for_stream_response_status(response)

            if self._is_sse_response(response):
                return await self._parse_sse_response(response)

            try:
                payload = json.loads((await response.aread()).decode("utf-8"))
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
        response = await client.post(url, json=request_data, timeout=request_timeout)
        if response is None:
            raise SeedreamAPIError("API 响应为空")

        self.logger.debug("收到响应: 状态码={}", response.status_code)
        self._raise_for_response_status(response)

        try:
            payload = response.json()
        except Exception as exc:
            raise SeedreamAPIError(f"JSON 解析失败: {str(exc)}") from exc
        return self._build_api_result(payload)

    async def _call_api(self, endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用 Seedream API
        """
        await self._ensure_client()
        client = self._get_http_client()

        url = self._build_generation_url()
        safe_request_data = self._sanitize_request_for_logging(request_data)
        request_timeout = self._build_http_timeout()
        total_attempts = max(1, self.config.max_retries)

        for attempt in range(total_attempts):
            try:
                self._log_request_attempt(
                    endpoint=endpoint,
                    attempt=attempt,
                    total_attempts=total_attempts,
                    url=url,
                    safe_request_data=safe_request_data,
                )

                if request_data.get("stream"):
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
                if 400 <= status_code < 500:
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
                self.logger.warning(
                    "{} API 调用失败 (尝试 {}/{}): {}",
                    endpoint,
                    attempt + 1,
                    total_attempts,
                    str(exc),
                )
                if attempt == total_attempts - 1:
                    raise

            if attempt < total_attempts - 1:
                await asyncio.sleep(2**attempt)

        raise SeedreamAPIError(f"{endpoint} API 调用重试次数已用尽")

    async def _prepare_image_input(self, image: str) -> str:
        """
        准备图像输入数据

        将图像 URL 或本地文件路径转换为 API 所需格式。
        对于 URL 直接返回，对于本地文件读取并转换为 Base64 编码的 Data URI。

        Args:
            image: 图像 URL 或本地文件路径

        Returns:
            处理后的图像数据（URL 或 Base64 Data URI）

        Raises:
            SeedreamAPIError: 图像文件不存在或处理失败
        """
        try:
            normalized_image = image.strip()
            workspace_roots = get_workspace_roots()

            # 如果是 URL，直接返回
            if normalized_image.startswith(("http://", "https://")):
                parsed = urlparse(normalized_image)
                if not parsed.netloc:
                    raise SeedreamAPIError(f"无效的图像 URL: {normalized_image}")
                return normalized_image

            # 如果是 Data URI，直接返回
            if normalized_image.lower().startswith("data:image/"):
                return validate_image_url(normalized_image)

            if not workspace_roots:
                raise SeedreamAPIError("当前 MCP 会话未授权任何工作区目录，无法读取本地图片。")

            # 验证图片路径
            validated_path = None
            validation_errors: List[str] = []
            for root in workspace_roots:
                is_valid, error_msg, normalized_path = validate_image_path(
                    normalized_image, base_dir=str(root)
                )
                if is_valid and normalized_path is not None:
                    validated_path = normalized_path
                    break
                if error_msg:
                    validation_errors.append(error_msg)

            if validated_path is None:
                error_text = "图像路径校验失败"
                if validation_errors:
                    error_text = "；".join(dict.fromkeys(validation_errors))

                # 提供路径建议
                suggestions = suggest_similar_paths(
                    image,
                    search_dirs=[str(root) for root in workspace_roots],
                )
                suggestion_text = ""
                if suggestions:
                    suggestion_text = "\n\n建议的相似路径:\n" + "\n".join(
                        f"  • {s}" for s in suggestions[:3]
                    )

                raise SeedreamAPIError(f"{error_text}{suggestion_text}")

            # 读取文件并转换为 Base64
            image_bytes = await asyncio.to_thread(validated_path.read_bytes)

            # 转换为 Base64
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            # 获取 MIME 类型
            suffix = validated_path.suffix.lower()
            mime_type_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
                ".tiff": "image/tiff",
                ".webp": "image/webp",
            }
            mime_type = mime_type_map.get(suffix, "image/jpeg")

            self.logger.info(f"成功处理图片文件: {validated_path} ({len(image_bytes)} bytes)")
            return f"data:{mime_type};base64,{image_b64}"

        except (SeedreamAPIError, SeedreamValidationError):
            raise
        except Exception as e:
            raise SeedreamAPIError(f"图像处理失败: {str(e)}")

    async def _prepare_images_in_parallel(self, images: Sequence[str]) -> List[str]:
        """
        受限并发预处理多张图片
        """
        concurrency = max(1, self._image_prepare_concurrency)
        semaphore = asyncio.Semaphore(concurrency)

        async def _prepare_with_limit(image: str) -> str:
            async with semaphore:
                return await self._prepare_image_input(image)

        tasks = [_prepare_with_limit(image) for image in images]
        return list(await asyncio.gather(*tasks))

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

        error_str = str(error)

        # 超时错误
        if "timeout" in error_str.lower():
            return SeedreamTimeoutError(f"API 调用超时: {error_str}")

        # 网络错误
        if any(keyword in error_str.lower() for keyword in ["connection", "network", "dns"]):
            return SeedreamNetworkError(f"网络连接失败: {error_str}")

        # 其他 API 错误
        return SeedreamAPIError(f"API 调用失败: {error_str}")
