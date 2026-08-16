"""Seedream MCP 客户端模块。

定义 :class:`SeedreamClient`，封装火山引擎 Seedream 系列图像生成 API 的调用。
该类同时作为公共库 API 与 MCP 工具后端，提供文生图、图文生图、多图融合、
组图生成四种入口，入口处重新校验参数，与工具层形成 defense-in-depth。

内置图像预处理 LRU 缓存与 single-flight 去重、流式与非流式统一解析、
指数退避重试、请求与响应脱敏与异常分类；并行批次经 :class:`SharedRequestPlan`
共享单份 request_data 与序列化 body，构建与序列化各恰好发生一次。
"""

import asyncio
import hashlib
import json
import random
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Iterator, Sequence, cast

import httpx

from .config import SeedreamConfig, get_active_config
from .utils.core.errors import (
    SeedreamAPIError,
    SeedreamConfigError,
    SeedreamNetworkError,
    SeedreamTimeoutError,
    SeedreamValidationError,
    format_error_for_user,
    handle_api_error,
    parse_retry_after,
)
from .utils.core.logs import get_logger, log_function_call
from .utils.model.model_capabilities import get_max_reference_images, get_model_capabilities
from .utils.core.validators import (
    ValidatedCommonParams,
    resolve_sequential_max_images,
    validate_background,
    validate_common_generation_params,
    validate_layer_decomposition,
    validate_max_images,
    validate_sequential_image_limit,
)
from .utils.io.io_sse import is_sse_response, parse_sse_response
from .utils.images.image_ref import classify_image_reference
from .utils.images.image_prepare import ImagePreparer

# 指数退避单次等待上限
_MAX_BACKOFF_SECONDS = 60

# 错误响应体独立读取上限
_ERROR_BODY_BYTE_LIMIT = 4 * 1024 * 1024

# SSE 事件信封余量
_SSE_EVENT_ENVELOPE_MARGIN = 4 * 1024

# 响应体 join 卸载阈值
_JOIN_OFFLOAD_THRESHOLD = 8 * 1024 * 1024


