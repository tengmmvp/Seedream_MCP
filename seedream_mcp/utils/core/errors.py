"""Seedream MCP 错误处理模块。

定义工具集的自定义异常类型层级，以及 HTTP 错误响应的归约与用户可见信息格式化。
所有自定义异常以 SeedreamMCPError 为根，按场景派生配置、API、校验、超时、网络等
子类，便于上层按异常类型分支处理与重试决策。
"""

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from typing import Mapping, Any, TypeVar, cast


class SeedreamMCPError(Exception):
    """所有 Seedream MCP 自定义异常的基类。

    提供 message、error_code、details 公共字段，并通过 to_dict 序列化为结构化输出；
    配置、API、校验、超时、网络等场景均派生对应子类，便于按类型捕获与分支处理。
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

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，供结构化错误输出使用。

        message 统一剥离敏感键值与 Bearer 令牌并截断，details 经敏感字段过滤，
        避免上游回显的敏感片段进入结构化输出；子类无需各自处理 message 与 details。
        """
        return {
            "error": self.__class__.__name__,
            "message": _truncate_value_for_output(
                _redact_sensitive_message(self.message), limit=_MESSAGE_OUTPUT_LIMIT
            ),
            "error_code": self.error_code,
            "details": _filter_sensitive_data(self.details),
        }


class SeedreamConfigError(SeedreamMCPError):
    """配置加载或校验失败时抛出。"""

    pass


class SeedreamAPIError(SeedreamMCPError):
    """API 调用失败时抛出。

    额外携带 status_code、response_data、retry_after，供上层判定可重试性与退避时长。
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

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，在基类基础上补充 status_code 与脱敏后的 response_data。"""
        result = super().to_dict()
        result.update(
            {
                "status_code": self.status_code,
                "response_data": _sanitize_response_data(self.response_data),
            }
        )
        return result


class SeedreamValidationError(SeedreamMCPError):
    """请求参数校验失败时抛出，附带出错的字段名与值。"""

    def __init__(self, message: str, field: str | None = None, value: Any | None = None):
        super().__init__(message)
        self.field = field
        self.value = value

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，在基类基础上补充出错的字段名与值。"""
        result = super().to_dict()
        result.update(
            {
                "field": self.field,
                "value": _truncate_value_for_output(
                    _filter_sensitive_data(self.value)
                    if isinstance(self.value, (dict, list))
                    else _sanitize_output_string(self.value)
                ),
            }
        )
        return result


class SeedreamTimeoutError(SeedreamMCPError):
    """请求超时时抛出。"""

    pass


class SeedreamNetworkError(SeedreamMCPError):
    """网络连接失败时抛出。"""

    pass


# Retry-After 下限：即便服务器返回 0 或极小值也至少等待此值，避免紧密重试风暴
_MIN_RETRY_AFTER_SECONDS = 1.0
# Retry-After 上限：即便服务器返回更大值，单次退避也不超过此值，避免被诱导长时间睡眠
_MAX_RETRY_AFTER_SECONDS = 300.0


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """解析 HTTP Retry-After 头为等待秒数。

    支持 delta-seconds 与 HTTP-date 两种格式；解析失败或负值返回 None。
    0 与极小值按最小下限兜底；返回值限制在 [_MIN_RETRY_AFTER_SECONDS, _MAX_RETRY_AFTER_SECONDS] 区间内。
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
# 结构化错误码的单点映射。handle_api_error、format_error_for_user 与
# _classify_generation_error_type 三处共用此档案，新增状态码或调整文案仅需改这一处，
# 避免三套并行分支相互漂移。
@dataclass(frozen=True)
class _ErrorProfile:
    """单条错误归约档案。

    base_message 仅 HTTP 状态档案使用，作为 handle_api_error 拼装 SeedreamAPIError.message
    的初始文案；异常类型档案不使用该字段。
    """

    display_title: str
    user_hint: str
    error_code: str
    base_message: str = ""


# 精确状态码档案。display_title 与 user_hint 供 format_error_for_user，base_message 供
# handle_api_error，error_code 供 _classify_generation_error_type。
_HTTP_STATUS_PROFILES: dict[int, _ErrorProfile] = {
    400: _ErrorProfile("API调用失败", "", "api_error", base_message="请求参数错误"),
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
        base_message="余额不足",
    ),
    403: _ErrorProfile(
        "API调用失败",
        "请确认 API Key 已开通目标模型的调用权限。",
        "api_error",
        base_message="访问被拒绝，请检查API权限",
    ),
    404: _ErrorProfile("API调用失败", "", "api_error", base_message="API端点不存在"),
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
        base_message="请求频率超限，请稍后重试",
    ),
}

