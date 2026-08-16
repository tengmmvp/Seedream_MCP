"""Seedream MCP 错误处理模块。

定义工具集的自定义异常类型层级，以及 HTTP 错误响应的归约与用户可见信息格式化。
所有自定义异常以 SeedreamMCPError 为根，按场景派生配置、API、校验、超时、网络等
子类，便于上层按异常类型分支处理与重试决策。
"""

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from typing import Mapping, Any, TypeVar, cast


class SeedreamMCPError(Exception):
    """所有 Seedream MCP 自定义异常的基类。

    配置、API、校验、超时、网络等场景均派生对应子类，便于按类型捕获与分支处理。

    Attributes:
        message: 人类可读的错误描述文本。
        error_code: 结构化错误码，未提供时为 None。
        details: 附加上下文键值对，未提供时为空字典。
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

        message 先截断再剥离敏感键值与 Bearer 令牌，与 sanitize_error_text 声明的
        truncate-first 次序一致，非字符串形态先归一化为文本再进入该管线；error_code
        为上游可回显的自由文本，过与 message 同口径的截断与净化，None 保持为 None；
        details 与 response_data 同口径先截断后过滤，超大容器收敛为元素数摘要，上游
        回显的敏感片段不进入结构化输出；子类无需各自处理 message 与 details。
        """
        return {
            "error": self.__class__.__name__,
            "message": _sanitize_message_for_output(self.message),
            "error_code": (
                None if self.error_code is None else _sanitize_message_for_output(self.error_code)
            ),
            "details": _sanitize_response_data(self.details),
        }


class SeedreamConfigError(SeedreamMCPError):
    """配置加载或校验失败。"""

    pass


class SeedreamAPIError(SeedreamMCPError):
    """API 调用失败。

    上层依据状态码与退避秒数判定可重试性与退避时长。

    Attributes:
        status_code: HTTP 状态码，未提供时为 None。
        response_data: 上游响应体数据，未提供时为空字典。
        retry_after: 服务器建议的重试等待秒数，未提供时为 None。
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
    """请求参数校验失败。

    Attributes:
        field: 出错的参数字段名，未提供时为 None。
        value: 出错的参数值，未提供时为 None。
    """

    def __init__(self, message: str, field: str | None = None, value: Any | None = None):
        super().__init__(message)
        self.field = field
        self.value = value

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，在基类基础上补充出错的字段名与值。

        value 的净化次序与 details/response_data 同口径先截断后脱敏：容器走
        _sanitize_response_data，字符串与 bytes 走 _sanitize_message_for_output，
        其中 bytes 先归一化为文本再截断脱敏、不绕过任一防线，数值与布尔原样保留。
        """
        if isinstance(self.value, (dict, list)):
            sanitized_value: Any = _sanitize_response_data(self.value)
        elif isinstance(self.value, (str, bytes)):
            sanitized_value = _sanitize_message_for_output(self.value, limit=_VALUE_OUTPUT_LIMIT)
        else:
            sanitized_value = _sanitize_output_string(self.value)
        result = super().to_dict()
        result.update({"field": self.field, "value": sanitized_value})
        return result


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
    """解析 HTTP Retry-After 头为等待秒数。

    支持 delta-seconds 与 HTTP-date 两种格式；解析失败或负值返回 None。
    0 与极小值按最小下限兜底；返回值限制在 [_MIN_RETRY_AFTER_SECONDS, _MAX_RETRY_AFTER_SECONDS] 区间内。

    Args:
        headers: HTTP 响应头映射，同时读取 retry-after 与 Retry-After 两种键名。

    Returns:
        建议等待的秒数，已收敛到上下限区间内；头部缺失、无法解析或
        目标时间不晚于当前时刻时为 None。
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

    Attributes:
        display_title: 面向用户的展示标题，供 format_error_for_user 使用。
        user_hint: 面向用户的可操作建议，可为空字符串。
        error_code: 结构化错误码，供错误分类使用。
        base_message: 仅 HTTP 状态档案使用，作为 handle_api_error 拼装
            SeedreamAPIError.message 的初始文案；异常类型档案留空。
    """

    display_title: str
    user_hint: str
    error_code: str
    base_message: str = ""


# 精确状态码档案。display_title 与 user_hint 供 format_error_for_user，base_message 供
# handle_api_error，error_code 供 _classify_generation_error_type。
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
        base_message="余额不足",
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
        base_message="请求频率超限，请稍后重试",
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


# 上游错误 message 片段拼入异常文案前的截断上限：错误体可能为任意长度的自由文本，
# 截断避免超大文本随异常 message 进入日志形成巨型日志行。
_UPSTREAM_MESSAGE_FRAGMENT_LIMIT = 8 * 1024


