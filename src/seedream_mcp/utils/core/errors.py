"""Seedream MCP 错误处理模块。

定义以 SeedreamMCPError 为根的自定义异常层级，按场景派生配置、API、校验、超时、
网络等子类，以及 HTTP 错误响应的归约与用户可见信息格式化，供上层按异常类型分支
处理与重试决策。
"""

from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from pydantic import ValidationError

from .sanitizers import (
    ERROR_TEXT_SEGMENT_JOINER,
    _sanitize_message_for_output,
    _truncate_value_for_output,
    normalize_message_text,
    sanitize_assembled_text,
    sanitize_error_text,
    sanitize_url,
    terminate_empty_value_credential_tails,
    truncate_upstream_message_fragment,
)

# 错误文本模板占位符：{name} 形态，单遍替换不重扫替换产物。
_ERROR_TEXT_TOKEN_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class _ParamPolicy(Enum):
    """错误文本参数的渲染策略分类。"""

    PLAIN = "plain"
    TEXT = "text"
    URL = "url"
    DETAIL = "detail"
    SECRET = "secret"


@dataclass(frozen=True)
class _TextParam:
    """错误文本参数：值与渲染策略分类。"""

    value: str
    policy: _ParamPolicy

    def render(self) -> str:
        """按策略渲染单参数：SECRET 恒掩码、URL 剥凭据与查询，其余原值。"""
        if self.policy is _ParamPolicy.SECRET:
            return "***"
        if self.policy is _ParamPolicy.URL:
            return sanitize_url(self.value)
        return self.value


def as_plain(value: Any) -> _TextParam:
    """代码常量与派生数值参数。"""
    return _TextParam(normalize_message_text(value), _ParamPolicy.PLAIN)


def as_text(value: Any) -> _TextParam:
    """可示动态文本参数（配置值、路径、用户输入、第三方 str）。"""
    return _TextParam(normalize_message_text(value), _ParamPolicy.TEXT)


def as_url(value: Any) -> _TextParam:
    """URL 参数：两通道均剥 userinfo 与查询参数。"""
    return _TextParam(normalize_message_text(value), _ParamPolicy.URL)


def as_detail(value: Any) -> _TextParam:
    """上游或第三方自由文本参数（最高不信等级）。"""
    return _TextParam(normalize_message_text(value), _ParamPolicy.DETAIL)


def as_secret(value: Any) -> _TextParam:
    """密钥材料参数：任何渲染通道不得明文。"""
    return _TextParam(normalize_message_text(value), _ParamPolicy.SECRET)


# 清单参数经 str 取 Python 列表字面量（单引号 repr）定形：裸 list 进入分类器会走
# JSON 序列化改用双引号，同一清单的文案形态随消费侧写法漂移。
def as_plain_list(items: list[str]) -> _TextParam:
    """清单类代码常量参数，渲染为 Python 列表字面量形态。"""
    return as_plain(str(items))


def as_text_list(items: list[str]) -> _TextParam:
    """清单类可示动态文本参数，渲染形态同 as_plain_list。"""
    return as_text(str(items))


class ErrorText:
    """结构化错误文本：静态模板携带分类参数，双通道按策略渲染。

    模板为代码常量（占位符 {name}），动态值一律以分类参数进入；raw_text 供日志
    通道（原文语义），safe_text 供用户可见通道（全套净化）；str() 取 safe 渲染，
    明文仅经 raw_text 显式获取。
    """

    __slots__ = ("template", "params")

    def __init__(self, template: str, params: Mapping[str, _TextParam]):
        tokens = set(_ERROR_TEXT_TOKEN_PATTERN.findall(template))
        names = set(params)
        if tokens != names:
            raise ValueError(
                raw_error_text(
                    "错误文本模板与参数不一致: 占位 {tokens} 参数 {names}",
                    tokens=as_plain_list(sorted(tokens)),
                    names=as_plain_list(sorted(names)),
                )
            )
        self.template = template
        self.params = dict(params)

    def _substitute(self) -> str:
        """参数按策略渲染后单遍替换占位符，替换产物不参与二次匹配。"""
        rendered = {name: param.render() for name, param in self.params.items()}
        return _ERROR_TEXT_TOKEN_PATTERN.sub(lambda match: rendered[match.group(1)], self.template)

    def raw_text(self) -> str:
        """日志通道渲染：SECRET 掩码、URL 净化，其余参数原样。"""
        return self._substitute()

    def safe_text(self) -> str:
        """用户通道渲染：空值尾形终止后经截断与全套脱敏管线，产物幂等。"""
        return sanitize_error_text(terminate_empty_value_credential_tails(self._substitute()))

    def assembled_text(self) -> str:
        """边界渲染：全套净化且不截断，供错误载荷出口（幂等重放口径）。"""
        return sanitize_assembled_text(self._substitute())

    def __str__(self) -> str:
        return self.safe_text()


