"""
图文生图工具模块
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, cast

from mcp.types import TextContent

from ...config import SeedreamConfig
from ...utils.logging import get_logger
from ..core.common import (
    execute_generation_handler,
    GenerationExecutionContext,
)

if TYPE_CHECKING:
    from ...client import SeedreamClient

logger = get_logger(__name__)


async def handle_image_to_image(
    arguments: Dict[str, Any], config: SeedreamConfig
) -> List[TextContent]:
    """
    处理图文生图请求

    根据输入的图像和提示词生成新图像，支持多种配置选项包括尺寸、水印、
    响应格式、流式输出、提示词优化及自动保存等功能。

    Args:
        arguments: 请求参数字典，支持以下键值：
            - prompt (str, optional): 生成图像的提示词描述
            - optimize_prompt_options (dict, optional): 提示词优化选项
            - image (str): 输入图像的路径或URL
            - size (str, optional): 生成图像尺寸，默认使用配置中的默认值
            - watermark (bool, optional): 是否添加水印，默认使用配置中的默认值
            - response_format (str, optional): 响应格式，支持 "url" 或 "b64_json"，默认为 "url"
            - stream (bool, optional): 是否启用流式输出，默认为 False
            - request_count (int, optional): 并行请求次数，默认 1，范围 1-4
            - parallelism (int, optional): 并行度上限，默认 min(request_count, 4)，范围 1-4
            - auto_save (bool, optional): 是否自动保存生成的图像，默认使用配置中的默认值
            - save_path (str, optional): 自定义保存路径
            - custom_name (str, optional): 自定义文件名

    Returns:
        List[TextContent]: 包含生成结果或错误信息的文本内容列表，
                          成功时返回图像URL/Base64数据及元数据，
                          失败时返回格式化的错误信息和操作指引。

    Raises:
        Exception: 捕获所有异常并转换为用户友好的错误消息，不向上层抛出。
    """
    image = arguments.get("image")

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> Dict[str, Any]:
        result = await client.image_to_image(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
            image=image,
            size=context.size,
            watermark=context.watermark,
            response_format=context.response_format,
            stream=context.stream,
        )
        return cast(Dict[str, Any], result)

    return await execute_generation_handler(
        arguments=arguments,
        config=config,
        module_logger=logger,
        tool_name="image_to_image",
        completion_title="图文生图任务完成",
        failure_prefix="图文生图生成",
        guidance="请检查图片路径/URL 与尺寸参数，确认 API Key 和网络可用后重试。",
        start_log_message=(
            "图文生图开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
        ),
        start_log_values_builder=lambda ctx: (
            len(ctx.prompt or ""),
            ctx.size,
            ctx.stream,
            ctx.request_count,
            ctx.parallelism,
        ),
        request_executor=_execute,
    )
