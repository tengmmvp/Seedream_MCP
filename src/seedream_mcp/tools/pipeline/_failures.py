"""生成失败归一化与分级记录。

错误消息提取脱敏、错误码分类、通用失败指引与分级日志；并行请求的失败原因
提取复用同一归一化。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from ...utils.core.errors import (
    SeedreamMCPError,
    format_error_for_user,
    normalized_upstream_error_code,
    resolve_error_profile,
)
from ...utils.core.sanitizers import sanitize_error_text
from ...utils.io.stream import data_items

if TYPE_CHECKING:
    from loguru import Logger


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

    code = normalized_upstream_error_code(raw_error.get("code"))
    if code:
        return sanitize_error_text(code)
    return None


def _classify_generation_error_type(exc: Exception) -> str:
    """将异常映射为归约档案的稳定错误码，不向 structuredContent 暴露异常类名。"""
    return resolve_error_profile(exc).error_code


# 失败排查建议统一回退通用文案：定向指引归位错误档案 user_hint（config_error 等），
# 由 format_error_for_user 拼接承担；无 hint 档案经 _execute 错误分支以本建议补充。
_DEFAULT_FAILURE_GUIDANCE = "请根据错误信息排查后重试。"


def log_tiered_failure(
    logger: Logger,
    exc: Exception,
    failure_template: str,
    *template_args: Any,
    user_facing: str | None = None,
    unexpected_template: str | None = None,
    unexpected_level: Literal["error", "warning"] = "error",
) -> None:
    """按两级策略记录失败：SeedreamMCPError 记 warning 不带堆栈，非预期异常带堆栈。

    Args:
        logger: 记录日志的 logger。
        exc: 待分级的异常；业务异常层缺省 user_facing 时经 format_error_for_user 渲染。
        failure_template: 业务异常层的消息模板，格式化错误文案以 ": {}" 追加在尾部。
        template_args: 消息模板的格式化参数。
        user_facing: 调用方对同一异常单次计算的 format_error_for_user 产物，
            业务异常层以 ": {}" 追加在消息尾部；缺省时业务分支对该异常自行渲染。
        unexpected_template: 非预期异常层的消息模板，缺省复用 failure_template。
        unexpected_level: 非预期异常的记录级别。
    """
    if isinstance(exc, SeedreamMCPError):
        rendered = user_facing if user_facing is not None else format_error_for_user(exc)
        logger.warning(failure_template + ": {}", *template_args, rendered)
        return
    template = failure_template if unexpected_template is None else unexpected_template
    if unexpected_level == "warning":
        logger.opt(exception=True).warning(template, *template_args)
    else:
        logger.opt(exception=True).error(template, *template_args)


def _extract_parallel_request_error(
    result: dict[str, Any] | None, fallback_exc: Exception | None
) -> str:
    """提取单个并行请求的失败原因，优先使用结果内错误信息，回退到异常格式化文案。"""
    if isinstance(result, dict):
        direct_error = _normalize_error_message(result.get("error"))
        if direct_error:
            return direct_error

        for item in data_items(result.get("data")):
            if not isinstance(item, dict):
                continue
            item_error = _normalize_error_message(item.get("error"))
            if item_error:
                return item_error

    if fallback_exc is not None:
        return format_error_for_user(fallback_exc)
    return "请求失败"
