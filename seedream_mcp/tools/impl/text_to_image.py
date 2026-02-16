"""
文生图工具模块
"""

from __future__ import annotations

# 标准库导入
from typing import Any, TYPE_CHECKING, Dict, List, cast

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


async def handle_text_to_image(
    arguments: Dict[str, Any], config: SeedreamConfig
) -> List[TextContent]:
    """
    处理文生图请求

    根据用户提供的文本提示词生成图片，支持尺寸配置、水印添加、提示词优化及自动保存等功能。
    调用 API 生成图片后，可选择性地将结果保存至本地并返回统一格式的响应。

    Args:
        arguments: 请求参数字典,包含以下键值：
            - prompt (str): 生成图片的文本提示词
            - optimize_prompt_options (dict, optional): 提示词优化选项配置
            - size (str, optional): 图片尺寸规格
            - watermark (bool, optional): 是否添加水印
            - response_format (str, optional): 响应格式，"url" 或 "b64_json"，默认为 "url"
            - stream (bool, optional): 是否启用流式输出，默认为 False
            - request_count (int, optional): 并行请求次数，默认 1，范围 1-4
            - parallelism (int, optional): 并行度上限，默认 min(request_count, 4)，范围 1-4
            - auto_save (bool, optional): 是否自动保存生成的图片
            - save_path (str, optional): 自定义图片保存路径
            - custom_name (str, optional): 自定义文件名前缀

    Returns:
        包含文本内容的列表，通常只有一个元素，描述生成任务的执行结果、图片 URL 或 Base64 数据及保存状态。

    Raises:
        Exception: 当生成过程中发生错误时，捕获异常并返回格式化的错误提示信息。
    """

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> Dict[str, Any]:
        result = await client.text_to_image(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
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
        tool_name="text_to_image",
        completion_title="文生图任务完成",
        failure_prefix="文生图生成",
        guidance="请检查提示词长度、尺寸与模型兼容性，确认 API Key 和网络可用后重试。",
        start_log_message=(
            "文生图开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
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
