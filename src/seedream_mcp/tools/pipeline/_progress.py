"""进度里程碑常量与上报原语。

进度值与工具处理阶段一一对应；上报失败静默降级不影响主流程。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ...utils.core.logs import get_logger

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

logger = get_logger()


PROGRESS_RECEIVED = 0.0
PROGRESS_VALIDATED = 10.0
PROGRESS_GENERATION_START = 20.0
PROGRESS_GENERATION_DONE = 70.0
PROGRESS_AUTOSAVE_START = 75.0
PROGRESS_AUTOSAVE_DONE = 95.0
PROGRESS_COMPLETE = 100.0
# 浏览工具扫描进度起点。
PROGRESS_SCAN_START = 20.0


async def safe_report_progress(
    ctx: Context[Any, Any] | None,
    *,
    progress: float,
    total: float = 100.0,
    message: str,
) -> None:
    """在支持进度能力的 MCP 会话中上报进度；上报失败不影响主流程。"""
    if ctx is None:
        return

    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except Exception as exc:
        logger.debug("进度上报失败，已忽略: {}", exc)


async def _yield_for_cancellation() -> None:
    """协作式让出执行权，确保取消信号能尽快生效。"""
    await asyncio.sleep(0)