class SharedRequestPlan:
    """单次工具调用内并行请求的共享计划。

    同一批次的多并行请求经本对象共享同一份 request_data 与序列化 body：构建与序列化
    各恰好发生一次，N 个请求的峰值内存为 1×body。计划由 tools 并行层在批次执行期间经
    ``shared_request_plan_scope`` 绑定到当前上下文，client 各生成方法与 ``_call_api``
    读取绑定值；批次结束后由作用域退出统一复位并调用 release 释放引用，body 不滞留至
    自动保存等后续阶段，对象随批次回收、不跨调用常驻。

    request_data 在共享期间不可变：构建完成后各生成方法与 ``_call_api`` 仅读取、不改写。

    单方法批次约束：get_or_build 与 get_or_serialize 的无锁快路径按计划内已有即复用
    判定，不校验产物归属于哪个生成方法。同一作用域内先后调用两个不同的生成方法时，
    后到方法会复用先到方法的 request_data 与 body，产出错误方法的请求。当前唯一调用方
    tools 并行层在单次工具调用内只分发一个 request_executor，批次内生成方法恒定，
    本约束由调用方保证；新增调用方须维持单方法批次语义，或先在键控机制中纳入方法
    标识再复用本计划。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.request_data: dict[str, Any] | None = None
        self.body: bytes | None = None
        self.validated_common_params: tuple[tuple[Any, ...], ValidatedCommonParams] | None = None

    async def get_or_build(
        self, builder: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """返回共享 request_data：首个到者执行 builder 构建，其余复用同一 dict。

        builder 在锁内执行，同批请求的图像预处理与请求字典组装只发生一次；构建抛出时
        不写入计划，各调用方独立失败，后到者在锁内自行重试构建。
        """
        if self.request_data is not None:
            return self.request_data
        async with self._lock:
            if self.request_data is None:
                self.request_data = await builder()
            return cast(dict[str, Any], self.request_data)

    async def get_or_serialize(
        self,
        request_data: dict[str, Any],
        serializer: Callable[[dict[str, Any]], bytes],
    ) -> bytes:
        """返回共享 body：首个到者序列化一次，其余复用同一 bytes 对象。

        锁覆盖序列化全程，同批并发调用排队等待首份 body 产出，而非各自持有一份等大
        拷贝；request_data 须为 ``get_or_build`` 返回的同一共享 dict。
        """
        if self.body is not None:
            return self.body
        async with self._lock:
            if self.body is None:
                self.body = await asyncio.to_thread(serializer, request_data)
            return cast(bytes, self.body)

    def release(self) -> None:
        """批次执行结束后清除共享引用，避免大 body 滞留至自动保存等后续阶段。"""
        self.request_data = None
        self.body = None
        self.validated_common_params = None


# 当前批次的共享计划绑定：tools 并行层在批次执行期间设置、结束后复位。contextvars
# 按 asyncio 任务上下文隔离，并发批次互不可见；未绑定的直连调用读取到 None，走独立
# 构建与序列化路径，公共 API 行为不受影响。
_ACTIVE_REQUEST_PLAN: ContextVar[SharedRequestPlan | None] = ContextVar(
    "seedream_active_request_plan", default=None
)


@contextmanager
def shared_request_plan_scope() -> Iterator[SharedRequestPlan]:
    """绑定新的共享请求计划至当前上下文，退出时复位绑定并释放计划引用。

    供 tools 并行层包裹批次请求分发：作用域内的 client 生成调用读取绑定计划，同批
    请求只构建一次 request_data、只序列化一次 body。以 with 使用，异常与取消路径
    均经 finally 复位。
    """
    plan = SharedRequestPlan()
    token = _ACTIVE_REQUEST_PLAN.set(plan)
    try:
        yield plan
    finally:
        _ACTIVE_REQUEST_PLAN.reset(token)
        plan.release()


async def _build_request_data(
    plan: SharedRequestPlan | None,
    builder: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """按共享计划构建 request_data：无计划直接构建，有计划经单飞复用同批结果。"""
    if plan is None:
        return await builder()
    return await plan.get_or_build(builder)


def _has_valid_image_items(data: Any) -> bool:
    """判定 200 响应的 data 字段是否含至少一个非错误的图片条目。

    list 形态须存在不含 error 键的 dict 条目；dict 形态本身计为一个条目，含 error
    键时视为失败占位；None 与标量形态无图片。供 _build_api_result 的顶层 error
    守卫判定请求级软失败。
    """
    if isinstance(data, list):
        return any(isinstance(item, dict) and "error" not in item for item in data)
    if isinstance(data, dict):
        return "error" not in data
    return False


class SeedreamClient:
    """Seedream API 客户端。

    各生成方法在入口对参数重新校验，与 tools 工具层的校验形成 defense-in-depth，
    确保公共库 API 与 MCP 工具后端两种调用路径行为一致。

    Attributes:
        config: 客户端配置对象。
        logger: 日志记录器实例。
    """

    def __init__(self, config: SeedreamConfig | None = None):
        """初始化 Seedream API 客户端。

        Args:
            config: 配置对象，若为 None 则使用全局默认配置。
        """
        self.config = config or get_active_config()
        self.logger = get_logger(__name__)
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

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
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
        """构建各生成方法共享的请求参数基础字典。

        size/watermark/response_format/output_format/stream/tools 的组装逻辑在四个生成
        方法中完全相同，集中于此避免漂移；方法特有的字段如参考图、组图选项等通过 extra 并入。
        prompt 为 None 时不写入该键，对应图层拆分场景的缺省提示词，由模型自动识别
        拆分意图。
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

    @log_function_call
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

        通过给模型提供清晰准确的文字指令，即可快速获得符合描述的高质量单张图片。

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
        ) = self._validate_common_generation_params(
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
            request_data = await _build_request_data(_ACTIVE_REQUEST_PLAN.get(), _build_request)

            response = await self._call_api("text_to_image", request_data)

            self.logger.info("文生图任务完成")
            return response

        except Exception as e:
            raise self._finalize_generation_error("文生图", e)

    @log_function_call
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

        基于已有图片，结合文字指令进行图像编辑。

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
        ) = self._validate_common_generation_params(
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
            request_data = await _build_request_data(_ACTIVE_REQUEST_PLAN.get(), _build_request)

            response = await self._call_api("image_to_image", request_data)

            self.logger.info("图文生图任务完成")
            return response

        except Exception as e:
            raise self._finalize_generation_error("图文生图", e)

    @log_function_call
    async def multi_image_fusion(
        self,
        prompt: str | None = None,
        optimize_prompt_options: dict[str, Any] | None = None,
        image: list[str] | None = None,
        size: str | None = None,
        watermark: bool | None = None,
        response_format: str = "url",
        output_format: str | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """融合多张参考图片的风格与元素生成新图像。

        根据输入的文本描述和多张参考图片，融合它们的风格、元素等特征来生成新图像。

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
        ) = self._validate_common_generation_params(
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
                extra={"image": image_data_list, "sequential_image_generation": "disabled"},
            )

        try:
            request_data = await _build_request_data(_ACTIVE_REQUEST_PLAN.get(), _build_request)

            response = await self._call_api("multi_image_fusion", request_data)

            self.logger.info("多图融合任务完成")
            return response

        except Exception as e:
            raise self._finalize_generation_error("多图融合", e)

    @log_function_call
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
        """生成一组内容关联的图片，仅 5.0/5.0 Lite/4.5/4.0 支持，5.0 Pro 不支持组图。

        支持通过一张或者多张图片和文字信息，生成漫画分镜、品牌视觉等一组内容关联的图片。

        支持三种输入模式：
        1. 文生组图：仅使用文本提示词
        2. 单图生组图：使用单张参考图像和文本提示词
        3. 多图生组图：使用多张参考图像和文本提示词

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
        """
        model_caps = get_model_capabilities(self.config.model_id)
        if not model_caps.supports_sequential_generation:
            raise SeedreamValidationError(
                f"{model_caps.display_name} 不支持组图生成，请切换为支持组图的模型",
                field="model",
                value=self.config.model_id,
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
        ) = self._validate_common_generation_params(
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
            request_data = await _build_request_data(_ACTIVE_REQUEST_PLAN.get(), _build_request)

            response = await self._call_api("sequential_generation", request_data)

            self.logger.info("组图输出任务完成")
            return response

        except Exception as e:
            raise self._finalize_generation_error("组图输出", e)

    def _validate_common_generation_params(
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

        委托 utils.core.validators.validate_common_generation_params 单一入口完成公共参数
        全量校验，作为公共库 API 的自校验层：工具链路经 schema 与 context 分层校验后仍会
        到达此处，直接调用 client 的库使用方则仅依赖本校验。size 与 watermark 未显式
        传入时按 config.default_size / default_watermark 兜底合成，与 tools 层
        build_generation_context 的合成语义一致，消除直连调用与配置的双源分叉；
        图层拆分场景 size 未显式传入时按官方默认取 auto。各方法特有的图片数量与
        序列校验仍在各自方法内执行。

        当前上下文绑定共享请求计划时按输入快照复用批内首次校验结果：批内各请求的
        公共参数相同，重复校验对结果无增量，仅重复 100k 级提示词的 CJK 计数扫描；
        缓存通常由 tools 并行层经 prevalidate_common_generation_params 在批次分发前
        写入，未经预校验的并发批次各请求独立校验，结果一致。直连调用未绑定计划，
        每次调用均独立校验，公共 API 行为不变。
        """
        if layer_decomposition and size is None:
            resolved_size = "auto"
        else:
            resolved_size = size if size is not None else self.config.default_size
        resolved_watermark = self.config.default_watermark if watermark is None else watermark
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
        plan = _ACTIVE_REQUEST_PLAN.get()
        if plan is not None:
            cached = plan.validated_common_params
            if cached is not None and cached[0] == inputs:
                return cached[1]
        validated = validate_common_generation_params(
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
        if plan is not None:
            plan.validated_common_params = (inputs, validated)
        return validated

    def prevalidate_common_generation_params(
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

        供 tools 并行层在批次请求分发前调用：批内公共参数全批相同，100k 级提示词的
        CJK 计数等重校验每批只发生一次，批内各生成方法在
        _validate_common_generation_params 内按输入快照命中缓存。校验失败立即上抛，
        异常类型与消息和单请求路径的首请求校验失败一致。未绑定共享计划时仅执行
        校验，无缓存效果。
        """
        self._validate_common_generation_params(
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
        """关闭 HTTP 客户端连接，释放资源。

        与 _ensure_client 共用 _client_lock 串行，避免并发关闭与首次创建交错，
        导致后续请求拿到 None 或已关闭的客户端。
        """
        async with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            await client.aclose()

    def _build_http_timeout(self) -> httpx.Timeout:
        """构建并缓存统一超时策略。

        首次构建后缓存到实例，避免每次请求重复构造。

        - `timeout`：连接建立、连接池获取与请求写入阶段的上限
        - `api_timeout`：响应读取阶段的单次读取间隔上限。httpx 的 read 超时按单次
          读操作计时而非整个响应的累计时长，流式响应持续慢滴流时不构成总时长约束
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
        """确保 HTTP 客户端已创建。

        首次创建经双检锁串行化，避免并发请求重复创建 httpx.AsyncClient 导致资源泄漏。

        Raises:
            SeedreamConfigError: API 密钥为空。配置类失败原样透传，保持 config_error
                归约档与配置排查指引，不落入下方 api_error 档的包装文案。
            SeedreamAPIError: 客户端创建失败或配置无效
        """
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    try:
                        headers = self._get_headers()
                        if not headers:
                            raise SeedreamAPIError("无法生成请求头：配置可能无效")

                        self._client = httpx.AsyncClient(
                            timeout=self._build_http_timeout(),
                            headers=headers,
                            trust_env=False,
                        )
                        self.logger.debug("HTTP 客户端创建成功")

                    except SeedreamConfigError:
                        self.logger.error("HTTP 客户端创建失败: API 密钥为空")
                        raise
                    except Exception as e:
                        self.logger.error("HTTP 客户端创建失败: {}", e)
                        self._client = None
                        raise SeedreamAPIError(f"HTTP 客户端初始化失败: {str(e)}") from e

    def _get_headers(self) -> dict[str, str]:
        """构建带 Bearer 认证与 JSON Content-Type 的请求头。

        Raises:
            SeedreamConfigError: API 密钥为空。密钥缺失属部署配置问题而非 API 调用
                失败，config_error 归约档使调用方得到配置排查指引。
        """
        if not self.config.api_key:
            raise SeedreamConfigError("API 密钥为空，请检查环境变量 ARK_API_KEY")

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        self.logger.debug("生成请求头: Authorization=Bearer ***")
        return headers

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
        return normalized

    @staticmethod
    def _normalize_image_sequence(
        images: Sequence[str] | None,
        *,
        min_count: int,
        max_count: int,
        field_name: str,
    ) -> list[str]:
        """校验并规范化图片列表输入。

        逐项规范化并按 min_count 与 max_count 校验数量。
        """
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

    @staticmethod
    def _summarize_image_field(image_value: Any) -> Any:
        """将 image 字段归约为可安全记录的摘要，避免 URL、data URI 与路径明文进入日志。"""
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

        kind = classify_image_reference(image_value)
        if kind == "url":
            return "<image_url>"
        if kind == "data_uri":
            return f"<data_uri:{len(image_value)} chars>"
        return "<local_image_path>"

    def _sanitize_request_for_logging(self, request_data: dict[str, Any]) -> dict[str, Any]:
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
        """获取已初始化的 HTTP 客户端实例。

        Raises:
            SeedreamAPIError: HTTP 客户端尚未初始化。
        """
        if self._client is None:
            raise SeedreamAPIError("HTTP 客户端未正确初始化")
        return self._client

    def _build_generation_url(self) -> str:
        """拼接图像生成端点 URL，归一化 base_url 尾部斜杠避免拼出双斜杠路径。

        ARK_BASE_URL 以 / 结尾时直接拼接会得到 //images/generations，部分网关按
        双斜杠路径拒绝路由；rstrip 在 client 侧单点归一化，config 层不重复处理。
        """
        return f"{self.config.base_url.rstrip('/')}/images/generations"

    def _log_request_attempt(
        self,
        *,
        endpoint: str,
        attempt: int,
        total_attempts: int,
        url: str,
        safe_request_data: dict[str, Any],
    ) -> None:
        """输出单次 API 调用尝试的调试日志，请求体须传入已脱敏副本。"""
        self.logger.debug(
            "{} API 调用尝试 {}/{}",
            endpoint,
            attempt + 1,
            total_attempts,
        )
        self.logger.debug("请求 URL: {}", url)
        self.logger.debug("请求数据(脱敏): {}", safe_request_data)

    @staticmethod
    def _require_dict_payload(payload: Any) -> dict[str, Any]:
        """校验 200 响应的 JSON 体为对象形态，非 dict 时抛出明确的格式错误。

        _build_api_result 以 dict 形态读取字段，list/str/null 等形态会触发
        AttributeError 形式的误导性报错；错误路径 _error_data_from_body 已对
        非 dict 体降级处理，成功路径在此对称拦截，进入结果构建前显式拒绝。
        """
        if not isinstance(payload, dict):
            # null 体映射为 JSON 术语，其余形态用类型名表述
            received = "null" if payload is None else type(payload).__name__
            raise SeedreamAPIError(f"响应格式错误: 期望 JSON 对象，实际收到 {received}")
        return payload

    def _build_api_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """统一归一化 API 返回结果结构。

        success 仅代表 HTTP 层成功，即已收到 200 响应；body 级的部分失败或空数据
        由 status 与 data 共同表达，status 取值为 completed/partial/failed，
        调用方应同时检查 status 而非仅依赖 success。顶层 error 为非空 dict 且 data
        无有效图片时属请求级软失败：success 置 False 并透传 error 键，调用方据此
        取回上游错误码与原因，不再被吞为成功零图。
        """
        data = payload.get("data")
        if isinstance(data, list):
            data_count = len(data)
        elif data is None:
            data_count = 0
        else:
            data_count = 1

        status = payload.get("status")
        if status is not None and not isinstance(status, str):
            self.logger.debug(
                "API 响应 status 字段非 str（{}），已收敛为 None",
                type(status).__name__,
            )
            status = None
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
        usage = payload.get("usage", {})
        if not isinstance(usage, dict):
            self.logger.debug(
                "API 响应 usage 字段非 dict（{}），已收敛为空 dict",
                type(usage).__name__,
            )
            usage = {}

        top_error = payload.get("error")
        if isinstance(top_error, dict) and top_error and not _has_valid_image_items(data):
            self.logger.warning(
                "200 响应携带顶层 error 且无有效图片，标记为请求级失败: code={}",
                top_error.get("code"),
            )
            return {
                "success": False,
                "data": data or [],
                "usage": usage,
                "status": "failed",
                "error": top_error,
                "tools": payload.get("tools"),
            }
        return {
            "success": True,
            "data": data or [],
            "usage": usage,
            "status": status,
            "tools": payload.get("tools"),
        }

    @staticmethod
    def _retry_after_or_none(status_code: int, headers: Any) -> float | None:
        """对可重试状态码（429/5xx）解析 Retry-After，其余返回 None。"""
        if status_code == 429 or status_code >= 500:
            return parse_retry_after(headers)
        return None

    @staticmethod
    def _serialize_request(request_data: dict[str, Any]) -> bytes:
        """将请求体流式序列化为 UTF-8 bytes，供 httpx 直接发送以跳过事件循环内编码。

        经 iterencode 分片逐片编码，避免先物化完整 str 再整体 encode 的双份临时
        拷贝；关闭 ensure_ascii 使中文等非 ASCII 字符以 UTF-8 原样输出，不被 ASCII
        转义序列膨胀。末尾保留一次 bytes(buffer) 拷贝：httpx 对 content 仅特判
        bytes 与 str，bytearray 会落入 Iterable 分支逐元素产出 int 而损坏请求体，
        故无法以零拷贝方式直接交出 buffer。并行请求的内存包络见 _call_api 注释。
        """
        encoder = json.JSONEncoder(ensure_ascii=False)
        buffer = bytearray()
        for chunk in encoder.iterencode(request_data):
            buffer += chunk.encode("utf-8")
        return bytes(buffer)

    def _raise_api_error_for(
        self,
        status_code: int,
        headers: Any,
        error_data: dict[str, Any],
    ) -> None:
        """按状态码与错误体装配并抛出统一 API 异常，标准与流式路径共用。"""
        retry_after = self._retry_after_or_none(status_code, headers)
        raise handle_api_error(status_code, error_data, retry_after=retry_after)

    def _response_body_byte_limit(self) -> int:
        """上游响应体读取总量上限，非流式 JSON、流式 JSON 与 SSE 三条路径共用。

        显式配置 response_body_limit 时直接生效。未配置时推导：组图单次请求最多
        返回 15 张图，b64_json 模式下单张图片的 base64 负载上限为
        auto_save_max_file_size 的 4/3（base64 将 3 字节编码为 4 字符），
        15 × 4/3 = 20，故 20 × auto_save_max_file_size 恰好覆盖合法最坏响应体，
        超过该值的响应只能来自异常或被污染的上游。
        """
        if self.config.response_body_limit is not None:
            return self.config.response_body_limit
        return self.config.auto_save_max_file_size * 20

    def _error_body_byte_limit(self) -> int:
        """错误路径读体上限：取响应体总量上限与 4MB 独立上限的较小值。"""
        return min(self._response_body_byte_limit(), _ERROR_BODY_BYTE_LIMIT)

    async def _read_response_body_capped(
        self, response: httpx.Response, *, max_bytes: int | None = None
    ) -> bytes:
        """流式读取响应体并施加总量上限，超限抛出 SeedreamAPIError。

        max_bytes 缺省时取 _response_body_byte_limit；错误路径传入
        _error_body_byte_limit 的独立小上限。Content-Length 头先做快速预检，
        超限时无需读取直接拒绝；chunked 或缺失 Content-Length 的响应在
        aiter_bytes 累计读取中强制上限，超限时中断读取并抛出携带实际读取字节数的
        错误。chunks 列表与 join 产物在返回前短暂并存，进程内存峰值约为已读字节
        的 2 倍，默认上限下可达约 2GB，部署方需按此峰值规划进程内存。
        响应的关闭由调用方负责（stream 上下文退出或显式 aclose）。
        """
        if max_bytes is None:
            max_bytes = self._response_body_byte_limit()
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                declared_bytes = -1
            if declared_bytes > max_bytes:
                raise SeedreamAPIError(
                    f"响应体过大: Content-Length 声明 {declared_bytes} 字节，"
                    f"超过上限 {max_bytes} 字节，可经 SEEDREAM_RESPONSE_BODY_LIMIT 调整"
                )
        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            received += len(chunk)
            if received > max_bytes:
                raise SeedreamAPIError(
                    f"响应体过大: 已读取 {received} 字节，超过上限 {max_bytes} 字节，"
                    f"可经 SEEDREAM_RESPONSE_BODY_LIMIT 调整"
                )
            chunks.append(chunk)
        if received > _JOIN_OFFLOAD_THRESHOLD:
            return await asyncio.to_thread(b"".join, chunks)
        return b"".join(chunks)

    @staticmethod
    async def _error_data_from_body(raw_body: bytes) -> dict[str, Any]:
        """将错误响应体归约为 handle_api_error 可消费的字典，非对象 JSON 体降级为 message。

        json.loads 与降级分支的 bytes decode 均为同步 CPU 操作，移至工作线程执行，
        避免大错误体在事件循环内解析或解码阻塞其他任务；解码文本经 handle_api_error
        截断后才进入异常 message。
        """
        try:
            parsed: Any = await asyncio.to_thread(json.loads, raw_body)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

        def _decode_as_message() -> dict[str, Any]:
            return {"message": raw_body.decode("utf-8", errors="ignore")}

        return await asyncio.to_thread(_decode_as_message)

    async def _raise_for_stream_response_status(self, response: httpx.Response) -> None:
        """将流式响应中的非 200 状态码转换为统一 API 异常。"""
        if response.status_code == 200:
            return

        raw_body = await self._read_response_body_capped(
            response, max_bytes=self._error_body_byte_limit()
        )
        error_data = await self._error_data_from_body(raw_body)
        self._raise_api_error_for(response.status_code, response.headers, error_data)

    async def _send_stream_request(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        request_body: bytes,
        request_timeout: httpx.Timeout,
    ) -> dict[str, Any]:
        """发送流式请求，将 SSE 或 JSON 响应解析为统一结果结构。"""
        async with client.stream(
            "POST", url, content=request_body, timeout=request_timeout
        ) as response:
            self.logger.debug("收到响应: 状态码={}", response.status_code)
            await self._raise_for_stream_response_status(response)

            if is_sse_response(response):
                sse_result = await parse_sse_response(
                    response,
                    model_id=self.config.model_id,
                    chunk_size=self.config.stream_chunk_size,
                    buffer_max_size=self.config.stream_buffer_max_size,
                    event_truncate_threshold=max(
                        self.config.stream_buffer_max_size,
                        4 * ((self.config.auto_save_max_file_size + 2) // 3)
                        + _SSE_EVENT_ENVELOPE_MARGIN,
                    ),
                    total_bytes_limit=self._response_body_byte_limit(),
                    log=self.logger,
                )
                truncated_events = sse_result.pop("truncated_events", 0)
                if isinstance(truncated_events, int) and truncated_events > 0:
                    sse_result["truncated_events"] = truncated_events
                return sse_result

            try:
                raw_body = await self._read_response_body_capped(response)
                payload = await asyncio.to_thread(json.loads, raw_body)
            except SeedreamAPIError:
                raise
            except Exception as exc:
                raise SeedreamAPIError(f"JSON 解析失败: {str(exc)}") from exc
            return self._build_api_result(self._require_dict_payload(payload))

    async def _send_standard_request(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        request_body: bytes,
        request_timeout: httpx.Timeout,
    ) -> dict[str, Any]:
        """发送非流式请求，将 JSON 响应解析为统一结果结构。

        以 build_request + send(stream=True) 发送：client.post 会使 httpx 先全量缓冲
        响应体，Content-Length 预检在缓冲完成后才生效，chunked 或缺失 Content-Length
        的巨型响应在缓冲阶段无任何拦截；流式发送使总量限额在接收过程中即强制执行。
        """
        request = client.build_request("POST", url, content=request_body, timeout=request_timeout)
        response = await client.send(request, stream=True)
        try:
            self.logger.debug("收到响应: 状态码={}", response.status_code)
            if response.status_code != 200:
                raw_body = await self._read_response_body_capped(
                    response, max_bytes=self._error_body_byte_limit()
                )
                error_data = await self._error_data_from_body(raw_body)
                self._raise_api_error_for(response.status_code, response.headers, error_data)

            raw_body = await self._read_response_body_capped(response)
            try:
                payload = await asyncio.to_thread(json.loads, raw_body)
            except Exception as exc:
                raise SeedreamAPIError(f"JSON 解析失败: {str(exc)}") from exc
            return self._build_api_result(self._require_dict_payload(payload))
        finally:
            await response.aclose()

    async def _call_api(self, endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        """调用 Seedream API。

        按 request_data 是否含 stream 标志分发到流式或非流式发送路径。失败时按错误类型
        分类处理：非 2xx 中仅 429 与 5xx 可重试，其余状态码（含 3xx 与 401-499）立即
        抛出；超时及网络错误按指数退避或服务端 Retry-After 重试，重试次数用尽后抛出
        对应的 Seedream 异常。
        """
        await self._ensure_client()
        client = self._get_http_client()

        url = self._build_generation_url()
        safe_request_data = self._sanitize_request_for_logging(request_data)
        request_timeout = self._build_http_timeout()
        plan = _ACTIVE_REQUEST_PLAN.get()
        if plan is None:
            request_body = await asyncio.to_thread(self._serialize_request, request_data)
        else:
            request_body = await plan.get_or_serialize(request_data, self._serialize_request)
        total_attempts = max(1, self.config.max_retries + 1)

        is_stream = bool(request_data.get("stream"))
        for attempt in range(total_attempts):
            pending_retry_after: float | None = None
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
                        request_body=request_body,
                        request_timeout=request_timeout,
                    )

                return await self._send_standard_request(
                    client=client,
                    url=url,
                    request_body=request_body,
                    request_timeout=request_timeout,
                )
            except SeedreamAPIError as exc:
                if exc.status_code is None:
                    self.logger.warning(
                        "{} API 调用失败（无 HTTP 状态码，不再重试）: {}",
                        endpoint,
                        exc.message,
                    )
                    raise
                status_code = exc.status_code
                if status_code != 429 and status_code < 500:
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
                self.logger.warning(
                    "{} API 调用出现非预期错误，不再重试 (尝试 {}/{}): {}",
                    endpoint,
                    attempt + 1,
                    total_attempts,
                    str(exc),
                )
                raise

            if attempt < total_attempts - 1:
                if pending_retry_after is not None:
                    await asyncio.sleep(pending_retry_after + random.uniform(0, 1))
                else:
                    await asyncio.sleep(
                        min(float(2**attempt) + random.uniform(0, 1), _MAX_BACKOFF_SECONDS)
                    )

        raise SeedreamAPIError(f"{endpoint} API 调用意外结束")

    async def _prepare_image_input(self, image: str) -> str:
        """准备图像输入数据，委托 ImagePreparer 预处理缓存子系统。"""
        return await self._image_preparer.prepare_image_input(image)

    async def _prepare_images_in_parallel(self, images: Sequence[str]) -> list[str]:
        """受限并发预处理多张图片，委托 ImagePreparer。"""
        return await self._image_preparer.prepare_images_in_parallel(images)

    def _normalize_api_error(self, error: Exception) -> Exception:
        """归一化 API 错误为 Seedream 错误类型。

        已是 Seedream 错误的异常原样返回；其余异常包装为 SeedreamAPIError。
        与 utils.core.errors.handle_api_error 的职责不同：后者按 HTTP 状态码装配
        异常，在 _call_api 内按响应分类，本方法仅兜底包装 _call_api 之外的异常。

        Args:
            error: 原始异常对象。

        Returns:
            处理后的 Seedream 特定异常对象。
        """
        if isinstance(
            error,
            (
                SeedreamAPIError,
                SeedreamConfigError,
                SeedreamValidationError,
                SeedreamTimeoutError,
                SeedreamNetworkError,
            ),
        ):
            return error

        wrapped = SeedreamAPIError(f"API 调用失败: {error}")
        wrapped.__cause__ = error
        return wrapped

    def _finalize_generation_error(self, task_label: str, error: Exception) -> Exception:
        """记录任务失败日志并返回归一化异常，供四生成方法 except 分支复用。

        各生成方法的异常收尾仅任务名不同，集中日志与归一化避免四处重复实现漂移。
        """
        self.logger.error("{}任务失败: {}", task_label, format_error_for_user(error))
        return self._normalize_api_error(error)