def _normalize_non_str_message(value: Any) -> str:
    """将非字符串 message 归一化为文本：dict/list 以 JSON 序列化，其余取 str。

    JSON 序列化产出带双引号的键值形态，嵌套字符串内的引号与换行被转义为反斜杠
    形态，键名后的引号与转义序列由 _SENSITIVE_KEYVALUE_SEPARATOR 的引号变体与
    转义容忍覆盖，凭据仍被键值脱敏命中；序列化与 str() 对超深嵌套结构都会触发
    解释器递归上限，逐级回退后以类型占位符兜底，保证任何形态的 message 分量都能
    进入字符串脱敏管线，不再借 dict/list 形态穿透，RecursionError 也不外逃。
    """
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError, RecursionError):
            try:
                return str(value)
            except RecursionError:
                return f"<unprintable:{type(value).__name__}>"
    return str(value)


def normalize_message_text(value: Any) -> str:
    """将任意形态的错误 message 分量归一化为文本，字符串原样返回。

    dict/list 走 JSON 序列化，其余非字符串取 str，超深嵌套逐级回退到类型占位符。
    结果净化出口对非字符串 message 先经本函数归一化再过 sanitize_error_text，
    凭据不借 dict/list 形态在净化之后经插值穿透文本与结构化输出通道。
    """
    return value if isinstance(value, str) else _normalize_non_str_message(value)


def truncate_upstream_message_fragment(value: Any) -> str:
    """归一化并截断上游错误 message 片段至 8KB，超长时保留前缀并标注原长度。

    handle_api_error 将上游 error/message 字段拼入异常 message，拼接前统一经本函数
    处理：非字符串分量先归一化为文本，dict/list 走 JSON 序列化，防止 dict 形态
    message 经 f-string 插值为 Python repr 后绕过下游键值脱敏；随后截断防止超大
    错误体随异常进入日志。io_sse 对请求级错误事件的 message 拼装共用本函数。

    Args:
        value: 上游错误 message 分量，任意类型。

    Returns:
        归一化并截断至 8KB 的文本，超长时前缀标注原始长度。
    """
    text = normalize_message_text(value)
    if len(text) > _UPSTREAM_MESSAGE_FRAGMENT_LIMIT:
        return f"<truncated:{len(text)} chars> {text[:_UPSTREAM_MESSAGE_FRAGMENT_LIMIT]}..."
    return text


