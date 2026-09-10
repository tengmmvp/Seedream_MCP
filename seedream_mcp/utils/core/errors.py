"""Seedream MCP 错误处理模块。

定义以 SeedreamMCPError 为根的自定义异常层级，按场景派生配置、API、校验、超时、
网络等子类，以及 HTTP 错误响应的归约与用户可见信息格式化，供上层按异常类型分支
处理与重试决策。
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from typing import Any

from .sanitizers import (
    _sanitize_message_for_output,
    _truncate_value_for_output,
    sanitize_error_text,
    truncate_upstream_message_fragment,
)


class SeedreamMCPError(Exception):
    """所有 Seedream MCP 自定义异常的基类，供按类型捕获与分支处理。

    Attributes:
        message: 人类可读的错误描述文本。
        error_code: 结构化错误码。
        details: 附加上下文键值对。
    """

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class SeedreamConfigError(SeedreamMCPError):
    """配置加载或校验失败。"""

    pass


class SeedreamAPIError(SeedreamMCPError):
    """API 调用失败，携带状态码与建议退避秒数供上层判定可重试性。

    Attributes:
        status_code: HTTP 状态码。
        response_data: 上游响应体数据。
        retry_after: 服务器建议的重试等待秒数。
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_data: dict[str, Any] | None = None,
        error_code: str | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message, error_code=error_code)
        self.status_code = status_code
        self.response_data = response_data or {}
        self.retry_after = retry_after


class SeedreamValidationError(SeedreamMCPError):
    """请求参数校验失败。

    Attributes:
        field: 出错的参数字段名。
        value: 出错的参数值，超长在构造期截断为前缀，巨型 data URI 等输入不整体挂载；
            截断前缀未经脱敏，输出到用户可见层前仍须经 sanitize_error_text。
    """

    def __init__(self, message: str, field: str | None = None, value: Any | None = None):
        super().__init__(message)
        self.field = field
        self.value = _truncate_value_for_output(value)


class SeedreamTimeoutError(SeedreamMCPError):
    """请求超时。"""

    pass


class SeedreamNetworkError(SeedreamMCPError):
    """网络连接失败。"""

    pass


# Retry-After 下限：即便服务器返回 0 或极小值也至少等待此值，避免紧密重试风暴。
_MIN_RETRY_AFTER_SECONDS = 1.0
# Retry-After 上限：即便服务器返回更大值，单次退避也不超过此值，避免被诱导长时间睡眠。
_MAX_RETRY_AFTER_SECONDS = 300.0


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """解析 HTTP Retry-After 头为等待秒数，支持 delta-seconds 与 HTTP-date 格式。

    Args:
        headers: HTTP 响应头映射，同时读取 retry-after 与 Retry-After 两种键名。

    Returns:
        收敛到 [_MIN_RETRY_AFTER_SECONDS, _MAX_RETRY_AFTER_SECONDS] 区间的等待秒数，
        0 与极小值按下限兜底；头部缺失、无法解析、负值或目标时刻已过时为 None。
    """
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    try:
        seconds = float(raw)
        if seconds >= 0:
            return max(_MIN_RETRY_AFTER_SECONDS, min(seconds, _MAX_RETRY_AFTER_SECONDS))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(raw)
        if target is not None:
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            delta = (target - datetime.now(timezone.utc)).total_seconds()
            if delta > 0:
                return max(_MIN_RETRY_AFTER_SECONDS, min(delta, _MAX_RETRY_AFTER_SECONDS))
    except (TypeError, ValueError, OverflowError):
        pass
    return None


# HTTP 错误响应与异常类型的统一归约档案：状态码或异常类到展示标题、用户建议、
# 结构化错误码的单点映射，handle_api_error、format_error_for_user 与
# _classify_generation_error_type 共用，新增状态码或调整文案只需改这一处。
@dataclass(frozen=True)
class _ErrorProfile:
    """单条错误归约档案。

    Attributes:
        display_title: 面向用户的展示标题。
        user_hint: 面向用户的可操作建议，可为空字符串。
        error_code: 结构化错误码。
        base_message: handle_api_error 拼装 SeedreamAPIError.message 的初始文案，
            仅 HTTP 状态档案使用，异常类型档案留空。
    """

    display_title: str
    user_hint: str
    error_code: str
    base_message: str = ""


# 精确状态码档案。
_HTTP_STATUS_PROFILES: dict[int, _ErrorProfile] = {
    400: _ErrorProfile(
        "API调用失败",
        "请核对请求参数。",
        "api_error",
        base_message="请求参数错误",
    ),
    401: _ErrorProfile(
        "认证失败",
        "请检查您的API密钥是否正确设置。",
        "auth_error",
        base_message="API密钥无效或已过期",
    ),
    402: _ErrorProfile(
        "余额不足",
        "请检查账户余额与配额。",
        "payment_required",
        base_message="账户余额不足或配额已耗尽",
    ),
    403: _ErrorProfile(
        "API调用失败",
        "请确认 API Key 已开通目标模型的调用权限。",
        "api_error",
        base_message="访问被拒绝，请检查API权限",
    ),
    404: _ErrorProfile(
        "API调用失败",
        "请确认 API 端点配置。",
        "api_error",
        base_message="API端点不存在",
    ),
    413: _ErrorProfile(
        "请求体过大",
        "请减小参考图尺寸或改用 URL 传入。",
        "payload_too_large",
        base_message="请求体过大",
    ),
    422: _ErrorProfile(
        "请求参数语义错误",
        "请检查请求参数语义。",
        "validation_error",
        base_message="请求参数语义错误",
    ),
    429: _ErrorProfile(
        "请求频率超限",
        "请稍后重试。",
        "rate_limited",
        base_message="上游返回限流",
    ),
}

