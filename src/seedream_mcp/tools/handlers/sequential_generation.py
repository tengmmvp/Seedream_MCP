"""组图输出工具处理器。

薄适配器：取出参考图与 max_images 字段，与 prompt 等封装为 ``_execute`` 回调，委托
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

    params.max_images 为调用方原始声明（缺省为 None），生效上限由执行上下文
    按参考图数量推导，client 侧重校验后随请求发出。

    Returns:
        含文本摘要与 structuredContent 的工具结果；失败不抛异常，isError 为 True。
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