def handle_api_error(
    response_status: int,
    response_data: dict[str, Any],
    retry_after: float | None = None,
) -> SeedreamAPIError:
    """将 HTTP 错误响应归约为 SeedreamAPIError。

    状态码专属基础文案取自 _HTTP_STATUS_PROFILES，再尝试从响应体提取上游 error.code 与
    message 拼入文案，message 片段经 8KB 截断防止超大错误体随异常进入日志；
    status_code 与 retry_after 原样保留在返回的异常上，由上层据此判定可重试性与退避时长。

    Args:
        response_status: HTTP 状态码。
        response_data: 响应体数据，可能含上游 error/message 字段。
        retry_after: 服务器建议的重试等待秒数，取自 Retry-After 头。

    Returns:
        装配好状态码与错误码的 SeedreamAPIError 实例。
    """
    error_message = _lookup_http_error_profile(response_status).base_message

    # 尝试从响应体中提取更详细的上游错误信息与错误码。
    error_code: str | None = None
    if isinstance(response_data, dict):
        if "error" in response_data:
            error_detail = response_data["error"]
            if isinstance(error_detail, dict):
                raw_code = error_detail.get("code")
                # 仅接受非空字符串错误码：上游数字码转字符串属臆测语义，其余类型置 None
                # 丢弃，message 拼装不受影响。
                error_code = raw_code if isinstance(raw_code, str) and raw_code else None
                if "message" in error_detail:
                    error_message = (
                        f"{error_message}: "
                        f"{truncate_upstream_message_fragment(error_detail['message'])}"
                    )
            elif isinstance(error_detail, str):
                error_message = (
                    f"{error_message}: {truncate_upstream_message_fragment(error_detail)}"
                )
        elif "message" in response_data:
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

    APIError 优先按 HTTP 状态码查 _HTTP_STATUS_PROFILES；其余按异常类型匹配；
    SeedreamMCPError 基类与未识别异常回退到各自兜底档案。

    Args:
        error: 待归约的任意异常实例。

    Returns:
        该异常对应的归约档案；未识别异常返回兜底档案。
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

    展示标题与可操作建议取自 resolve_error_profile 归约档案；message 统一先截断再
    剥离敏感片段，截断上限约束脱敏正则的工作长度，上游回显的长敏感片段不进入用户
    可见输出；仅 APIError 携带上游错误码时附加错误码提示，错误码同为上游自由文本，
    拼入前同样过净化管线。

    Args:
        error: 异常实例。

    Returns:
        格式化的错误信息字符串。
    """
    profile = resolve_error_profile(error)
    if isinstance(error, SeedreamAPIError):
        raw_message = error.message
        # 错误码是上游可回显的自由文本，拼入前过净化管线：控制字符压平、敏感片段
        # 剥离、超长截断，与 message 同口径，伪造行与凭据不借错误码进入用户可见输出。
        code_hint = (
            f" [错误码: {sanitize_error_text(error.error_code)}]" if error.error_code else ""
        )
    elif isinstance(error, SeedreamMCPError):
        raw_message = error.message
        code_hint = ""
    else:
        raw_message = str(error)
        code_hint = ""
    # 三类异常统一先截断再剥离敏感键值与 Bearer 令牌，避免任何分支的敏感片段进入用户可见输出；
    # dict/list 形态的 message 在 _sanitize_message_for_output 内归一化为文本，同样被覆盖。
    message = _sanitize_message_for_output(raw_message)

    line = f"{profile.display_title}: {message}{code_hint}"
    if profile.user_hint:
        line += f"\n{profile.user_hint}"
    return line


# 异常 value 序列化时的长度上限：避免 data URI 等大对象撑爆日志/结构化响应。
_VALUE_OUTPUT_LIMIT = 200
# 错误消息序列化时的长度上限：避免上游回显的长片段进入用户可见输出或结构化响应。
_MESSAGE_OUTPUT_LIMIT = 500
# dict/list 元素数超过此值即跳过长度估计直接给摘要，超大容器不进入逐元素遍历。
_CONTAINER_REPR_ELEMENT_LIMIT = 50
# 容器嵌套深度上限：与解释器 repr 递归上限同量级，超过后视为 repr 形态不可用，
# 长度估计返回 None，截断走类型占位符，与既有 RecursionError 兜底口径一致。
_CONTAINER_REPR_DEPTH_LIMIT = 1000
# 非 str/bytes 叶子元素的长度估计常数：任意对象的 repr 长度形态不定，按小常数
# 计入，不为判长物化其 repr。
_CONTAINER_LEAF_LENGTH_ESTIMATE = 16
# 容器单元素的标点开销估计：repr 形态中的引号、冒号、逗号等标点按固定值计入。
_CONTAINER_ELEMENT_OVERHEAD = 6


def _container_summary(value: Any) -> str:
    """返回 dict/list 的元素数摘要，用于超限或元素过多时替代完整 repr。"""
    if isinstance(value, dict):
        return f"<truncated:dict, {len(value)} keys>"
    return f"<truncated:list, {len(value)} items>"


def _estimate_container_output_length(value: Any, limit: int) -> int | None:
    """迭代估计 dict/list 的输出长度，供截断判长使用，不物化完整 repr。

    str/bytes 键与元素直接取 len，嵌套容器递归求和，其余元素按固定小常数计入，
    小元素数容器内的大字符串不再为判长分配整份 repr。total 累计超过 limit 即提前
    返回当前值，小外层容器内嵌超大子容器时判长只走到超限为止，不遍历其全部元素；
    返回值仅供调用方与 limit 比较，超限后的精确值没有意义。显式栈遍历配 id 判重，
    循环引用以常数终止展开；嵌套深度超过 _CONTAINER_REPR_DEPTH_LIMIT 返回 None，
    调用方以类型占位符兜底。
    """
    total = 0
    seen: set[int] = {id(value)}
    pending: list[tuple[Any, int]] = [(value, 1)]

    def leaf_length(item: Any) -> int:
        if isinstance(item, (str, bytes)):
            return len(item) + _CONTAINER_ELEMENT_OVERHEAD
        return _CONTAINER_LEAF_LENGTH_ESTIMATE

    def account_item(item: Any, depth: int) -> None:
        nonlocal total
        if isinstance(item, (dict, list)):
            if id(item) in seen:
                total += _CONTAINER_LEAF_LENGTH_ESTIMATE
                return
            seen.add(id(item))
            pending.append((item, depth + 1))
            return
        total += leaf_length(item)

    while pending:
        node, depth = pending.pop()
        if depth > _CONTAINER_REPR_DEPTH_LIMIT:
            return None
        if isinstance(node, dict):
            for key, item in node.items():
                total += leaf_length(key)
                account_item(item, depth)
                if total > limit:
                    return total
        else:
            for item in node:
                account_item(item, depth)
                if total > limit:
                    return total
    return total


def _truncate_value_for_output(value: Any, limit: int = _VALUE_OUTPUT_LIMIT) -> Any:
    """截断过长的异常 value，防止 data URI、大字典等撑爆日志或结构化响应。

    - 字符串超限：保留前 ``limit`` 字符并标注原长度。
    - dict/list 元素过多或估计长度超限：仅保留类型与元素个数摘要。
    - dict/list 嵌套超深：保留类型占位符。
    - None 或未超限：原样返回。

    dict/list 先按元素数短路，再以元素长度累计估计判长，str/bytes 直接取 len、
    嵌套容器求和、其余按固定小常数，不为判长物化完整 repr，小元素数容器内的
    大字符串不再造成整份 repr 的内存放大。
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
        estimated = _estimate_container_output_length(value, limit=limit)
        if estimated is None:
            return f"<{type(value).__name__}>"
        if estimated <= limit:
            return value
        return _container_summary(value)
    return value


