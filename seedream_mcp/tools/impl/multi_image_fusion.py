"""多图融合工具的 impl 处理器。

薄适配器：取出多图列表字段，与 prompt 等封装为 ``_execute`` 回调，委托
``execute_generation_handler`` 统一编排；字段规则与校验由 ``MultiImageFusionInput``
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
from ..core.schemas import MultiImageFusionInput
from ._common import MULTI_IMAGE_FUSION, _default_start_log_values

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient

logger = get_logger(__name__)


async def handle_multi_image_fusion(
    params: MultiImageFusionInput,
    config: SeedreamConfig,
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """处理多图融合请求，依据文本描述融合多张参考图特征生成新图像。

    入参经 schema 校验后由 ``execute_generation_handler`` 统一编排：构建执行上下文、
    调用客户端生成、可选自动保存并格式化结果；字段规则与默认值见
    ``MultiImageFusionInput``。

    Returns:
        含文本摘要与 structuredContent 的工具结果；失败不抛异常，以 ``is_error=True``
        返回。
    """
    image = params.image

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> dict[str, Any]:
        return await client.multi_image_fusion(
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

    return await execute_generation_handler(
        params=params,
        config=config,
        module_logger=logger,
        **MULTI_IMAGE_FUSION.as_handler_kwargs(),
        start_log_values_builder=_default_start_log_values,
        request_executor=_execute,
        ctx=ctx,
    )
