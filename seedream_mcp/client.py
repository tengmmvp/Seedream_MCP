"""
Seedream MCP工具 - 客户端模块

本模块提供 Seedream MCP工具的客户端封装，支持文生图、图生图、
多图融合和组图生成等功能。
"""

# 标准库导入
import asyncio
import base64
from typing import Any, Dict, List, Optional, Union

# 第三方库导入
import httpx

# 本地模块导入
from .config import SeedreamConfig, get_global_config
from .utils.errors import SeedreamAPIError, SeedreamNetworkError, SeedreamTimeoutError
from .utils.logging import get_logger, log_function_call
from .utils.path_utils import suggest_similar_paths, validate_image_path
from .utils.validation import (
    validate_image_list,
    validate_image_url,
    validate_max_images,
    validate_prompt,
    validate_response_format,
    validate_size_for_model,
    validate_watermark,
    validate_optimize_prompt_options,
)


class SeedreamClient:
    """
    Seedream 4.0 API 客户端类
    
    提供异步 HTTP 客户端封装，支持多种图像生成功能：
    - 文生图（text_to_image）
    - 图生图（image_to_image）
    - 多图融合（multi_image_fusion）
    - 组图生成（sequential_generation）
    
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
        self._client = None

    async def __aenter__(self):
        """
        异步上下文管理器入口
        
        创建并初始化 HTTP 客户端连接。
        
        Returns:
            SeedreamClient: 当前客户端实例
        """
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
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
        size: str = "2K",
        watermark: bool = True,
        response_format: str = "url",
        stream: bool = False,
        optimize_prompt_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        文生图功能
        
        根据文本提示词生成图像。
        
        Args:
            prompt: 文本提示词，描述要生成的图像内容
            size: 图像尺寸，可选值为 "1K"、"2K"、"4K"，默认为 "2K"
            watermark: 是否添加水印，默认为 True
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            stream: 是否使用流式传输，默认为 False
            optimize_prompt_options: 提示词优化选项，可选配置字典
        
        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等
        
        Raises:
            SeedreamAPIError: API 调用失败
            SeedreamValidationError: 参数验证失败
        """
        # 参数验证
        prompt = validate_prompt(prompt)
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)

        self.logger.info(f"开始文生图任务: prompt='{prompt[:50]}...', size={size}")

        try:
            # 构建请求参数
            request_data = {
                "model": self.config.model_id,
                "prompt": prompt,
                "size": size,
                "watermark": watermark,
                "response_format": response_format
            }
            if stream:
                request_data["stream"] = True
            validated_opts = validate_optimize_prompt_options(optimize_prompt_options, self.config.model_id)
            if validated_opts:
                request_data["optimize_prompt_options"] = validated_opts

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
        image: str,
        size: str = "2K",
        watermark: bool = True,
        response_format: str = "url",
        stream: bool = False,
        optimize_prompt_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        图生图功能
        
        基于输入图像和文本提示词生成新图像。
        
        Args:
            prompt: 文本提示词，描述要对输入图像进行的修改或转换
            image: 输入图像的 URL 或本地文件路径
            size: 图像尺寸，可选值为 "1K"、"2K"、"4K"，默认为 "2K"
            watermark: 是否添加水印，默认为 True
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            stream: 是否使用流式传输，默认为 False
            optimize_prompt_options: 提示词优化选项，可选配置字典
        
        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等
        
        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败
        """
        # 参数验证
        prompt = validate_prompt(prompt)
        image = validate_image_url(image)
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)

        self.logger.info(f"开始图生图任务: prompt='{prompt[:50]}...', size={size}")

        try:
            # 处理图像输入
            image_data = await self._prepare_image_input(image)

            # 构建请求参数
            request_data = {
                "model": self.config.model_id,
                "prompt": prompt,
                "image": image_data,
                "size": size,
                "watermark": watermark,
                "response_format": response_format
            }
            if stream:
                request_data["stream"] = True
            validated_opts = validate_optimize_prompt_options(optimize_prompt_options, self.config.model_id)
            if validated_opts:
                request_data["optimize_prompt_options"] = validated_opts

            # 调用 API
            response = await self._call_api("image_to_image", request_data)

            self.logger.info("图生图任务完成")
            return response

        except Exception as e:
            self.logger.error(f"图生图任务失败: {str(e)}")
            raise self._handle_api_error(e)

    @log_function_call
    async def multi_image_fusion(
        self,
        prompt: str,
        images: List[str],
        size: str = "2K",
        watermark: bool = True,
        response_format: str = "url",
        stream: bool = False,
        optimize_prompt_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        多图融合功能
        
        将多张图像融合生成新图像。
        
        Args:
            prompt: 文本提示词，描述要对输入图像进行的融合操作
            images: 输入图像的 URL 或本地文件路径列表，数量范围为 2-5 张
            size: 图像尺寸，可选值为 "1K"、"2K"、"4K"，默认为 "2K"
            watermark: 是否添加水印，默认为 True
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            stream: 是否使用流式传输，默认为 False
            optimize_prompt_options: 提示词优化选项，可选配置字典
        
        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等
        
        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败（如图像数量不符合要求）
        """
        # 参数验证
        prompt = validate_prompt(prompt)
        images = validate_image_list(images, min_count=2, max_count=5)
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)

        self.logger.info(f"开始多图融合任务: prompt='{prompt[:50]}...', images={len(images)}张, size={size}")

        try:
            # 处理图像输入
            image_data_list = []
            for image in images:
                image_data = await self._prepare_image_input(image)
                image_data_list.append(image_data)

            # 构建请求参数
            request_data = {
                "model": self.config.model_id,
                "prompt": prompt,
                "images": image_data_list,
                "size": size,
                "watermark": watermark,
                "response_format": response_format
            }
            if stream:
                request_data["stream"] = True
            validated_opts = validate_optimize_prompt_options(optimize_prompt_options, self.config.model_id)
            if validated_opts:
                request_data["optimize_prompt_options"] = validated_opts

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
        max_images: int = 4,
        size: str = "2K",
        watermark: bool = True,
        response_format: str = "url",
        image: Optional[Union[str, List[str]]] = None,
        stream: bool = False,
        optimize_prompt_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        组图生成功能（连续生成多张图像）
        
        支持三种输入模式：
        1. 文生组图：仅使用文本提示词
        2. 单图生组图：使用单张参考图像和文本提示词
        3. 多图生组图：使用多张参考图像和文本提示词
        
        Args:
            prompt: 文本提示词，描述要生成的图像内容
            max_images: 最大生成图像数量，范围为 1-15，默认为 4
            size: 图像尺寸，可选值为 "1K"、"2K"、"4K"，默认为 "2K"
            watermark: 是否添加水印，默认为 True
            response_format: 响应格式，可选值为 "url" 或 "b64_json"，默认为 "url"
            image: 可选的参考图像，支持单张图像 URL/路径或多张图像 URL/路径列表（最多 10 张）
            stream: 是否使用流式传输，默认为 False
            optimize_prompt_options: 提示词优化选项，可选配置字典
        
        Returns:
            包含生成结果的字典，包括图像数据、使用信息和状态等
        
        Raises:
            SeedreamAPIError: API 调用失败或图像处理失败
            SeedreamValidationError: 参数验证失败
        """
        # 参数验证
        prompt = validate_prompt(prompt)
        max_images = validate_max_images(max_images)
        size = validate_size_for_model(size, self.config.model_id)
        watermark = validate_watermark(watermark)
        response_format = validate_response_format(response_format)

        # 处理图像输入
        processed_image = None
        if image is not None:
            if isinstance(image, str):
                # 单张图片
                processed_image = await self._prepare_image_input(image)
            elif isinstance(image, list):
                # 多张图片
                if len(image) > 10:
                    raise SeedreamAPIError("最多支持 10 张参考图片")
                processed_image = []
                for img in image:
                    processed_img = await self._prepare_image_input(img)
                    processed_image.append(processed_img)
            else:
                raise SeedreamAPIError("image 参数必须是字符串或字符串列表")

        self.logger.info(f"开始组图生成任务: prompt='{prompt[:50]}...', max_images={max_images}, size={size}")

        try:
            # 构建请求参数
            request_data = {
                "model": self.config.model_id,
                "prompt": prompt,
                "sequential_image_generation": "auto",
                "sequential_image_generation_options": {
                    "max_images": max_images
                },
                "size": size,
                "watermark": watermark,
                "response_format": response_format
            }
            if stream:
                request_data["stream"] = True
            validated_opts = validate_optimize_prompt_options(optimize_prompt_options, self.config.model_id)
            if validated_opts:
                request_data["optimize_prompt_options"] = validated_opts

            # 添加图像参数
            if processed_image is not None:
                request_data["image"] = processed_image

            # 调用 API
            response = await self._call_api("sequential_generation", request_data)

            self.logger.info("组图生成任务完成")
            return response

        except Exception as e:
            self.logger.error(f"组图生成任务失败: {str(e)}")
            raise self._handle_api_error(e)

    async def close(self):
        """
        关闭 HTTP 客户端连接
        
        释放客户端资源，关闭所有打开的连接。
        """
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self):
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
                    timeout=self.config.api_timeout,
                    headers=headers
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
            "Content-Type": "application/json"
        }

        self.logger.debug(f"生成请求头: Authorization=Bearer {self.config.api_key[:10]}...")
        return headers

    async def _call_api(
        self,
        endpoint: str,
        request_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        调用 Seedream API
        
        执行 HTTP POST 请求，支持流式和非流式两种传输模式，
        实现自动重试机制和指数退避策略。
        
        Args:
            endpoint: API 端点标识（用于日志记录）
            request_data: 请求体数据
        
        Returns:
            包含成功标志、数据、使用信息和状态等的响应字典
        
        Raises:
            SeedreamAPIError: API 调用失败或响应解析失败
            SeedreamTimeoutError: 请求超时
            SeedreamNetworkError: 网络连接失败
        """
        await self._ensure_client()

        # 验证客户端是否正确创建
        if self._client is None:
            raise SeedreamAPIError("HTTP 客户端未正确初始化")

        # 构建 URL（Seedream 4.0 API 仅有一个端点）
        url = f"{self.config.base_url}/images/generations"

        for attempt in range(self.config.max_retries):
            try:
                self.logger.debug(f"{endpoint} API 调用尝试 {attempt + 1}/{self.config.max_retries}")
                self.logger.debug(f"请求 URL: {url}")
                self.logger.debug(f"请求数据: {request_data}")

                # 确保客户端的 post 方法存在且可调用
                if not hasattr(self._client, 'post') or not callable(self._client.post):
                    raise SeedreamAPIError("HTTP 客户端的 post 方法不可用")

                # 流式传输模式
                if request_data.get("stream"):
                    async with self._client.stream("POST", url, json=request_data, timeout=self.config.api_timeout) as response:
                        if response is None:
                            raise SeedreamAPIError("API 响应为空")
                        self.logger.debug(f"收到响应: 状态码={response.status_code}")
                        if response.status_code != 200:
                            error_text = (await response.aread()).decode("utf-8", errors="ignore")
                            raise SeedreamAPIError(f"HTTP {response.status_code}: {error_text}")
                        # 处理 SSE 流式响应
                        if response.headers.get("content-type", "").startswith("text/event-stream"):
                            import json
                            items: List[Dict[str, Any]] = []
                            usage: Dict[str, Any] = {}
                            status: Optional[str] = None
                            # 使用 aiter_bytes 或 aread 读取流式数据
                            if hasattr(response, "aiter_bytes"):
                                buffer = b""
                                async for chunk in response.aiter_bytes():
                                    if not chunk:
                                        continue
                                    buffer += chunk
                                    # 处理完整的事件段
                                    while b"\n\n" in buffer:
                                        seg, buffer = buffer.split(b"\n\n", 1)
                                        s = seg.strip()
                                        if not s:
                                            continue
                                        try:
                                            text = s.decode("utf-8")
                                            lines = text.split("\n")
                                            payload = None
                                            # 提取 data 行
                                            for ln in reversed(lines):
                                                if ln.startswith("data:"):
                                                    payload = ln[5:].strip()
                                                    break
                                            if not payload or payload == "[DONE]":
                                                continue
                                            evt = json.loads(payload)
                                        except Exception:
                                            continue
                                        # 处理事件类型
                                        t = evt.get("type")
                                        if t == "image_generation.partial_succeeded":
                                            items.append({
                                                "url": evt.get("url"),
                                                "b64_json": evt.get("b64_json"),
                                                "size": evt.get("size"),
                                                "image_index": evt.get("image_index"),
                                            })
                                        elif t == "image_generation.partial_failed":
                                            continue
                                        elif t == "image_generation.completed":
                                            usage = evt.get("usage", {}) or {}
                                            status = "completed"
                            else:
                                # 一次性读取全部数据
                                body = await response.aread()
                                segments = body.split(b"\n\n")
                                for seg in segments:
                                    s = seg.strip()
                                    if not s:
                                        continue
                                    try:
                                        text = s.decode("utf-8")
                                        lines = text.split("\n")
                                        payload = None
                                        for ln in reversed(lines):
                                            if ln.startswith("data:"):
                                                payload = ln[5:].strip()
                                                break
                                        if not payload or payload == "[DONE]":
                                            continue
                                        evt = json.loads(payload)
                                    except Exception:
                                        continue
                                    t = evt.get("type")
                                    if t == "image_generation.partial_succeeded":
                                        items.append({
                                            "url": evt.get("url"),
                                            "b64_json": evt.get("b64_json"),
                                            "size": evt.get("size"),
                                            "image_index": evt.get("image_index"),
                                        })
                                    elif t == "image_generation.partial_failed":
                                        continue
                                    elif t == "image_generation.completed":
                                        usage = evt.get("usage", {}) or {}
                                        status = "completed"
                            return {
                                "success": True,
                                "data": items,
                                "usage": usage,
                                "status": status,
                            }
                        else:
                            # 非 SSE 响应
                            text = await response.aread()
                            import json
                            parsed = json.loads(text.decode("utf-8"))
                            return {
                                "success": True,
                                "data": parsed.get("data", []),
                                "usage": parsed.get("usage", {}),
                                "status": parsed.get("status"),
                            }
                else:
                    # 非流式传输模式
                    response = await self._client.post(
                        url,
                        json=request_data,
                        timeout=self.config.api_timeout
                    )

                # 验证响应对象
                if response is None:
                    raise SeedreamAPIError("API 响应为空")

                self.logger.debug(f"收到响应: 状态码={response.status_code}")

                # 检查 HTTP 状态码
                if response.status_code == 200:
                    # 解析 JSON 响应
                    try:
                        result = response.json()
                        self.logger.debug(f"解析 JSON 成功: {result}")
                    except Exception as json_error:
                        raise SeedreamAPIError(f"JSON 解析失败: {str(json_error)}")

                    return {
                        "success": True,
                        "data": result.get("data", []),
                        "usage": result.get("usage", {}),
                        "status": result.get("status")
                    }
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    raise SeedreamAPIError(error_msg)

            except httpx.TimeoutException:
                self.logger.warning(f"{endpoint} API 调用超时 (尝试 {attempt + 1}/{self.config.max_retries})")
                if attempt == self.config.max_retries - 1:
                    raise SeedreamTimeoutError(f"{endpoint} API 调用超时")

            except httpx.NetworkError as e:
                self.logger.warning(f"{endpoint} 网络错误 (尝试 {attempt + 1}/{self.config.max_retries}): {str(e)}")
                if attempt == self.config.max_retries - 1:
                    raise SeedreamNetworkError(f"{endpoint} 网络连接失败: {str(e)}")

            except Exception as e:
                self.logger.warning(f"{endpoint} API 调用失败 (尝试 {attempt + 1}/{self.config.max_retries}): {str(e)}")
                if attempt == self.config.max_retries - 1:
                    raise

            # 指数退避重试
            await asyncio.sleep(2 ** attempt)

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
            # 如果是 URL，直接返回
            if image.startswith(("http://", "https://")):
                return image

            # 验证图片路径
            is_valid, error_msg, normalized_path = validate_image_path(image)

            if not is_valid:
                # 提供路径建议
                suggestions = suggest_similar_paths(image)
                suggestion_text = ""
                if suggestions:
                    suggestion_text = f"\n\n建议的相似路径:\n" + "\n".join(f"  • {s}" for s in suggestions[:3])

                raise SeedreamAPIError(f"{error_msg}{suggestion_text}")

            # 读取文件并转换为 Base64
            with open(normalized_path, "rb") as f:
                image_bytes = f.read()

            # 转换为 Base64
            image_b64 = base64.b64encode(image_bytes).decode('utf-8')

            # 获取 MIME 类型
            suffix = normalized_path.suffix.lower()
            mime_type_map = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.bmp': 'image/bmp',
                '.tiff': 'image/tiff',
                '.tif': 'image/tiff',
                '.webp': 'image/webp',
                '.svg': 'image/svg+xml',
                '.ico': 'image/x-icon'
            }
            mime_type = mime_type_map.get(suffix, 'image/jpeg')

            self.logger.info(f"成功处理图片文件: {normalized_path} ({len(image_bytes)} bytes)")
            return f"data:{mime_type};base64,{image_b64}"

        except SeedreamAPIError:
            raise
        except Exception as e:
            raise SeedreamAPIError(f"图像处理失败: {str(e)}")

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
        if isinstance(error, (SeedreamAPIError, SeedreamTimeoutError, SeedreamNetworkError)):
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