# 5xx 与未列举状态码的兜底档案。
_HTTP_5XX_PROFILE = _ErrorProfile(
    "API调用失败",
    "服务端暂时不可用，请稍后重试。",
    "api_error",
    base_message="服务器内部错误",
)
_HTTP_DEFAULT_PROFILE = _ErrorProfile("API调用失败", "", "api_error", base_message="API调用失败")


def _lookup_http_error_profile(status_code: int) -> _ErrorProfile:
    """按 HTTP 状态码查归约档案，5xx 与未列举码回退到对应兜底档案。"""
    profile = _HTTP_STATUS_PROFILES.get(status_code)
    if profile is not None:
        return profile
    if 500 <= status_code < 600:
        return _HTTP_5XX_PROFILE
    return _HTTP_DEFAULT_PROFILE


def handle_api_error(
    response_status: int,
    response_data: dict[str, Any],
    retry_after: float | None = None,
) -> SeedreamAPIError:
    """将 HTTP 错误响应归约为 SeedreamAPIError。

    基础文案取自 _HTTP_STATUS_PROFILES，再尝试拼入响应体携带的上游 error.code 与
    message，message 片段经 8KB 截断；status_code 与 retry_after 原样保留在异常上，
    供上层判定可重试性与退避时长。

    Args:
        response_status: HTTP 状态码。
        response_data: 上游错误响应体，error 与 message 字段内容拼入异常文案。
        retry_after: 服务器建议的重试等待秒数。

    Returns:
        装配完成的 SeedreamAPIError，供调用方直接 raise。
    """
    error_message = _lookup_http_error_profile(response_status).base_message

    error_code: str | None = None
    if isinstance(response_data, dict):
        # error 键可用形态未拼出 message 时回退查顶层 message，畸形 error 值
        # （非 dict 非 str）不使顶层描述被跳过。
        detail_message_extracted = False
        if "error" in response_data:
            error_detail = response_data["error"]
            if isinstance(error_detail, dict):
                raw_code = error_detail.get("code")
                # 仅接受非空字符串错误码，上游数字码不臆测转换，其余类型置 None 丢弃。
                error_code = raw_code if isinstance(raw_code, str) and raw_code else None
                if "message" in error_detail:
                    error_message = (
                        f"{error_message}: "
                        f"{truncate_upstream_message_fragment(error_detail['message'])}"
                    )
                    detail_message_extracted = True
            elif isinstance(error_detail, str):
                error_message = (
                    f"{error_message}: {truncate_upstream_message_fragment(error_detail)}"
                )
                detail_message_extracted = True
        if not detail_message_extracted and "message" in response_data:
            error_message = (
                f"{error_message}: "
                f"{truncate_upstream_message_fragment(response_data['message'])}"
            )

    return SeedreamAPIError(
        message=error_message,
        status_code=response_status,
        response_data=response_data,
        error_code=error_code,
        retry_after=retry_after,
    )


# 自定义异常类型到归约档案的映射，按 isinstance 顺序匹配。APIError 按 status 子查表，
# 不在此列表；SeedreamMCPError 基类与未识别异常各自有兜底档案。
_EXCEPTION_PROFILES: tuple[tuple[type, _ErrorProfile], ...] = (
    (SeedreamConfigError, _ErrorProfile("配置错误", "", "config_error")),
    (
        SeedreamValidationError,
        _ErrorProfile("参数验证失败", "请检查对应参数的取值范围。", "validation_error"),
    ),
    (
        SeedreamTimeoutError,
        _ErrorProfile("请求超时", "请检查网络连接或稍后重试。", "timeout_error"),
    ),
    (
        SeedreamNetworkError,
        _ErrorProfile("网络连接错误", "请检查网络连接。", "network_error"),
    ),
)

# SeedreamMCPError 基类兜底与未识别异常兜底。
_GENERIC_MCP_PROFILE = _ErrorProfile("操作失败", "", "generation_failed")
_UNKNOWN_PROFILE = _ErrorProfile("未知错误", "", "generation_failed")


def resolve_error_profile(error: Exception) -> _ErrorProfile:
    """将任意异常归约为统一的错误档案，供展示标题、用户建议与结构化错误码共用。

    APIError 按状态码查 _HTTP_STATUS_PROFILES，其余按异常类型匹配，基类与未识别
    异常回退到各自兜底档案。
    """
    if isinstance(error, SeedreamAPIError):
        return _lookup_http_error_profile(error.status_code or 0)
    for exc_type, profile in _EXCEPTION_PROFILES:
        if isinstance(error, exc_type):
            return profile
    if isinstance(error, SeedreamMCPError):
        return _GENERIC_MCP_PROFILE
    return _UNKNOWN_PROFILE


def format_error_for_user(error: Exception) -> str:
    """按异常类型将错误格式化为面向用户的提示文案。

    展示标题与可操作建议取自 resolve_error_profile 归约档案；message 与错误码均
    先截断再脱敏，上游回显的敏感片段不进入用户可见输出；仅 APIError 携带错误码时
    附加错误码提示。
    """
    profile = resolve_error_profile(error)
    if isinstance(error, SeedreamAPIError):
        raw_message = error.message
        # 错误码是上游可回显的自由文本，经 sanitize_error_text 与 message 同口径净化。
        code_hint = (
            f" [错误码: {sanitize_error_text(error.error_code)}]" if error.error_code else ""
        )
    elif isinstance(error, SeedreamMCPError):
        raw_message = error.message
        code_hint = ""
    else:
        raw_message = str(error)
        code_hint = ""
    message = _sanitize_message_for_output(raw_message)

    line = f"{profile.display_title}: {message}{code_hint}"
    if profile.user_hint:
        line += f"\n{profile.user_hint}"
    return line