# 敏感字段关键词：键名经边界匹配命中任一关键词即视为敏感，输出时以 *** 脱敏。
# 边界匹配要求键名等于关键词或以下划线、连字符分隔包含关键词，避免短词如 key 误命中
# monkey、keyboard 等无关键名。本清单与 _SENSITIVE_KEY_SUBSTRINGS 同时是自由文本
# 键值脱敏键名交替组的派生源，构成 dict 键与自由文本两条脱敏路径的单一来源。
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

# 高确信度敏感词：自身足够特异性，直接子串匹配以覆盖 x-authorization、my-apikey、
# privatekey、sshkey 等连字符或无分隔变体，无需边界限定。camelCase 复合词 secretKey、
# accessKey、sessionKey、authKey 归一化小写后同样在此命中，dict 键与自由文本两条
# 脱敏路径的键名分支均由本清单派生。
_SENSITIVE_KEY_SUBSTRINGS = (
    "authorization",
    "apikey",
    "privatekey",
    "sshkey",
    "secretkey",
    "accesskey",
    "sessionkey",
    "authkey",
)

# 敏感关键词的段集合与键名切分模式：_is_sensitive_key 按切分段的成员判断使用，
# 集合由关键词清单派生保持单一来源；下划线、连字符、点号、空格四种分隔符经
# 预编译字符类一次切分完成，热路径上避免逐分隔符多次 split 与逐关键词的
# 前缀后缀扫描。
_SENSITIVE_KEY_KEYWORD_SET = frozenset(_SENSITIVE_KEY_KEYWORDS)
_KEY_SEGMENT_SPLIT_PATTERN = re.compile(r"[_\- .]")


# Bearer 鉴权头令牌模式：上游错误体回显鉴权头时据此剥离令牌，防止其进入结构化输出。
_BEARER_TOKEN_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)

# 不参与自由文本键名交替组直接派生的短词：key 与 auth 单独出现特异性不足，
# keyboard、author 一类普通词会被误吞，仅保留受限复合分支覆盖其敏感形态。
_SENSITIVE_KEYVALUE_AMBIGUOUS_KEYWORDS = frozenset({"key", "auth"})

# 键名续段：以 . 、- 或 _ 起头、后接不含分隔符的词字符段。点号纳入续段分隔符后，
# session.id 一类点号键名与 dict 键的点号切分口径一致，自由文本通道同样命中。续段
# 字符类排除 _ 、- 与 . 自身，各续段边界唯一确定，星号失败回溯随总长线性；若续段
# 允许跨分隔字符，嵌套量词的切分歧义会把 session_a_a_a 一类输入的回溯推到指数级。
_SENSITIVE_KEYVALUE_KEY_SUFFIX = r"(?:[._-][^\W_.-]+)*"

# 敏感键名交替组：keyvalue 裸值模式的键匹配与值吸收的停止前瞻共用。分支由
# _SENSITIVE_KEY_SUBSTRINGS 与 _SENSITIVE_KEY_KEYWORDS 派生，特异性足够的词生成
# 「关键词 + 续段」分支，覆盖 session、session_id、session-id、session.id、jwt、
# privatekey 等形态；短词 key 与 auth 走受限复合分支（api-key、auth-token、
# client-secret），复合分支的可选分隔字符与续段同步纳入点号，api.key、access.token
# 与 dict 键的点号切分同口径命中，api key 复合分支允许空格分隔，覆盖 "API Key:
# <凭据>" 形态，键命中仍要求紧跟分隔符与值，普通句中无分隔符的 api key 词组不受
# 影响。后缀形态如 max_tokens 中 token 前还有普通字母，派生分支要求续段以分隔符
# 开头，该词形不受影响。新增敏感词只需扩展上方两清单，本组自动跟进。
_SENSITIVE_KEYVALUE_KEYS = (
    "|".join(
        keyword + _SENSITIVE_KEYVALUE_KEY_SUFFIX
        for keyword in (*_SENSITIVE_KEY_SUBSTRINGS, *_SENSITIVE_KEY_KEYWORDS)
        if keyword not in _SENSITIVE_KEYVALUE_AMBIGUOUS_KEYWORDS
    )
    + r"|api[-_. ]?key"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
    + r"|(?:access|auth|refresh|session|api)[-_.]?token"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
    + r"|(?:client|api|signing|app)[-_.]?secret"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
)

