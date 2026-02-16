"""
多图融合工具模块
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, cast

from mcp.types import CallToolResult

from ...config import SeedreamConfig
from ...utils.logging import get_logger
from ..core.common import (
    execute_generation_handler,
    GenerationExecutionContext,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from ...client import SeedreamClient

# 模块日志记录器
logger = get_logger(__name__)


async def handle_multi_image_fusion(
    arguments: Dict[str, Any],
    config: SeedreamConfig,
    ctx: "Context[Any, Any, Any] | None" = None,
) -> CallToolResult:
    """
    处理多图融合请求

    根据提供的参数执行多图融合任务，包括参数验证、API调用、结果处理及可选的自动保存功能。
    支持流式响应和提示词优化，可将结果返回为URL或Base64编码格式。

    Args:
        arguments: 融合任务参数字典，包含以下字段：
            - prompt (str, optional): 融合描述提示词，默认为空字符串
            - optimize_prompt_options (Dict, optional): 提示词优化选项
            - image (List): 待融合的图片列表（2-14 张）
            - size (str, optional): 生成图片尺寸，默认使用配置中的默认值
            - watermark (bool, optional): 是否添加水印，默认使用配置中的默认值
            - response_format (str, optional): 响应格式，支持 'url' 或 'b64_json'，默认为 'url'
            - stream (bool, optional): 是否启用流式响应，默认为 False
            - request_count (int, optional): 并行请求次数，默认 1，范围 1-4
            - parallelism (int, optional): 并行度上限，默认 min(request_count, 4)，范围 1-4
            - auto_save (bool, optional): 是否自动保存结果，默认使用配置中的默认值
            - save_path (str, optional): 保存路径，默认使用配置中的路径
            - custom_name (str, optional): 自定义文件名

    Returns:
        CallToolResult: MCP 标准工具结果。
            - content: 面向模型的文本摘要
            - structuredContent: 结构化结果数据
            - isError: 是否为错误结果

    Raises:
        Exception: 当融合过程中发生错误时抛出异常，异常信息会被捕获并格式化返回给用户
    """
    image = arguments.get("image")

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> Dict[str, Any]:
        result = await client.multi_image_fusion(
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
        tool_name="multi_image_fusion",
        completion_title="多图融合任务完成",
        failure_prefix="多图融合",
        guidance="请检查图片列表与尺寸参数，确认 API Key 和网络可用后重试。",
        start_log_message=(
            "多图融合开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
        ),
        start_log_values_builder=lambda ctx: (
            len(ctx.prompt or ""),
            ctx.size,
            ctx.stream,
            ctx.request_count,
            ctx.parallelism,
        ),
        request_executor=_execute,
        ctx=ctx,
    )
