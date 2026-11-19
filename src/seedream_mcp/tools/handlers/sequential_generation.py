"""组图输出工具处理器。

薄适配器：取出参考图字段，与 prompt 等封装为 ``_execute`` 回调，委托
``execute_generation_handler`` 统一编排；字段规则与校验由
``SequentialGenerationInput`` 单一定义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult

from ...config import SeedreamConfig
from ...utils.core.logs import get_logger
from ..pipeline import execute_generation_handler
from ..schemas import SequentialGenerationInput
from ._metadata import SEQUENTIAL_GENERATION

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient
    from ..pipeline import GenerationExecutionContext

logger = get_logger()


async def handle_sequential_generation(
    params: SequentialGenerationInput,
    config: SeedreamConfig,
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """处理组图输出请求，基于参考图与文本生成一组内容关联的图片。

    生效 max_images 显式声明优先、缺省按参考图数量推导（由输入模型现算），以官方
    sequential_image_generation_options 形态传 client，client 侧重校验后随请求发出。

    Returns:
        含文本摘要与 structuredContent 的工具结果；失败不抛异常，以 ``is_error=True`` 返回。
    """
    image = params.image

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> dict[str, Any]:
        return await client.sequential_generation(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
            image=image,
            sequential_image_generation_options=context.sequential_image_generation_options,
            size=context.size,
            background=context.background,
            watermark=context.watermark,
            tools=context.tools,
            output_format=context.output_format,
            response_format=context.response_format,
            stream=context.stream,
        )

    return await execute_generation_handler(
        params=params,
        config=config,
        metadata=SEQUENTIAL_GENERATION,
        module_logger=logger,
        request_executor=_execute,
        ctx=ctx,
    )
