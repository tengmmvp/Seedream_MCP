"""多图融合工具的 impl 处理器。

作为薄适配器：从入参取出多图列表字段，与 prompt 等封装为 ``_execute`` 回调，再委托
``execute_generation_handler`` 流水线完成校验、调用、保存与结果格式化。字段规则与校验
由 schemas.MultiImageFusionInput 单一定义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

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

logger = get_logger(__name__)


async def handle_multi_image_fusion(
    arguments: Dict[str, Any],
    config: SeedreamConfig,
    ctx: "Context[Any, Any, Any] | None" = None,
) -> CallToolResult:
    """处理多图融合请求，依据文本描述融合多张参考图特征生成新图像。

    流程由 ``execute_generation_handler`` 统一编排：参数经 schema 校验后构建执行上下文，
    调用客户端生成，可选自动保存，最终返回结构化工具结果。完整字段规则与默认值见
    ``MultiImageFusionInput``，本函数仅透传 arguments。

    Args:
        arguments: 工具原始参数字典，结构见 ``MultiImageFusionInput``。
        config: 当前生效的 SeedreamConfig。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 标准工具结果，含面向模型的文本摘要与 structuredContent，失败时不抛出异常而
        以 ``isError=True`` 返回。
    """
    image = arguments.get("image")

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> Dict[str, Any]:
        # log_function_call 装饰器将返回类型归一化为 Any，显式标注恢复 Dict 契约。
        result: Dict[str, Any] = await client.multi_image_fusion(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
            image=image,
            size=context.size,
            watermark=context.watermark,
            response_format=context.response_format,
            output_format=context.output_format,
            stream=context.stream,
            tools=context.tools,
        )
        return result

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