# 键值分隔符与值吸收共用的空白字符类：ASCII 空白加 Unicode 空白（NBSP、Ogham 空格、
# U+2000 至 U+200A 空格区段、行/段分隔符、窄/中数学空格、全角空格），封堵
# password\xa0=\xa0SECRET、token　:　SECRET 一类借 Unicode 空白分隔的绕过形态。
# \n、\r、\x85 与既有的 \t、\x0b、\x0c 一并列入，控制字符压平前的首轮键值匹配中
# 「冒号加换行」一类形态由本类承接空白段；压平后控制字符不复存在，第二轮中这些
# 成员空转，保持类自身完备。
_KEYVALUE_WHITESPACE_CLASS = (
    r"[\t\n\r \x0b\x0c\x85\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"
)

# 控制空白字符类：真实制表符、换行符、回车符、垂直制表符、换页符与 NEL。作为
# 分隔符交替组的独占分支，覆盖无冒号等号的「键后直接跟控制空白再跟值」形态，
# 该形态只在压平前的首轮键值匹配中存在。
_KEYVALUE_CONTROL_WHITESPACE_CLASS = r"[\t\n\r\x0b\x0c\x85]"

# 键值分隔符交替组：有限分支族。字面转义族覆盖反斜杠加 n、r、t、f 的两字符转义
# 与反斜杠 u 加四位十六进制、反斜杠 x 加两位十六进制的转义形态，json.dumps 与
# repr 把实际控制字符转义为字面序列后键名后的凭据仍被命中；冒号等号族容忍可选
# 反斜杠前缀；控制空白分支的尾部前瞻要求其后不再紧跟控制空白，控制空白串中该
# 分支只在串尾唯一位置可命中，与前后空白量词不构成同一串的双重吸收切分路径，
# 失败回溯保持线性。各分支首字符互斥，同一位置至多一个分支命中，交替自身不
# 放大回溯。
_SENSITIVE_KEYVALUE_ALT = (
    r"(?:\\[nrtf]"
    r"|\\u[0-9a-fA-F]{4}"
    r"|\\x[0-9a-fA-F]{2}"
    r"|\\?[:：﹕=＝]"
    r"|" + _KEYVALUE_CONTROL_WHITESPACE_CLASS + r"(?!" + _KEYVALUE_CONTROL_WHITESPACE_CLASS + r"))"
)

# 键值分隔符：允许键名后紧跟至多两段「引号 + 空白」组合，引号前容忍可选反斜杠，
# 引号字符含全角变体（U+FF02 ＂ 与 U+FF07 ＇），覆盖 JSON/Python repr 回显形态
# （{"api_key": "xxx"}、{'api_key': 'xxx'}）、json.dumps 对嵌套字符串的转义产物
# 形态（{\"api_key\": \"xxx\"}）与「引号-空白-引号」形态（api_key '' : secret）；
# 分隔符字符为 ASCII 或全角变体（U+FF1A 全角冒号、U+FE55 小型冒号、U+FF1D 全角
# 等号），转义族与控制空白独占分支见 _SENSITIVE_KEYVALUE_ALT，实际控制字符与
# 其转义产物获得同等覆盖，不再借归一化产物绕过脱敏。空白段取
# _KEYVALUE_WHITESPACE_CLASS，Unicode 空白分隔同样命中。引号与空白组成单一
# 非捕获组且计数受限，反斜杠为可选前缀、分隔符分支为有限交替，控制空白分支经
# 前瞻约束后同一段空白不存在两个量词分别吸收的切分路径，分隔符匹配失败时的
# 回溯保持线性，不随空格数二次方增长。
_SENSITIVE_KEYVALUE_SEPARATOR = (
    _KEYVALUE_WHITESPACE_CLASS
    + r"*(?:\\?['\"＂＇]"
    + _KEYVALUE_WHITESPACE_CLASS
    + r"*){0,2}"
    + _SENSITIVE_KEYVALUE_ALT
    + _KEYVALUE_WHITESPACE_CLASS
    + r"*"
)

# 值吸收的停止前瞻形态：可选前导引号 + 任意键名 + 分隔符，不限于敏感词。前导引号
# 覆盖 JSON 回显中键名自身带引号的形态；下一个词呈现键名加分隔符结构时值吸收停止，
# 该词作为新键值对独立参与后续匹配，非敏感键值对保留原文而非被整体吞掉。
_SENSITIVE_KEYVALUE_ANY_KEY = r"['\"]?[\w-]+" + _SENSITIVE_KEYVALUE_SEPARATOR

