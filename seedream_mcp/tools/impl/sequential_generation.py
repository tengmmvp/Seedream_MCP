"""组图输出工具的 impl 处理器。

作为薄适配器：从入参取出参考图与 max_images 字段，与 prompt 等封装为 ``_execute``
回调，再委托 ``execute_generation_handler`` 流水线完成校验、调用、保存与结果格式化。
字段规则与校验由 schemas.SequentialGenerationInput 单一定义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult

from ...config import SeedreamConfig
from ...utils.logging import get_logger

from ..core.common import (
    execute_generation_handler,
    GenerationExecutionContext,
)
from ._common import SEQUENTIAL_GENERATION, _sequential_start_log_values_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from ...client import SeedreamClient

logger = get_logger(__name__)


async def handle_sequential_generation(
    arguments: dict[str, Any],
    config: SeedreamConfig,
    ctx: Context[Any, Any, Any] | None = None,
) -> CallToolResult:
    """处理组图输出请求，基于参考图与文本生成一组内容关联的图片。

    流程由 ``execute_generation_handler`` 统一编排：参数经 schema 校验后构建执行上下文，
    调用客户端生成，可选自动保存，最终返回结构化工具结果。完整字段规则与默认值见
    ``SequentialGenerationInput``，本函数仅透传 arguments。

    Args:
        arguments: 工具原始参数字典，结构见 ``SequentialGenerationInput``。
        config: 当前生效的 SeedreamConfig。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 标准工具结果，含面向模型的文本摘要与 structuredContent，失败时不抛出异常而
        以 ``isError=True`` 返回。
    """
    image = arguments.get("image")
    max_images = arguments.get("max_images")

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> dict[str, Any]:
        result: dict[str, Any] = await client.sequential_generation(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
            image=image,
            size=context.size,
            watermark=context.watermark,
            max_images=max_images,
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
        **SEQUENTIAL_GENERATION.as_handler_kwargs(),
        start_log_values_builder=_sequential_start_log_values_factory(max_images),
        request_executor=_execute,
        ctx=ctx,
    )
