"""生成工具底层辅助函数。

用量累加、错误归一化、自动保存路径解析，以及面向 MCP 客户端的进度上报与日志推送。
这些函数不依赖生成上下文与结果结构，作为其余子模块的公共基础。
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
from ...utils.io.io_path import is_within_resolved, get_workspace_root, normalize_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

logger = get_logger(__name__)


# 进度里程碑常量：common.py、parallel.py 与 impl/browse_images.py 共用，集中定义避免
# 跨模块隐式契约漂移与两套同名常量各自演化。
# 生成管道阶梯：接收 0 → 校验完成 10 → 生成开始 20 → 生成完成 70 → 自动保存开始 75 → 保存完成 95 → 结束 100。
PROGRESS_RECEIVED = 0.0
PROGRESS_VALIDATED = 10.0
PROGRESS_GENERATION_START = 20.0
PROGRESS_GENERATION_DONE = 70.0
PROGRESS_AUTOSAVE_START = 75.0
PROGRESS_AUTOSAVE_DONE = 95.0
PROGRESS_COMPLETE = 100.0
# 浏览工具阶梯：扫描开始 20，多目录扫描按已扫描目录占比在 70 的跨度内渐增至 90，
# 结束复用 PROGRESS_COMPLETE。数值与生成管道部分里程碑相同但语义独立，故单独命名。
PROGRESS_SCAN_START = 20.0
PROGRESS_SCAN_SPAN = 70.0


def _add_usage_value(usage: dict[str, Any], key: str, value: Any) -> None:
    """累加用量统计值。

    标量数值字段直接累加；嵌套 dict 字段对其标量子键递归累加合并，使并发聚合与单请求
    原样保留的用量结构一致。布尔与非数值标量跳过以避免污染汇总结果。
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
    """判定生成结果是否视为失败，纳入 HTTP 层 success 与显式 status==failed。"""
    return not bool(result.get("success")) or result.get("status") == "failed"


def _normalize_error_message(raw_error: Any) -> str | None:
    """将不同形态的错误对象提取为可读文本，经统一脱敏后返回。

    上游错误文本可能回显鉴权片段，出口处过 sanitize_error_text 使并行聚合消息与
    异常路径的防护一致。
    """
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
        return code.strip()
    return None


def _classify_generation_error_type(exc: Exception) -> str:
    """将异常映射为稳定的结构化错误码，避免向 structuredContent 暴露内部异常类名。

    错误码统一来自 errors 模块的归约档案，单发与并发路径共用此函数使两条路径的错误码
    契约一致，且不泄露实现细节。
    """
    return resolve_error_profile(exc).error_code


# 凭据与连接类错误的共用排查建议。
_NETWORK_CREDENTIAL_GUIDANCE = "请确认 API Key 和网络可用后重试。"

# 有意走默认排查建议的错误码：generation_failed 是基类与未识别异常的兜底档案码，
# 无比通用建议更具体的指引，不进入错误码查表；错误码全集守护测试据此放行。
_FAILURE_GUIDANCE_INTENTIONAL_DEFAULT_CODES = frozenset({"generation_failed"})

# 失败排查建议按错误码查表：参数与请求形态类错误引导调整参数取值，凭据、服务与连接
# 类错误引导检查 API Key 与网络，避免校验失败时误导调用方排查无关项。错误码取值与
# errors 模块的归约档案一致，新增错误码时同步维护本表；未列举错误码回退通用建议。
# guidance 拼接仅在归约档案未携带 user_hint 时发生（见 common.py 失败分支），本表
# 是该场景下的兜底建议来源，档案有 user_hint 的错误码以档案建议为准。
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

# HTTP 状态码级排查建议：SeedreamAPIError 的多个业务失败状态（400/404 等）归约到
# 同一个 api_error 错误码，仅按错误码查表会把参数错误与端点错误统一导向凭据与网络
# 排查，与档案 user_hint 矛盾。guidance 拼接仅在归约档案未携带 user_hint 时发生，
# 而 _HTTP_STATUS_PROFILES 对以下各状态均已配置 user_hint，故本表当前不会触达，
# 仅作为档案移除或新增状态缺 hint 时的兜底；建议文案与各状态档案 user_hint 语义
# 一致，未列举状态回退错误码查表。
_FAILURE_GUIDANCE_BY_STATUS: dict[int, str] = {
    400: "请核对请求参数。",
    401: _NETWORK_CREDENTIAL_GUIDANCE,
    402: "请检查账户余额与配额。",
    404: "请确认 API 端点配置。",
    429: "请稍后重试。",
}


def _resolve_failure_guidance(exc: Exception) -> str:
    """按 HTTP 状态码或错误归约档案的错误码选择失败排查建议，供异常降级文案拼接。

    SeedreamAPIError 优先按 status_code 查状态级建议表，无状态码或状态未列举时按
    归约档案错误码兜底查表，两表均未命中回退通用建议。
    """
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


def _resolve_base_dir(config: SeedreamConfig, save_path: str | None) -> Path:
    """解析自动保存的基础目录路径，校验用户路径须落在默认目录之内。

    优先使用用户指定路径，若未指定则使用配置中的默认路径。

    Args:
        config: Seedream 配置实例，包含自动保存相关参数。
        save_path: 用户指定的保存路径，可选。

    Returns:
        解析后的安全路径对象。

    Raises:
        SeedreamValidationError: 无法确定工作区根，或 save_path 无效、越出默认保存目录。
    """
    # 多根场景下取首个授权根作为自动保存默认落点。browse 与图像输入采用遍历全根的不同策略。
    if config.auto_save_base_dir:
        default_base_dir = Path(config.auto_save_base_dir).expanduser().resolve()
    else:
        # MCP Roots 为空列表时 get_workspace_root 抛 ValueError，原样上抛会落入未识别
        # 异常档案呈「未知错误」；转校验异常归入 validation_error 档，用户可见文案
        # 指向工作区授权问题而非未知失败。
        try:
            workspace_root = get_workspace_root()
        except ValueError as exc:
            raise SeedreamValidationError(
                f"无法确定自动保存基础目录: {exc}",
                field="auto_save_base_dir",
                value=config.auto_save_base_dir,
            ) from exc
        default_base_dir = (workspace_root / "images").resolve()

    if not save_path:
        return default_base_dir

    try:
        user_path = normalize_path(save_path, str(default_base_dir))
    except ValueError as exc:
        raise SeedreamValidationError(f"保存路径无效: {exc}", field="save_path", value=save_path)

    # user_path 由 normalize_path 解析、default_base_dir 在本函数上方解析，两者均已 resolve，
    # 直接 relative_to 比较即可，避免对已 resolve 的二者再次重复 resolve。
    if not is_within_resolved(user_path, default_base_dir):
        raise SeedreamValidationError(
            f"save_path 超出允许范围: {default_base_dir}",
            field="save_path",
            value=save_path,
        )

    return user_path


async def _safe_report_progress(
    ctx: Context[Any, Any, Any] | None,
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
    ctx: Context[Any, Any, Any] | None,
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
