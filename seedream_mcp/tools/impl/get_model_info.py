"""模型信息工具的 impl 处理器。

薄壳入口：能力快照的载荷组装委托 ``tools.core.model_info``，此处仅包装为
结构化工具结果。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.model_info import build_model_info_payload, build_model_info_text
from ..core.common import log_tiered_failure
from ..core.outputs import (
    GetModelInfoStructuredOutput,
    build_error_dict,
    build_structured_tool_result,
)
from ...config import SeedreamConfig
from ...utils.core.errors import format_error_for_user
from ...utils.core.logs import get_logger

if TYPE_CHECKING:
    from mcp.types import CallToolResult

logger = get_logger()


async def handle_get_model_info(config: SeedreamConfig) -> CallToolResult:
    """处理模型信息查询，返回当前配置模型的能力快照。

    未预期异常降级为结构化错误返回，不向调用方抛出。
    """
    try:
        payload = build_model_info_payload(config)
        structured = GetModelInfoStructuredOutput(
            tool="get_model_info", success=True, status="completed", **payload
        ).model_dump(exclude={"error"})
        return await build_structured_tool_result(
            build_model_info_text(payload), structured, is_error=False
        )
    except Exception as exc:
        log_tiered_failure(logger, exc, "模型信息查询失败")
        message = format_error_for_user(exc)
        structured = GetModelInfoStructuredOutput(
            tool="get_model_info",
            success=False,
            status="failed",
            error=build_error_dict("model_info_failed", message),
        ).model_dump()
        return await build_structured_tool_result(message, structured, is_error=True)
