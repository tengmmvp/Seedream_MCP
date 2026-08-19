"""组图输出工具的 impl 处理器。

薄适配器：取出参考图与 max_images 字段，与 prompt 等封装为 ``_execute`` 回调，委托
``execute_generation_handler`` 统一编排；字段规则与校验由
``SequentialGenerationInput`` 单一定义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult

from ...config import SeedreamConfig
from ...utils.core.logs import get_logger

from ..core.common import (
    execute_generation_handler,
    GenerationExecutionContext,
)
from ..core.schemas import SequentialGenerationInput
from ._common import SEQUENTIAL_GENERATION

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient

logger = get_logger()


async def handle_sequential_generation(
    params: SequentialGenerationInput,
    config: SeedreamConfig,
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """处理组图输出请求，基于参考图与文本生成一组内容关联的图片。

    入参经 schema 校验后由 ``execute_generation_handler`` 统一编排：构建执行上下文、
    调用客户端生成、可选自动保存并格式化结果；字段规则与默认值见
    ``SequentialGenerationInput``。max_images 未显式提供时由 schema 按参考图数量
    自动推导。

    Returns:
        含文本摘要与 structuredContent 的工具结果；失败不抛异常，以 ``is_error=True``
        返回。
    """
    image = params.image

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> dict[str, Any]:
        return await client.sequential_generation(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
            image=image,
            size=context.size,
            watermark=context.watermark,
            max_images=context.max_images,
            response_format=context.response_format,
            output_format=context.output_format,
            stream=context.stream,
            tools=context.tools,
        )

    return await execute_generation_handler(
        params=params,
        config=config,
        metadata=SEQUENTIAL_GENERATION,
        module_logger=logger,
        request_executor=_execute,
        ctx=ctx,
    )
