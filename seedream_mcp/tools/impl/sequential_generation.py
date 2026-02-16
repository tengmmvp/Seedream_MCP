"""
组图输出工具模块
"""

from __future__ import annotations

# 标准库导入
from typing import TYPE_CHECKING, Any, Dict, List, cast

# 第三方库导入
from mcp.types import TextContent

# 项目内部导入 - 配置
from ...config import SeedreamConfig
from ...utils.logging import get_logger

# 项目内部导入 - 核心功能
from ..core.common import (
    execute_generation_handler,
    GenerationExecutionContext,
)

if TYPE_CHECKING:
    from ...client import SeedreamClient

# 模块日志记录器
logger = get_logger(__name__)


async def handle_sequential_generation(
    arguments: Dict[str, Any], config: SeedreamConfig
) -> List[TextContent]:
    """
    处理组图输出请求

    执行批量图片生成任务，支持提示词优化、多种响应格式、水印配置及自动保存功能。
    根据用户配置参数调用 API 生成多张图片，并可选择性地将结果保存至本地。

    Args:
        arguments: 请求参数字典，包含以下键值：
            - prompt (str): 生成图片的提示词描述
            - max_images (int, optional): 最大生成图片数量；未提供时由客户端按参考图自动推导
            - image (str, optional): 参考图片的 URL 或 Base64 编码
            - size (str, optional): 图片尺寸规格
            - watermark (bool, optional): 是否添加水印
            - response_format (str, optional): 响应格式，"url" 或 "b64_json"，默认为 "url"
            - stream (bool, optional): 是否启用流式输出，默认为 False
            - request_count (int, optional): 并行请求次数，默认 1，范围 1-4
            - parallelism (int, optional): 并行度上限，默认 min(request_count, 4)，范围 1-4
            - optimize_prompt_options (dict, optional): 提示词优化选项
            - auto_save (bool, optional): 是否自动保存图片
            - save_path (str, optional): 自定义保存路径
            - custom_name (str, optional): 自定义文件名前缀

    Returns:
        包含文本内容的列表，通常只有一个元素，描述生成任务的执行结果、图片信息及保存状态。

    Raises:
        Exception: 当生成过程中发生错误时，捕获异常并返回格式化的错误提示信息。
    """
    max_images = arguments.get("max_images")
    image = arguments.get("image")

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> Dict[str, Any]:
        result = await client.sequential_generation(
            prompt=context.prompt,
            max_images=max_images,
            size=context.size,
            watermark=context.watermark,
            response_format=context.response_format,
            image=image,
            stream=context.stream,
            optimize_prompt_options=context.optimize_prompt_options,
        )
        return cast(Dict[str, Any], result)

    return await execute_generation_handler(
        arguments=arguments,
        config=config,
        module_logger=logger,
        tool_name="sequential_generation",
        completion_title="组图输出任务完成",
        failure_prefix="组图输出",
        guidance="请检查提示词、数量与图片参数，确认 API Key 和网络可用后重试。",
        start_log_message=(
            "组图输出开始: prompt_len={}, max_images={}, size={}, stream={}, "
            "request_count={}, parallelism={}"
        ),
        start_log_values_builder=lambda ctx: (
            len(ctx.prompt or ""),
            max_images,
            ctx.size,
            ctx.stream,
            ctx.request_count,
            ctx.parallelism,
        ),
        request_executor=_execute,
    )