def error_text(template: str, **params: _TextParam) -> ErrorText:
    """以关键字参数形态构造 ErrorText，模板占位符与参数名双向校验。"""
    return ErrorText(template, params)


def raw_error_text(template: str, **params: _TextParam) -> str:
    """构造并直接取日志通道渲染：内部 builtin 异常与日志侧文本的惯用产线。"""
    return ErrorText(template, params).raw_text()


def safe_error_text(template: str, **params: _TextParam) -> str:
    """构造并直接取用户通道渲染：直达用户输出的 SDK/CLI 边界惯用产线。"""
    return ErrorText(template, params).safe_text()


def base_detail_error_text(base: Any, detail: Any) -> ErrorText:
    """「基底文案: 详情」组合：基底为可信文案常量，详情为不可信自由文本。

    基底经参数进入而非拼入模板：文案演化出花括号不被当作占位符炸构造期校验。
    """
    return error_text(
        "{base}: {detail}",
        base=as_plain(base),
        detail=as_detail(detail),
    )


def detail_error_text(value: Any) -> ErrorText:
    """外来不可信字符串一步包裹为 ErrorText 载荷，经 detail 分类进入双通道渲染。"""
    return error_text("{detail}", detail=as_detail(value))


def render_boundary_error_text(message: str | ErrorText) -> str:
    """错误载荷边界的统一渲染：ErrorText 取不截断的全套净化，str 走幂等净化。"""
    if isinstance(message, ErrorText):
        return message.assembled_text()
    return sanitize_assembled_text(message)


def assemble_error_segments(*segments: str | ErrorText) -> str:
    """多段错误文本以段分隔符拼接：段按日志原文渲染并在段边界终止，空段不参与。

    终止在拼接前对每段文末补空值凭据尾形（幂等）——段边界后拼入的恢复指引在
    出口重放时不被值吸收吞成掩码；ErrorText 段取 raw_text（日志原文语义），
    用户通道净化由出口的边界渲染承担。
    """
    joined: list[str] = []
    for segment in segments:
        text = segment.raw_text() if isinstance(segment, ErrorText) else segment
        if text:
            joined.append(terminate_empty_value_credential_tails(text))
    return ERROR_TEXT_SEGMENT_JOINER.join(joined)


def validation_error_user_message(exc: ValidationError) -> ErrorText:
    """取首条 pydantic 校验错误的字段与原因拼为可定位的用户消息。"""
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", ()))
    return error_text(
        "参数校验失败: {field} {reason}",
        field=as_plain(field or str(first.get("type"))),
        reason=as_text(first.get("msg")),
    )


class SeedreamMCPError(Exception):
    """所有 Seedream MCP 自定义异常的基类，供按类型捕获与分支处理。

    message 接受纯字符串或 ErrorText：字符串走遗留净化路径；ErrorText 时
    str(exc) 与 .message 为日志通道渲染（原文语义），用户可见渲染经
    format_error_for_user 取 safe_text。

    Attributes:
        message: 日志通道渲染文本（SECRET 掩码、URL 净化，其余原文）。
        error_code: 结构化错误码。
    """

    def __init__(
        self,
        message: str | ErrorText,
        error_code: str | None = None,
    ):
        if isinstance(message, ErrorText):
            payload: ErrorText | None = message
            rendered = message.raw_text()
        else:
            payload = None
            rendered = message
        super().__init__(rendered)
        self._error_text_payload = payload
        self._message = rendered
        self.error_code = error_code

    @property
    def message(self) -> str:
        """日志通道渲染文本。"""
        return self._message

    @property
    def error_text_payload(self) -> ErrorText | None:
        """结构化载荷，纯字符串构造时为 None。"""
        return self._error_text_payload


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
        message: str | ErrorText,
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

    def __init__(
        self, message: str | ErrorText, field: str | None = None, value: Any | None = None
    ):
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


def response_reports_failure(result: dict[str, Any]) -> bool:
    """生成结果结构的 success 为假值或 status 显式为 failed 时判定失败。

    结局日志分级与工具输出的 success 字段共用本谓词，两处失败口径不漂移。
    """
    return not bool(result.get("success")) or result.get("status") == "failed"


