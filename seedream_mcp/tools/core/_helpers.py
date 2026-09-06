"""生成工具底层辅助函数。

用量累加、错误归一化、保存路径解析与进度上报，不依赖生成上下文与结果结构，
作为其余子模块的公共基础。
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...config import SeedreamConfig
from ...utils.core.errors import (
    SeedreamValidationError,
    format_error_for_user,
    resolve_error_profile,
    sanitize_error_text,
)
from ...utils.core.logs import get_logger
from ...utils.io.io_path import (
    get_workspace_root,
    is_within_resolved,
    normalize_path,
    resolve_cached_default_save_base_dir,
    resolve_cached_save_base_dir,
)

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
# 浏览工具扫描进度起点与跨度：多目录扫描按已扫目录占比在区间内插值上报。
PROGRESS_SCAN_START = 20.0
PROGRESS_SCAN_SPAN = 70.0


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


def resolve_default_base_dir(config: SeedreamConfig) -> Path:
    """解析自动保存的默认基础目录，供保存路径解析、预检与 Web 域共用。

    Raises:
        SeedreamValidationError: 未配置 auto_save_base_dir 且无法确定工作区根。
    """
    if config.auto_save_base_dir:
        # 显式配置的保存根经 io_path 的进程级缓存 resolve，仅 expanduser 后为绝对
        # 路径的配置串首次发生文件系统调用，相对路径随进程 CWD 变化须每次现算；
        # 配置写入路径统一使缓存失效。
        return resolve_cached_save_base_dir(config.auto_save_base_dir)
    # get_workspace_root 的 ValueError 转校验异常，归入 validation_error 档，用户可见
    # 文案指向工作区授权问题而非未知失败。
    try:
        workspace_root = get_workspace_root()
    except ValueError as exc:
        raise SeedreamValidationError(
            f"无法确定自动保存基础目录: {exc}",
            field="auto_save_base_dir",
            value=config.auto_save_base_dir,
        ) from exc
    # 默认目录的常量拼接路径同样经进程级缓存 resolve，键派生自工作区根；配置写入
    # 路径统一使缓存失效。
    return resolve_cached_default_save_base_dir(workspace_root)


def _validate_save_path_bounds(default_base_dir: Path, save_path: str) -> Path:
    """校验用户保存路径有效且落在默认目录之内，返回规范化后的用户路径。

    Raises:
        SeedreamValidationError: save_path 无效或越出默认保存目录。
    """
    try:
        user_path = normalize_path(save_path, str(default_base_dir))
    except ValueError as exc:
        raise SeedreamValidationError(f"保存路径无效: {exc}", field="save_path", value=save_path)

    # 两路径均已 resolve，直接比较即可，无需重复 resolve。
    if not is_within_resolved(user_path, default_base_dir):
        raise SeedreamValidationError(
            f"save_path 超出允许范围: {default_base_dir}",
            field="save_path",
            value=save_path,
        )

    return user_path


def _resolve_base_dir(config: SeedreamConfig, save_path: str | None) -> Path:
    """解析自动保存的基础目录，save_path 未指定时返回默认目录，指定时校验须落在
    默认目录之内。

    Raises:
        SeedreamValidationError: 无法确定工作区根，或 save_path 无效、越出默认保存目录。
    """
    default_base_dir = resolve_default_base_dir(config)
    if not save_path:
        return default_base_dir
    return _validate_save_path_bounds(default_base_dir, save_path)


def prevalidate_save_path(config: SeedreamConfig, save_path: str | None) -> None:
    """在生成请求分发前预检 save_path 的边界合法性。

    与 _resolve_base_dir 共用同一默认目录解析与越界判定，使非法 save_path 在计费
    请求前即以 validation_error 拒绝，而非留待自动保存阶段降级为软警告。未提供
    save_path 时不做检查。

    Raises:
        SeedreamValidationError: save_path 无效或越出默认保存目录。
    """
    if not save_path:
        return
    _validate_save_path_bounds(resolve_default_base_dir(config), save_path)


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
