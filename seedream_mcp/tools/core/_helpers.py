"""生成工具底层辅助函数。

用量累加、错误归一化、自动保存路径解析，以及面向 MCP 客户端的进度上报与日志推送。
这些函数不依赖生成上下文与结果结构，作为其余子模块的公共基础。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from ...config import SeedreamConfig
from ...utils.errors import SeedreamValidationError
from ...utils.logging import get_logger
from ...utils.path_utils import get_workspace_root, is_path_within_base, normalize_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

logger = get_logger(__name__)


def _add_usage_value(usage: Dict[str, Any], key: str, value: Any) -> None:
    """累加用量统计值，跳过布尔与非数值类型以避免污染汇总结果。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    current = usage.get(key, 0)
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        current = 0
    usage[key] = current + value


def _normalize_error_message(raw_error: Any) -> Optional[str]:
    """将不同形态的错误对象提取为可读文本。"""
    if isinstance(raw_error, str):
        message = raw_error.strip()
        return message or None

    if not isinstance(raw_error, dict):
        return None

    for key in ("message", "msg", "detail", "error"):
        value = raw_error.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    code = raw_error.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip()
    return None


def _extract_parallel_request_error(
    result: Optional[Dict[str, Any]], fallback_error: Optional[str]
) -> str:
    """提取单个并行请求的失败原因，优先使用结果内错误信息。"""
    if isinstance(result, dict):
        direct_error = _normalize_error_message(result.get("error"))
        if direct_error:
            return direct_error

        data = result.get("data")
        if isinstance(data, dict):
            nested_error = _normalize_error_message(data.get("error"))
            if nested_error:
                return nested_error
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                item_error = _normalize_error_message(item.get("error"))
                if item_error:
                    return item_error

    fallback = _normalize_error_message(fallback_error)
    return fallback or "请求失败"


def _resolve_base_dir(config: SeedreamConfig, save_path: Optional[str]) -> Path:
    """解析自动保存的基础目录路径，校验用户路径须落在默认目录之内。

    优先使用用户指定路径，若未指定则使用配置中的默认路径。

    Args:
        config: Seedream 配置实例，包含自动保存相关参数。
        save_path: 用户指定的保存路径，可选。

    Returns:
        解析后的安全路径对象。
    """
    # 多根场景下取首个授权根作为自动保存默认落点。browse 与图像输入采用遍历全根的不同策略。
    if config.auto_save_base_dir:
        default_base_dir = Path(config.auto_save_base_dir).expanduser().resolve()
    else:
        workspace_root = get_workspace_root()
        default_base_dir = (workspace_root / "images").resolve()

    if not save_path:
        return default_base_dir

    try:
        user_path = normalize_path(save_path, str(default_base_dir))
    except ValueError as exc:
        raise SeedreamValidationError(f"保存路径无效: {exc}", field="save_path", value=save_path)

    if not is_path_within_base(user_path, default_base_dir):
        raise SeedreamValidationError(
            f"save_path 超出允许范围: {default_base_dir}",
            field="save_path",
            value=save_path,
        )

    return user_path


async def _safe_report_progress(
    ctx: Optional["Context[Any, Any, Any]"],
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


_VALID_LOG_LEVELS = ("debug", "info", "warning", "error")


async def _safe_ctx_log(
    ctx: Optional["Context[Any, Any, Any]"],
    level: str,
    message: str,
) -> None:
    """向 MCP 客户端推送日志通知，级别限 debug/info/warning/error。

    客户端未声明 logging 能力或推送失败时静默跳过，不影响主流程。本函数面向客户端实时
    可见的通知，与 loguru 文件日志互补，后者用于离线排查。
    """
    if ctx is None or level not in _VALID_LOG_LEVELS:
        return

    try:
        if level == "debug":
            await ctx.debug(message)
        elif level == "info":
            await ctx.info(message)
        elif level == "warning":
            await ctx.warning(message)
        else:
            await ctx.error(message)
    except Exception as exc:
        logger.debug("MCP 日志推送失败，已忽略: {}", exc)


async def _yield_for_cancellation() -> None:
    """协作式让出执行权，确保取消信号能尽快生效。"""
    await asyncio.sleep(0)
