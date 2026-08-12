"""
Seedream MCP工具 - 错误处理模块

定义各种异常类型和错误处理函数。
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping, Optional, Dict, Any


class SeedreamMCPError(Exception):
    """Seedream MCP工具基础异常类"""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "error_code": self.error_code,
            "details": self.details,
        }


class SeedreamConfigError(SeedreamMCPError):
    """配置相关错误"""

    pass


class SeedreamAPIError(SeedreamMCPError):
    """API调用相关错误"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_data: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        retry_after: Optional[float] = None,
    ):
        super().__init__(message, error_code=error_code)
        self.status_code = status_code
        self.response_data = response_data or {}
        self.retry_after = retry_after

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = super().to_dict()
        # message 可能拼入上游回显片段，超长时截断，避免潜在敏感内容撑爆输出
        result["message"] = _truncate_value_for_output(result.get("message"), limit=500)
        result.update(
            {
                "status_code": self.status_code,
                "response_data": _sanitize_response_data(self.response_data),
            }
        )
        return result


class SeedreamValidationError(SeedreamMCPError):
    """参数验证错误"""

    def __init__(self, message: str, field: Optional[str] = None, value: Optional[Any] = None):
        super().__init__(message)
        self.field = field
        self.value = value

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = super().to_dict()
        result.update(
            {
                "field": self.field,
                "value": _truncate_value_for_output(self.value),
            }
        )
        return result


class SeedreamTimeoutError(SeedreamMCPError):
    """超时错误"""

    pass


class SeedreamNetworkError(SeedreamMCPError):
    """网络连接错误"""

    pass


# Retry-After 上限：即便服务器返回更大值，单次退避也不超过此值，避免被诱导长时间睡眠
_MAX_RETRY_AFTER_SECONDS = 300.0


def parse_retry_after(headers: Mapping[str, str]) -> Optional[float]:
    """解析 HTTP Retry-After 头为等待秒数。

    支持 delta-seconds 与 HTTP-date 两种格式；解析失败或非正返回 None。
    返回值限制在 _MAX_RETRY_AFTER_SECONDS 以内。
    """
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    try:
        seconds = float(raw)
        if seconds >= 0:
            return min(seconds, _MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(raw)
        if target is not None:
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            delta = (target - datetime.now(timezone.utc)).total_seconds()
            if delta > 0:
                return min(delta, _MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def handle_api_error(
    response_status: int,
    response_data: Dict[str, Any],
    retry_after: Optional[float] = None,
) -> SeedreamAPIError:
    """处理API错误响应

    Args:
        response_status: HTTP状态码
        response_data: 响应数据
        retry_after: 服务器建议的重试等待秒数，取自 Retry-After 头

    Returns:
        SeedreamAPIError实例
    """
    error_message = "API调用失败"

    # 根据状态码提供更具体的错误信息
    if response_status == 400:
        error_message = "请求参数错误"
    elif response_status == 401:
        error_message = "API密钥无效或已过期"
    elif response_status == 403:
        error_message = "访问被拒绝，请检查API权限"
    elif response_status == 404:
        error_message = "API端点不存在"
    elif response_status == 429:
        error_message = "请求频率超限，请稍后重试"
    elif response_status >= 500:
        error_message = "服务器内部错误"

    # 尝试从响应中提取更详细的错误信息与错误码
    error_code: Optional[str] = None
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


def format_error_for_user(error: Exception) -> str:
    """格式化错误信息供用户查看

    Args:
        error: 异常实例

    Returns:
        格式化的错误信息字符串
    """
    if isinstance(error, SeedreamConfigError):
        return f"配置错误: {error.message}"
    elif isinstance(error, SeedreamAPIError):
        code_hint = f" [错误码: {error.error_code}]" if error.error_code else ""
        # message 可能回显上游响应，截断防长敏感片段进入用户可见输出
        message = error.message[:500]
        if error.status_code == 401:
            return f"认证失败: {message}{code_hint}\n请检查您的API密钥是否正确设置。"
        elif error.status_code == 429:
            return f"请求频率超限: {message}{code_hint}\n请稍后重试。"
        else:
            return f"API调用失败: {message}{code_hint}"
    elif isinstance(error, SeedreamValidationError):
        return f"参数验证失败: {error.message}"
    elif isinstance(error, SeedreamTimeoutError):
        return f"请求超时: {error.message}\n请检查网络连接或稍后重试。"
    elif isinstance(error, SeedreamNetworkError):
        return f"网络连接错误: {error.message}\n请检查网络连接。"
    elif isinstance(error, SeedreamMCPError):
        return f"操作失败: {error.message}"
    else:
        return f"未知错误: {str(error)}"


# 异常 value 序列化时的长度上限：避免 data URI 等大对象撑爆日志/结构化响应
_VALUE_OUTPUT_LIMIT = 200


def _truncate_value_for_output(value: Any, limit: int = _VALUE_OUTPUT_LIMIT) -> Any:
    """截断过长的异常 value，防止 data URI、大字典等撑爆日志或结构化响应。

    - 字符串超限：保留前 ``limit`` 字符并标注原长度。
    - dict/list 超限：仅保留类型与元素个数摘要。
    - None 或未超限：原样返回。
    """
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"<truncated:{len(value)} chars> {value[:limit]}..."
    if isinstance(value, (dict, list)):
        try:
            repr_len = len(repr(value))
        except Exception:
            return f"<{type(value).__name__}>"
        if repr_len <= limit:
            return value
        if isinstance(value, dict):
            return f"<truncated:dict, {len(value)} keys>"
        return f"<truncated:list, {len(value)} items>"
    return value


# 敏感字段关键词：键名包含任一关键词即视为敏感，输出时以 *** 脱敏
_SENSITIVE_KEY_KEYWORDS = (
    "key",
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "authorization",
    "auth",
    "apikey",
    "cookie",
    "session",
    "jwt",
    "assertion",
    "signature",
    "nonce",
    "saml",
)


def _filter_sensitive_data(data: Any) -> Any:
    """递归过滤字典/列表中的敏感字段。

    键名命中敏感关键词的值替换为 ***，其余递归处理；非容器类型原样返回。
    """
    if isinstance(data, dict):
        return {
            key: (
                "***"
                if any(keyword in str(key).lower() for keyword in _SENSITIVE_KEY_KEYWORDS)
                else _filter_sensitive_data(value)
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_filter_sensitive_data(item) for item in data]
    return data


def _sanitize_response_data(data: Any) -> Any:
    """对 API 响应数据先脱敏再截断，避免敏感信息或大对象进入结构化错误输出。"""
    return _truncate_value_for_output(_filter_sensitive_data(data))
