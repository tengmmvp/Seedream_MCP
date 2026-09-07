"""生成工具底层辅助函数。

用量累加、错误归一化、保存路径解析与进度上报，不依赖生成上下文与结果结构，
作为其余子模块的公共基础。
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...utils.core.errors import (
    SeedreamValidationError,
    format_error_for_user,
    resolve_error_profile,
    sanitize_error_text,
)
from ...utils.core.logs import get_logger
from ...utils.io.io_path import normalize_path, resolve_save_root

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

logger = get_logger()


# 进度里程碑常量
PROGRESS_RECEIVED = 0.0
PROGRESS_VALIDATED = 10.0
PROGRESS_GENERATION_START = 20.0
PROGRESS_GENERATION_DONE = 70.0
PROGRESS_AUTOSAVE_START = 75.0
PROGRESS_AUTOSAVE_DONE = 95.0
PROGRESS_COMPLETE = 100.0
# 浏览工具扫描进度起点。
PROGRESS_SCAN_START = 20.0


def _add_usage_value(usage: dict[str, Any], key: str, value: Any) -> None:
    """累加用量统计值。

    标量数值直接累加，嵌套 dict 递归合并子键；布尔与非数值标量跳过，避免污染汇总。
    """
    if isinstance(value, dict):
        current = usage.get(key)
        if isinstance(current, dict):
            for sub_key, sub_value in value.items():
                _add_usage_value(current, sub_key, sub_value)
        else:
            usage[key] = copy.deepcopy(value)
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    current = usage.get(key, 0)
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        current = 0
    usage[key] = current + value


def _is_generation_failed(result: dict[str, Any]) -> bool:
    """判定生成结果是否失败，综合 HTTP 层 success 与显式 status==failed 两信号。"""
    return not bool(result.get("success")) or result.get("status") == "failed"


def _normalize_error_message(raw_error: Any) -> str | None:
    """将不同形态的错误对象提取为可读文本，经 sanitize_error_text 脱敏后返回。"""
    if isinstance(raw_error, str):
        message = raw_error.strip()
        return sanitize_error_text(message) if message else None

    if not isinstance(raw_error, dict):
        return None

    for key in ("message", "msg", "detail", "error"):
        value = raw_error.get(key)
        if isinstance(value, str) and value.strip():
            return sanitize_error_text(value.strip())

    code = raw_error.get("code")
    if isinstance(code, str) and code.strip():
        return sanitize_error_text(code.strip())
    return None


def _classify_generation_error_type(exc: Exception) -> str:
    """将异常映射为归约档案的稳定错误码，不向 structuredContent 暴露异常类名。"""
    return resolve_error_profile(exc).error_code


# 凭据与连接类错误的共用排查建议。
_NETWORK_CREDENTIAL_GUIDANCE = "请确认 API Key 和网络可用后重试。"

# generation_failed 为兜底档案码，无更具体指引，有意不进入下方查表；守护测试据此放行。
_FAILURE_GUIDANCE_INTENTIONAL_DEFAULT_CODES = frozenset({"generation_failed"})

_FAILURE_GUIDANCE_BY_ERROR_CODE: dict[str, str] = {
    "validation_error": "请根据错误信息调整对应参数取值。",
    "payload_too_large": "请根据错误信息调整对应参数取值。",
    "rate_limited": "请稍后重试。",
    "payment_required": "请检查账户余额与配额。",
    "config_error": "请检查服务端配置后重试。",
    "auth_error": _NETWORK_CREDENTIAL_GUIDANCE,
    "api_error": _NETWORK_CREDENTIAL_GUIDANCE,
    "network_error": _NETWORK_CREDENTIAL_GUIDANCE,
    "timeout_error": _NETWORK_CREDENTIAL_GUIDANCE,
}
_DEFAULT_FAILURE_GUIDANCE = "请根据错误信息排查后重试。"

# HTTP 状态码级排查建议：多个业务失败状态归约到同一 api_error 错误码，按状态码
# 区分建议；未列举状态回退错误码查表。
_FAILURE_GUIDANCE_BY_STATUS: dict[int, str] = {
    400: "请核对请求参数。",
    401: _NETWORK_CREDENTIAL_GUIDANCE,
    402: "请检查账户余额与配额。",
    404: "请确认 API 端点配置。",
    429: "请稍后重试。",
}


def _resolve_failure_guidance(exc: Exception) -> str:
    """选择失败排查建议：优先按 status_code 查状态级表，其次按错误码查表，均未命中
    回退通用建议。"""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        status_guidance = _FAILURE_GUIDANCE_BY_STATUS.get(status_code)
        if status_guidance is not None:
            return status_guidance
    error_code = resolve_error_profile(exc).error_code
    return _FAILURE_GUIDANCE_BY_ERROR_CODE.get(error_code, _DEFAULT_FAILURE_GUIDANCE)


def _extract_parallel_request_error(
    result: dict[str, Any] | None, fallback_exc: Exception | None
) -> str:
    """提取单个并行请求的失败原因，优先使用结果内错误信息，回退到异常格式化文案。"""
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

    if fallback_exc is not None:
        return format_error_for_user(fallback_exc)
    return "请求失败"


def _resolve_base_dir(save_path: str | None) -> Path:
    """解析本次调用的写入目录：save_path 声明优先，否则取部署级存储区。

    save_path 为调用级存储声明，位置不受限；相对路径以部署级存储区为基准，
    绝对形态不依赖基准、存储声明不可解析时不受阻；UNC、空字节、冒号分量等
    形态经 normalize_path 在 resolve 前拒绝。

    Raises:
        SeedreamValidationError: save_path 路径无效。
        SeedreamConfigError: 部署级存储声明不可解析，经 resolve_save_root 穿透。
    """
    if not save_path:
        return resolve_save_root()
    base = None if Path(save_path).is_absolute() else str(resolve_save_root())
    try:
        return normalize_path(save_path, base)
    except ValueError as exc:
        raise SeedreamValidationError(
            f"保存路径无效: {exc}", field="save_path", value=save_path
        ) from exc


def prevalidate_save_path(save_path: str | None) -> Path | None:
    """在生成请求分发前预检 save_path 的路径有效性并解析写入目录。

    使非法 save_path 在计费请求前即以 validation_error 拒绝，而非留待自动保存
    阶段降级为软警告；解析结果供调用内读写资格置位复用，不再二次解析。

    Returns:
        解析后的本次调用写入目录；未提供 save_path 时为 None。

    Raises:
        SeedreamValidationError: save_path 路径无效或存储区配置无法解析。
    """
    if not save_path:
        return None
    return _resolve_base_dir(save_path)


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