# 5xx 与未列举状态码的兜底档案
_HTTP_5XX_PROFILE = _ErrorProfile("API调用失败", "", "api_error", base_message="服务器内部错误")
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

    状态码专属基础文案取自 _HTTP_STATUS_PROFILES，再尝试从响应体提取上游 error.code 与
    message 拼入文案；status_code 与 retry_after 原样保留在返回的异常上，由上层据此判定
    可重试性与退避时长。

    Args:
        response_status: HTTP 状态码。
        response_data: 响应体数据，可能含上游 error/message 字段。
        retry_after: 服务器建议的重试等待秒数，取自 Retry-After 头。

    Returns:
        装配好状态码与错误码的 SeedreamAPIError 实例。
    """
    error_message = _lookup_http_error_profile(response_status).base_message

    # 尝试从响应体中提取更详细的上游错误信息与错误码
    error_code: str | None = None
    if isinstance(response_data, dict):
        if "error" in response_data:
            error_detail = response_data["error"]
            if isinstance(error_detail, dict):
                error_code = error_detail.get("code")
                if "message" in error_detail:
                    error_message = f"{error_message}: {error_detail['message']}"
            elif isinstance(error_detail, str):
                error_message = f"{error_message}: {error_detail}"
        elif "message" in response_data:
            error_message = f"{error_message}: {response_data['message']}"

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
    (SeedreamValidationError, _ErrorProfile("参数验证失败", "", "validation_error")),
    (
        SeedreamTimeoutError,
        _ErrorProfile("请求超时", "请检查网络连接或稍后重试。", "timeout_error"),
    ),
    (
        SeedreamNetworkError,
        _ErrorProfile("网络连接错误", "请检查网络连接。", "network_error"),
    ),
)

# SeedreamMCPError 基类兜底与未识别异常兜底
_GENERIC_MCP_PROFILE = _ErrorProfile("操作失败", "", "generation_failed")
_UNKNOWN_PROFILE = _ErrorProfile("未知错误", "", "generation_failed")


def resolve_error_profile(error: Exception) -> _ErrorProfile:
    """将任意异常归约为统一的错误档案，供展示标题、用户建议与结构化错误码共用。

    APIError 优先按 HTTP 状态码查 _HTTP_STATUS_PROFILES；其余按异常类型匹配；
    SeedreamMCPError 基类与未识别异常回退到各自兜底档案。
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

    展示标题与可操作建议取自 resolve_error_profile 归约档案；message 统一截断，避免上游
    回显的长敏感片段进入用户可见输出；仅 APIError 携带上游错误码时附加错误码提示。

    Args:
        error: 异常实例。

    Returns:
        格式化的错误信息字符串。
    """
    profile = resolve_error_profile(error)
    if isinstance(error, SeedreamAPIError):
        raw_message = error.message
        code_hint = f" [错误码: {error.error_code}]" if error.error_code else ""
    elif isinstance(error, SeedreamMCPError):
        raw_message = error.message
        code_hint = ""
    else:
        raw_message = str(error)
        code_hint = ""
    # 三类异常统一先剥离敏感键值与 Bearer 令牌再截断，避免任何分支的敏感片段进入用户可见输出
    message = _truncate_value_for_output(
        _redact_sensitive_message(raw_message), limit=_MESSAGE_OUTPUT_LIMIT
    )

    line = f"{profile.display_title}: {message}{code_hint}"
    if profile.user_hint:
        line += f"\n{profile.user_hint}"
    return line


# 异常 value 序列化时的长度上限：避免 data URI 等大对象撑爆日志/结构化响应
_VALUE_OUTPUT_LIMIT = 200
# 错误消息序列化时的长度上限：避免上游回显的长片段进入用户可见输出或结构化响应
_MESSAGE_OUTPUT_LIMIT = 500
# dict/list 元素数超过此值即跳过 repr 直接给摘要，避免大集合 repr 造成内存放大
_CONTAINER_REPR_ELEMENT_LIMIT = 50


def _container_summary(value: Any) -> str:
    """返回 dict/list 的元素数摘要，用于超限或元素过多时替代完整 repr。"""
    if isinstance(value, dict):
        return f"<truncated:dict, {len(value)} keys>"
    return f"<truncated:list, {len(value)} items>"


def _truncate_value_for_output(value: Any, limit: int = _VALUE_OUTPUT_LIMIT) -> Any:
    """截断过长的异常 value，防止 data URI、大字典等撑爆日志或结构化响应。

    - 字符串超限：保留前 ``limit`` 字符并标注原长度。
    - dict/list 元素过多或 repr 超限：仅保留类型与元素个数摘要。
    - None 或未超限：原样返回。

    dict/list 先按元素数短路，仅对小集合计算 repr 判长，避免大集合 repr 造成内存放大。
    """
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"<truncated:{len(value)} chars> {value[:limit]}..."
    if isinstance(value, (dict, list)):
        if len(value) > _CONTAINER_REPR_ELEMENT_LIMIT:
            return _container_summary(value)
        try:
            repr_len = len(repr(value))
        except Exception:
            return f"<{type(value).__name__}>"
        if repr_len <= limit:
            return value
        return _container_summary(value)
    return value


# 敏感字段关键词：键名经边界匹配命中任一关键词即视为敏感，输出时以 *** 脱敏。
# 边界匹配要求键名等于关键词或以下划线、连字符分隔包含关键词，避免短词如 key 误命中
# monkey、keyboard 等无关键名。
_SENSITIVE_KEY_KEYWORDS = (
    "key",
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "auth",
    "cookie",
    "session",
    "jwt",
    "assertion",
    "signature",
    "nonce",
    "saml",
)

# 高确信度敏感词：自身足够特异性，直接子串匹配以覆盖 x-authorization、my-apikey
# 等连字符或无分隔变体，无需边界限定
_SENSITIVE_KEY_SUBSTRINGS = ("authorization", "apikey")


# Bearer 鉴权头令牌模式：上游错误体回显鉴权头时据此剥离令牌，防止其进入结构化输出
_BEARER_TOKEN_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)

# 敏感键值裸值模式：authorization/apikey/token/secret 类键名后跟分隔符（: 或 =）与值时，
# 剥离值部分，覆盖 api-key=xxx、apikey: xxx、Authorization: Basic xxx、token=xxx、
# client_secret=yyy 等上游错误体回显形态。可选的第二个空白分隔词用于吸收
# Authorization 的 scheme（如 Basic），避免仅剥离 scheme 而泄露凭据。token/secret 变体
# 同样要求分隔符存在，普通文本中的词形不受影响。
_SENSITIVE_KEYVALUE_PATTERN = re.compile(
    r"(?i)(api[-_]?key|authorization|(?:access|auth|refresh|session|api)[-_]?token"
    r"|(?:client|api|signing|app)[-_]?secret|token|secret)([ \t]*[:=][ \t]*)\S+(?:[ \t]+\S+)?"
)

# CR/LF 控制字符模式：上游错误体可能携带换行，剥离以防止日志注入伪造行，与
# io_download.sanitize_url 的控制字符剥离对齐。替换为空格保留词边界可读性。
_CONTROL_CHARS_PATTERN = re.compile(r"[\r\n]")


def _redact_bearer_tokens(value: Any) -> Any:
    """剥离字符串值中的 Bearer 令牌，保留 Bearer 前缀以保留语义。"""
    if isinstance(value, str):
        return _BEARER_TOKEN_PATTERN.sub(r"\1***", value)
    return value


_SanitizedValue = TypeVar("_SanitizedValue")


def _sanitize_output_string(value: _SanitizedValue) -> _SanitizedValue:
    """对字符串值剥离敏感键值裸值、Bearer 令牌与 CRLF 控制字符。

    message 与 details/value/response_data 等结构化字段共用此净化，使各字段对敏感
    片段与日志注入的防护完全一致；非字符串原样返回。先剥 authorization/apikey 键名
    后的裸值，再剥残留 Bearer 令牌，末尾剥 CR/LF 防日志注入。
    """
    if not isinstance(value, str):
        return value
    redacted = _SENSITIVE_KEYVALUE_PATTERN.sub(r"\1\2***", value)
    redacted = _BEARER_TOKEN_PATTERN.sub(r"\1***", redacted)
    return cast("_SanitizedValue", _CONTROL_CHARS_PATTERN.sub(" ", redacted))


def _redact_sensitive_message(value: str) -> str:
    """剥离 message 中的敏感键值裸值、Bearer 令牌与 CRLF。

    委托 _sanitize_output_string，与 details/value/response_data 等结构化字段共用同一
    净化实现，避免两处脱敏逻辑漂移。
    """
    return _sanitize_output_string(value)


def _is_sensitive_key(key: Any) -> bool:
    """判断键名是否命中敏感关键词。

    高确信度词采用子串匹配以覆盖连字符与无分隔变体；其余关键词采用边界匹配，
    键名等于关键词或以下划线、连字符分隔包含关键词方视为命中，避免短词如 key 误匹配。
    """
    key_lower = str(key).lower()
    for substring in _SENSITIVE_KEY_SUBSTRINGS:
        if substring in key_lower:
            return True
    for keyword in _SENSITIVE_KEY_KEYWORDS:
        if (
            key_lower == keyword
            or key_lower.endswith("_" + keyword)
            or key_lower.endswith("-" + keyword)
            or key_lower.endswith("." + keyword)
            or key_lower.startswith(keyword + "_")
            or key_lower.startswith(keyword + "-")
            or key_lower.startswith(keyword + ".")
        ):
            return True
    return False


def _filter_sensitive_data(data: Any) -> Any:
    """递归过滤字典/列表中的敏感字段。

    键名命中敏感关键词的值替换为 ***；非敏感键的字符串值额外剥离 Bearer 令牌模式，
    防止上游错误体回显的鉴权头进入结构化输出。非容器类型原样返回。
    """
    if isinstance(data, dict):
        return {
            key: (
                "***"
                if _is_sensitive_key(key)
                else _filter_sensitive_data(_sanitize_output_string(value))
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_filter_sensitive_data(_sanitize_output_string(item)) for item in data]
    return data


def _sanitize_response_data(data: Any) -> Any:
    """对 API 响应数据先脱敏再截断，避免敏感信息或大对象进入结构化错误输出。"""
    return _truncate_value_for_output(_filter_sensitive_data(data))