# HTTP 错误响应与异常类型的统一归约档案：状态码与异常类到展示标题、用户建议、
# 结构化错误码与应答状态的单点映射，handle_api_error、format_error_for_user
# 与 _classify_generation_error_type 共用，webapp 状态表自 http_status 派生，
# 新增状态码或调整文案只需改这一处。
@dataclass(frozen=True)
class _ErrorProfile:
    """单条错误归约档案。

    Attributes:
        display_title: 面向用户的展示标题。
        user_hint: 面向用户的可操作建议，可为空字符串。
        error_code: 结构化错误码。
        http_status: 错误码对外应答的 HTTP 状态码，webapp 状态表由此派生。
        base_message: handle_api_error 拼装 SeedreamAPIError.message 的初始文案，
            仅 HTTP 状态档案使用，异常类型档案留空。
    """

    display_title: str
    user_hint: str
    error_code: str
    http_status: int
    base_message: str = ""


# 精确状态码档案。
_HTTP_STATUS_PROFILES: dict[int, _ErrorProfile] = {
    400: _ErrorProfile(
        "API调用失败",
        "请核对请求参数。",
        "api_error",
        http_status=502,
        base_message="请求参数错误",
    ),
    401: _ErrorProfile(
        "认证失败",
        "请检查您的API密钥是否正确设置。",
        "auth_error",
        http_status=503,
        base_message="API密钥无效或已过期",
    ),
    402: _ErrorProfile(
        "余额不足",
        "请检查账户余额与配额。",
        "payment_required",
        http_status=402,
        base_message="账户余额不足或配额已耗尽",
    ),
    403: _ErrorProfile(
        "API调用失败",
        "请确认 API Key 已开通目标模型的调用权限。",
        "api_error",
        http_status=502,
        base_message="访问被拒绝，请检查API权限",
    ),
    404: _ErrorProfile(
        "API调用失败",
        "请确认 API 端点配置。",
        "api_error",
        http_status=502,
        base_message="API端点不存在",
    ),
    413: _ErrorProfile(
        "请求体过大",
        "请减小参考图尺寸或改用 URL 传入。",
        "payload_too_large",
        http_status=400,
        base_message="请求体过大",
    ),
    422: _ErrorProfile(
        "请求参数语义错误",
        "请检查请求参数语义。",
        "validation_error",
        http_status=400,
        base_message="请求参数语义错误",
    ),
    429: _ErrorProfile(
        "请求频率超限",
        "请稍后重试。",
        "rate_limited",
        http_status=429,
        base_message="上游返回限流",
    ),
}

# 5xx 与未列举状态码的兜底档案。
_HTTP_5XX_PROFILE = _ErrorProfile(
    "API调用失败",
    "服务端暂时不可用，请稍后重试。",
    "api_error",
    http_status=502,
    base_message="服务器内部错误",
)
_HTTP_DEFAULT_PROFILE = _ErrorProfile(
    "API调用失败",
    "",
    "api_error",
    http_status=502,
    base_message="API调用失败",
)


def _lookup_http_error_profile(status_code: int) -> _ErrorProfile:
    """按 HTTP 状态码查归约档案，5xx 与未列举码回退到对应兜底档案。"""
    profile = _HTTP_STATUS_PROFILES.get(status_code)
    if profile is not None:
        return profile
    if 500 <= status_code < 600:
        return _HTTP_5XX_PROFILE
    return _HTTP_DEFAULT_PROFILE


def has_message_value(value: Any) -> bool:
    """None、空串与纯空白串视为缺失，其余形态经归一化后参与错误文案拼接。"""
    if isinstance(value, str):
        return len(value) > 0 and not value.isspace()
    return value is not None


def normalized_upstream_error_code(raw_code: Any) -> str | None:
    """仅接受非空字符串错误码并 strip 归一：上游数字码不臆测转字符串，纯空白码按缺失丢弃。"""
    if isinstance(raw_code, str):
        stripped = raw_code.strip()
        return stripped or None
    return None


def upstream_message_fragment(raw_message: Any) -> str | None:
    """上游 message 缺失时返回 None，非缺失形态经截断归一化为文本片段。"""
    if has_message_value(raw_message):
        return truncate_upstream_message_fragment(raw_message)
    return None


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
    profile = _lookup_http_error_profile(response_status)
    message: str | ErrorText = profile.base_message

    error_code: str | None = None
    if isinstance(response_data, dict):
        # error 键可用形态未拼出 message 时回退查顶层 message，畸形 error 值
        # （非 dict 非 str）不使顶层描述被跳过。
        detail_fragment: str | None = None
        error_detail = response_data.get("error")
        if isinstance(error_detail, dict):
            error_code = normalized_upstream_error_code(error_detail.get("code"))
            detail_fragment = upstream_message_fragment(error_detail.get("message"))
        elif isinstance(error_detail, str):
            detail_fragment = upstream_message_fragment(error_detail)
        if detail_fragment is None:
            detail_fragment = upstream_message_fragment(response_data.get("message"))
        if detail_fragment is not None:
            message = base_detail_error_text(profile.base_message, detail_fragment)

    return SeedreamAPIError(
        message=message,
        status_code=response_status,
        response_data=response_data,
        error_code=error_code,
        retry_after=retry_after,
    )