# 敏感键值裸值模式：authorization/apikey/token/secret/password/cookie 类键名后跟分隔符
# （ASCII 或全角的 :/=）与值时，剥离值部分，覆盖 api-key=xxx、apikey: xxx、
# Authorization: Basic xxx、token=xxx、client_secret=yyy、password=zzz、
# Cookie: SESSIONID=vvv 等上游错误体回显形态。
# 值吸收为贪婪多词，在下一个任意键名加分隔符形态前停止：多词凭据 secretA secretB
# 整体剥离，同文本中后续键值对无论键名敏感与否都保持独立形态，多键 JSON 回显只
# 脱敏敏感键，相邻非敏感键值对与括号配平得以保留；具体复合键名置于泛化词前，
# 保证交替组按序优先命中长变体。所有键名同样要求分隔符存在，普通文本中的词形不受
# 影响。已知误吞面：Cookie: 巧克力蛋糕食谱推荐 一类无空格整段文本被整体脱敏为
# Cookie: ***，next token: <eos> 一类普通键值的值同样被吞；敏感键后的多词值无法与
# 非敏感尾词可靠区分，按 fail-closed 方向并入脱敏，宁可多脱不漏凭据。
_SENSITIVE_KEYVALUE_PATTERN = re.compile(
    r"(?i)("
    + _SENSITIVE_KEYVALUE_KEYS
    + r")("
    + _SENSITIVE_KEYVALUE_SEPARATOR
    + r")\S+"
    + r"(?:"
    + _KEYVALUE_WHITESPACE_CLASS
    + r"+(?!"
    + _SENSITIVE_KEYVALUE_ANY_KEY
    + r")\S+)*"
)

# 控制字符模式：C0 控制字符、DEL 与 NEL（U+0085）逐字符压平为空格，防止上游错误体
# 或文件名经日志与结构化输出注入伪造行；替换为空格保留词边界可读性。\t 属 C0 区，
# 按日志通道既有口径一并压平，制表符对齐在结构化输出中无语义。logs 的日志消息
# patcher 共用本常量，两模块的控制字符口径单一来源。
CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x1f\x7f\x85]")

# 零宽不可见字符：ZWSP、ZWNJ、BOM 与软连字符。插入键名内部或键与分隔符之间时
# 视觉不可见，键值匹配前整体移除以拼接出真实键名，还原后的 apikey、session.id
# 一类键名照常命中脱敏；替换为空串而非空格，避免把键名切成两段导致脱敏失效。
_INVISIBLE_CHARS_PATTERN = re.compile(r"[\u200b\u200c\ufeff\u00ad]")

# URL userinfo 剥离模式：错误消息或字段值中的 http(s) URL 携带 user:pass@ 凭据时剥去
# userinfo 部分，防止参考图 URL 被拒后的原值回显把凭据送进结构化输出与用户可见文本。
# userinfo 区间为协议前缀后到首个空白、斜杠、问号或井号之前的连续段，密码含 @ 时
# 贪婪取区间内最后一个 @ 作为 userinfo 与主机的边界，user:p@ss@example.com 形态剥净
# 不再残留 ss@ 片段；查询串中的 @ 位于区间之外，无 userinfo 的 URL 不受影响。协议
# 前缀限定 http(s) 且要求词边界，避免误伤普通文本中的 mailto:user@host 形态。
_URL_USERINFO_PATTERN = re.compile(r"(?i)\b((?:https?)://)[^\s/?#]+@")


_SanitizedValue = TypeVar("_SanitizedValue")


def _sanitize_output_string(value: _SanitizedValue) -> _SanitizedValue:
    """对字符串值剥离零宽字符、敏感键值裸值、Bearer 令牌与 URL userinfo。

    message 与 details/value/response_data 等结构化字段共用此净化，使各字段对敏感
    片段与日志注入的防护完全一致；非字符串原样返回。零宽不可见字符先于键值匹配
    移除，拼接出真实键名；随后在控制字符压平前的原文上执行第一次键值匹配，真实
    换行或制表符独占分隔的形态在此命中；压平后再执行第二次键值匹配，覆盖冒号
    等号族、转义产物与引号变体等其余形态；末尾剥 Bearer 令牌与 URL userinfo 凭据。
    """
    if not isinstance(value, str):
        return value
    redacted = _INVISIBLE_CHARS_PATTERN.sub("", value)
    redacted = _SENSITIVE_KEYVALUE_PATTERN.sub(r"\1\2***", redacted)
    redacted = CONTROL_CHARS_PATTERN.sub(" ", redacted)
    redacted = _SENSITIVE_KEYVALUE_PATTERN.sub(r"\1\2***", redacted)
    redacted = _BEARER_TOKEN_PATTERN.sub(r"\1***", redacted)
    return cast("_SanitizedValue", _URL_USERINFO_PATTERN.sub(r"\1", redacted))


