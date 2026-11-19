"""图层拆分工具处理器。

薄适配器：图层拆分是图文生图 API 的固定场景（恒 ``layer_decomposition=true``），
取出单张参考图字段，与 prompt 等封装为 ``_execute`` 回调，委托
``execute_generation_handler`` 统一编排；字段规则与校验由 ``LayerDecomposeInput``
单一定义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult

from ...config import SeedreamConfig
from ...utils.core.logs import get_logger
from ..pipeline import execute_generation_handler
from ..schemas import LayerDecomposeInput
from ._metadata import LAYER_DECOMPOSE

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient
    from ..pipeline import GenerationExecutionContext

logger = get_logger()


async def handle_layer_decompose(
    params: LayerDecomposeInput,
    config: SeedreamConfig,
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """处理图层拆分请求，将单张图片拆解为底图与多个透明图层。

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
            # 图层拆分开关经 context 校验（由 LayerDecomposeInput 类型判定），
            # 不另持硬编码真值。
            layer_decomposition=context.layer_decomposition,
            layer_decomposition_options=context.layer_decomposition_options,
            size=context.size,
            watermark=context.watermark,
            tools=context.tools,
            output_format=context.output_format,
            response_format=context.response_format,
            stream=context.stream,
        )

    return await execute_generation_handler(
        params=params,
        config=config,
        metadata=LAYER_DECOMPOSE,
        module_logger=logger,
        request_executor=_execute,
        ctx=ctx,
    )
