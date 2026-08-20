"""图文生图工具的 impl 处理器。

薄适配器：取出单张参考图字段，与 prompt 等封装为 ``_execute`` 回调，委托
``execute_generation_handler`` 统一编排；字段规则与校验由 ``ImageToImageInput``
单一定义。
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
from ..core.schemas import ImageToImageInput
from ._common import IMAGE_TO_IMAGE

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient

logger = get_logger()


async def handle_image_to_image(
    params: ImageToImageInput,
    config: SeedreamConfig,
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """处理图文生图请求，基于参考图与文本指令生成新图像。

    Returns:
        含文本摘要与 structuredContent 的工具结果；失败不抛异常，以 ``is_error=True``
        返回。
    """
    image = params.image

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> dict[str, Any]:
        return await client.image_to_image(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
            image=image,
            layer_decomposition=context.layer_decomposition or None,
            background=context.background,
            size=context.size,
            watermark=context.watermark,
            response_format=context.response_format,
            output_format=context.output_format,
            stream=context.stream,
            tools=context.tools,
        )

    return await execute_generation_handler(
        params=params,
        config=config,
        metadata=IMAGE_TO_IMAGE,
        module_logger=logger,
        request_executor=_execute,
        ctx=ctx,
    )