def _redact_sensitive_message(value: Any) -> str:
    """剥离 message 中的敏感键值裸值、Bearer 令牌、控制字符与 URL userinfo。

    非字符串 message 先归一化为文本，dict/list 走 JSON 序列化，再进入脱敏管线，
    封堵 dict 形态 message 借 str/repr 的引号形态穿透脱敏的路径。委托
    _sanitize_output_string，与 details/value/response_data 等结构化字段共用同一
    净化实现，避免两处脱敏逻辑漂移。
    """
    return cast("str", _sanitize_output_string(normalize_message_text(value)))


def _sanitize_message_for_output(value: Any, limit: int = _MESSAGE_OUTPUT_LIMIT) -> str:
    """对异常 message 先截断再剥离敏感片段，供 to_dict 与 format_error_for_user 共用。

    与 sanitize_error_text 声明的 truncate-first 次序一致：截断上限约束脱敏正则的
    工作长度，作为对抗超长构造输入的纵深兜底；截断丢弃段中的凭据随截断消失，
    保留段中的凭据再被脱敏剥离，截断点落在键名中间时该键值对不再被命中，其值
    同样已被截断丢弃，不构成泄露。非字符串 message 先归一化为文本，dict/list 走
    JSON 序列化，防止 dict 形态借 str/repr 的引号形态穿透脱敏。
    """
    text = normalize_message_text(value)
    return _redact_sensitive_message(_truncate_value_for_output(text, limit=limit))


def sanitize_error_text(
    message: _SanitizedValue, limit: int = _MESSAGE_OUTPUT_LIMIT
) -> _SanitizedValue:
    """对上游错误文本先截断再剥离敏感片段与控制字符，供全部用户可见输出路径共用。

    先截断后脱敏使正则的工作长度受 limit 约束，作为对抗超长构造输入的纵深兜底；
    截断丢弃段中的凭据随截断消失，保留段中的凭据再被脱敏剥离，两个方向都不残留。
    截断点落在键名中间时该键值对不再被命中，其值同样已被截断丢弃，不构成泄露。

    异常路径经 to_dict/format_error_for_user 已净化；结果数据路径——SSE 失败事件、
    响应 data 项的 error 字段、并行聚合消息、自动保存 error——同样可能携带上游回显
    的鉴权片段，各出口统一经本函数收敛为与异常路径相同的防护，消除两条输出通道的
    不对称。非字符串原样返回。

    Args:
        message: 待净化的错误文本，非字符串输入原样返回。
        limit: 截断长度上限。

    Returns:
        先截断再剥离敏感片段与控制字符后的文本。
    """
    if not isinstance(message, str):
        return message
    return cast(
        "_SanitizedValue",
        _sanitize_output_string(_truncate_value_for_output(message, limit=limit)),
    )


# 数据字段序列化时的防御性长度上限：URL 等数据字段的取值是返回结果可用性的一
# 部分，不施加错误文本的 500 字符截断；16KB 级上限仅防异常超长数据撑爆输出。
_DATA_OUTPUT_LIMIT = 16 * 1024

# 纯 URL 数据字段判定：以 http(s):// 开头且不含任何空白字符的值视为 URL 本体。
_URL_DATA_PREFIX_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_WHITESPACE_PATTERN = re.compile(r"\s")


def _sanitize_url_data_text(value: str, limit: int) -> str:
    """纯 URL 数据字段的轻量净化：截断后仅剥离控制字符、Bearer 令牌与 userinfo。

    URL 的查询参数是 URL 的组成部分而非凭据回显，token=/Secret= 等参数名触发键值
    裸值脱敏会把签名 URL 的查询串整体替换为 ***，数据字段随之不可用；userinfo 与
    Bearer 形态的凭据不受豁免影响，仍在此路径剥离，控制字符压平防御日志注入。
    """
    truncated = cast("str", _truncate_value_for_output(value, limit=limit))
    redacted = CONTROL_CHARS_PATTERN.sub(" ", truncated)
    redacted = _BEARER_TOKEN_PATTERN.sub(r"\1***", redacted)
    return _URL_USERINFO_PATTERN.sub(r"\1", redacted)


