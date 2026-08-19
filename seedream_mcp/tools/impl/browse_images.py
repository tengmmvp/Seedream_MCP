"""图片浏览工具的 impl 处理器。

薄壳入口：不经 ``execute_generation_handler`` 生成流水线，扫描编排与分页逻辑委托
``tools.core.browse``；字段规则与校验由 ``BrowseImagesInput`` 单一定义。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.browse import build_browse_fallback_result, execute_browse_request
from ..core.common import PROGRESS_COMPLETE, safe_report_progress
from ..core.schemas import BrowseImagesInput
from ...utils.core.errors import format_error_for_user
from ...utils.core.logs import get_logger

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context
    from mcp.types import CallToolResult

logger = get_logger()


async def handle_browse_images(
    params: BrowseImagesInput,
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """处理图片浏览请求，扫描工作区内指定目录的图片并分页返回。

    仅允许访问 MCP Roots 授权的工作区目录；未预期异常降级为结构化错误返回，不向
    调用方抛出。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        ctx: MCP 上下文，用于进度上报，可为 None。

    Returns:
        浏览工具结果，失败时 isError 为 True。
    """
    # 已解析目录列表在外层创建、core 流水线填充：异常兜底分支经同一引用回显已解析目录。
    resolved_directories: list[Path] = []
    try:
        return await execute_browse_request(params, ctx, resolved_directories=resolved_directories)
    except Exception as exc:
        logger.error("浏览图片处理失败", exc_info=True)
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        return await build_browse_fallback_result(
            params, resolved_directories, format_error_for_user(exc)
        )