# 自定义异常类型到归约档案的映射，按 isinstance 顺序匹配。APIError 按 status 子查表，
# 不在此列表；SeedreamMCPError 基类与未识别异常各自有兜底档案。
_EXCEPTION_PROFILES: tuple[tuple[type, _ErrorProfile], ...] = (
    (
        SeedreamConfigError,
        _ErrorProfile("配置错误", "请检查服务端配置后重试。", "config_error", http_status=503),
    ),
    (
        SeedreamValidationError,
        _ErrorProfile(
            "参数验证失败", "请检查对应参数的取值范围。", "validation_error", http_status=400
        ),
    ),
    (
        SeedreamTimeoutError,
        _ErrorProfile("请求超时", "请检查网络连接或稍后重试。", "timeout_error", http_status=504),
    ),
    (
        SeedreamNetworkError,
        _ErrorProfile("网络连接错误", "请检查网络连接。", "network_error", http_status=502),
    ),
)

# SeedreamMCPError 基类兜底与未识别异常兜底。
_GENERIC_MCP_PROFILE = _ErrorProfile("操作失败", "", "generation_failed", http_status=500)
_UNKNOWN_PROFILE = _ErrorProfile("未知错误", "", "generation_failed", http_status=500)


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


def iter_error_profiles() -> Iterator[_ErrorProfile]:
    """按注册表序只读遍历全量归约档案（状态码、异常类型与兜底档案）。

    webapp 状态表与守护测试共用本迭代消费同一注册表，档案增删只在注册表
    单点发生，不暴露可变注册表结构。
    """
    yield from _HTTP_STATUS_PROFILES.values()
    yield from (profile for _, profile in _EXCEPTION_PROFILES)
    yield from (
        _HTTP_5XX_PROFILE,
        _HTTP_DEFAULT_PROFILE,
        _GENERIC_MCP_PROFILE,
        _UNKNOWN_PROFILE,
    )


def format_error_for_user(error: Exception) -> str:
    """按异常类型将错误格式化为面向用户的提示文案。

    展示标题与可操作建议取自 resolve_error_profile 归约档案；ErrorText 载荷经
    safe_text 全套净化，遗留字符串路径经截断脱敏，上游回显的敏感片段不进入用户
    可见输出；仅 APIError 携带错误码时附加错误码提示，错误码与 message 同口径
    净化。
    """
    profile = resolve_error_profile(error)
    code_hint = ""
    if isinstance(error, SeedreamAPIError) and error.error_code:
        # 错误码是上游可回显的自由文本，经 sanitize_error_text 与 message 同口径净化。
        code_hint = f" [错误码: {sanitize_error_text(error.error_code)}]"
    payload = error.error_text_payload if isinstance(error, SeedreamMCPError) else None
    if payload is not None:
        rendered = payload.safe_text()
    elif isinstance(error, SeedreamMCPError):
        rendered = _sanitize_message_for_output(error.message)
    else:
        rendered = _sanitize_message_for_output(str(error))
    # 尾随空白衔接 Bearer 空白分支、空值尾形衔接键值与 Bearer 冒号分支，重放都会把
    # joiner 与指引开头吞成掩码；收敛尾随空白并对空值尾形补掩码终止形态（与
    # sanitize_assembled_text 共用终止逻辑，指引拼接后重放恒等）。
    message = terminate_empty_value_credential_tails(rendered.rstrip())

    line = f"{profile.display_title}: {message}{code_hint}"
    if profile.user_hint:
        line = assemble_error_segments(line, profile.user_hint)
    return line


def failure_message_with_guidance(
    prefix: str, user_facing: str, user_hint: str, fallback_guidance: str
) -> str:
    """装配带恢复指引的失败消息：场景前缀包裹用户文案。

    user_facing 取调用方对同一异常单次计算的 format_error_for_user 产物；
    user_hint 非空时文案已含建议，取日志通道渲染不叠加，空建议以段分隔符追加
    fallback_guidance。

    Args:
        prefix: 失败场景前缀，与「失败：」拼接为消息开头。
        user_facing: 已格式化的用户可见错误文案。
        user_hint: 归约档案携带的可操作建议，空串表示无建议。
        fallback_guidance: 无 user_hint 时追加的回退恢复指引。

    Returns:
        日志通道语义的失败消息字符串。
    """
    failure_line = error_text(
        "{prefix}失败：{detail}",
        prefix=as_plain(prefix),
        detail=as_text(user_facing),
    )
    if user_hint:
        return failure_line.raw_text()
    return assemble_error_segments(failure_line, fallback_guidance)