def sanitize_data_text(value: _SanitizedValue, limit: int = _DATA_OUTPUT_LIMIT) -> _SanitizedValue:
    """对数据字段文本剥离敏感片段与控制字符，仅保留防御性大上限截断。

    与 sanitize_error_text 的分工：数据字段净化 vs 错误文本净化。url/original_url
    等数据字段的取值本身是返回结果的一部分，截断即破坏可用性——签名 URL 常见
    400-700 字符，500 字符截断会使其不可用；故此处保留 CRLF、敏感键值裸值、
    Bearer 令牌与 URL userinfo 的全套脱敏，仅以 16KB 防御上限兜底，非 URL 文本
    委托 sanitize_error_text 完成净化与截断。

    URL 数据字段不应用键值脱敏：查询参数是 URL 的组成而非凭据回显，命中
    token=/Secret= 等参数名会使签名 URL 不可用；以 http(s):// 开头且不含空白
    的纯 URL 走仅 userinfo 与控制字符的轻量路径，凭据仍在 userinfo 与 Bearer
    处理中覆盖。纯 URL 判定先 strip 首尾空白，带前导空白的 URL 同样按纯 URL
    走轻量路径，签名查询串不被键值脱敏替换为 ***。URL 前缀但含空白或控制字符的
    混合文本不属于纯 URL，仍走全套脱敏，凭据不借 URL 形态逃逸。非字符串原样
    返回。

    Args:
        value: 待净化的数据字段文本，非字符串输入原样返回。
        limit: 防御性截断上限。

    Returns:
        净化后的数据字段文本；纯 URL 走轻量净化路径，其余文本走
        sanitize_error_text 的全套净化路径。
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if _URL_DATA_PREFIX_PATTERN.match(stripped) and _WHITESPACE_PATTERN.search(stripped) is None:
        return cast("_SanitizedValue", _sanitize_url_data_text(stripped, limit=limit))
    return sanitize_error_text(value, limit=limit)


def _is_sensitive_key(key: Any) -> bool:
    """判断键名是否命中敏感关键词。

    高确信度词采用子串匹配以覆盖连字符、无分隔与 camelCase 变体；其余关键词采用
    段匹配，键名按下划线、连字符、点号、空格切分后任一段等于关键词方视为命中，
    位于复合键中段的关键词同样覆盖，user.session_id 与 request-session-id 命中。
    与自由文本路径的一致范围是分隔符定界的复合词；无分隔前缀复合词如
    usersession_id 仅自由文本路径的未锚定匹配命中，键名段匹配不命中。短词 key
    不误匹配 monkey、keyboard，空格分隔规则与自由文本复合分支 api key 一致，
    dict 键 "api key" 同样命中。
    """
    key_lower = str(key).lower()
    for substring in _SENSITIVE_KEY_SUBSTRINGS:
        if substring in key_lower:
            return True
    segments = frozenset(_KEY_SEGMENT_SPLIT_PATTERN.split(key_lower))
    return not segments.isdisjoint(_SENSITIVE_KEY_KEYWORD_SET)


def _filter_sensitive_data(data: Any) -> Any:
    """过滤字典/列表中的敏感字段，深层嵌套经显式栈迭代处理。

    键名命中敏感关键词的值替换为 ***；非敏感键与列表项的字符串值额外剥离
    Bearer 令牌与敏感键值片段，防止上游错误体回显的鉴权头进入结构化输出，
    容器项继续下钻。以显式栈替代递归，超深嵌套不触发解释器递归上限，
    RecursionError 不外逃，与 _normalize_non_str_message 对超深 message 的
    兜底口径对齐。已访问容器经 id 记录，循环引用容器以 <truncated:cyclic>
    占位终止展开，不产生无限循环；判重为全量集合而非祖先集合，同一容器被
    多处共享引用时同样折叠为占位符，本函数的错误数据源为 json.loads 产物
    不产生共享引用，折叠方向 fail-closed 不放大输出。非容器类型原样返回。
    """
    if not isinstance(data, (dict, list)):
        return data
    result: Any = {} if isinstance(data, dict) else []
    seen: set[int] = {id(data)}
    pending: list[tuple[Any, Any]] = [(data, result)]
    while pending:
        source, target = pending.pop()
        if isinstance(source, dict):
            for key, value in source.items():
                if _is_sensitive_key(key):
                    target[key] = "***"
                    continue
                value = _sanitize_output_string(value)
                if isinstance(value, (dict, list)):
                    if id(value) in seen:
                        target[key] = "<truncated:cyclic>"
                        continue
                    child: Any = {} if isinstance(value, dict) else []
                    target[key] = child
                    seen.add(id(value))
                    pending.append((value, child))
                else:
                    target[key] = value
        else:
            for item in source:
                item = _sanitize_output_string(item)
                if isinstance(item, (dict, list)):
                    if id(item) in seen:
                        target.append("<truncated:cyclic>")
                        continue
                    nested: Any = {} if isinstance(item, dict) else []
                    target.append(nested)
                    seen.add(id(item))
                    pending.append((item, nested))
                else:
                    target.append(item)
    return result


def _sanitize_response_data(data: Any) -> Any:
    """对 API 响应数据先截断后脱敏，避免敏感信息或大对象进入结构化错误输出。

    与 sanitize_error_text 的纵深次序一致：截断先行约束脱敏正则的工作长度，超大
    容器先收敛为元素数摘要，正则不再遍历其全部字符串值；截断保留段中的敏感片段
    随后仍被脱敏剥离，两个方向都不残留。
    """
    return _filter_sensitive_data(_truncate_value_for_output(data))
