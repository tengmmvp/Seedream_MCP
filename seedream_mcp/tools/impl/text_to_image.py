"""文生图工具的 impl 处理器。

作为薄适配器：封装 prompt 与优化选项为 ``_execute`` 回调，再委托
``execute_generation_handler`` 流水线完成校验、调用、保存与结果格式化。字段规则与校验
由 schemas.TextToImageInput 单一定义。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from mcp.types import CallToolResult

from ...config import SeedreamConfig
from ...utils.core.logs import get_logger

from ..core.common import (
    execute_generation_handler,
    GenerationExecutionContext,
)
from ..core.schemas import TextToImageInput
from ._common import TEXT_TO_IMAGE, _default_start_log_values

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

    from ...client import SeedreamClient

logger = get_logger(__name__)


async def handle_text_to_image(
    params: TextToImageInput,
    config: SeedreamConfig,
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """处理文生图请求，依据文本提示词生成图片。

    流程由 ``execute_generation_handler`` 统一编排：参数经 schema 校验后构建执行上下文，
    调用客户端生成，可选自动保存，最终返回结构化工具结果。完整字段规则与默认值见
    ``TextToImageInput``，本函数仅透传入参模型。

    Args:
        params: 经 pydantic 校验的文生图入参模型。
        config: 当前生效的 SeedreamConfig。
        ctx: MCP 上下文，用于进度上报，无会话时可为 None。

    Returns:
        MCP 标准工具结果，含面向模型的文本摘要与 structuredContent，失败时不抛出异常而
        以 ``is_error=True`` 返回。
    """

    async def _execute(
        client: "SeedreamClient", context: GenerationExecutionContext
    ) -> dict[str, Any]:
        return await client.text_to_image(
            prompt=context.prompt,
            optimize_prompt_options=context.optimize_prompt_options,
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
        **TEXT_TO_IMAGE.as_handler_kwargs(),
        start_log_values_builder=_default_start_log_values,
        request_executor=_execute,
        ctx=ctx,
    )
