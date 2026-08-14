"""错误分类、HTTP 错误归约与用户可见格式化的单元测试。

覆盖 _classify_generation_error_type 的 8 个分支、handle_api_error 的状态码阶梯文案
与上游错误体提取、format_error_for_user 的 isinstance 分支与 message 截断。
"""

import pytest

from seedream_mcp.tools.core.common import _classify_generation_error_type
from seedream_mcp.utils.core.errors import (
    SeedreamAPIError,
    SeedreamConfigError,
    SeedreamMCPError,
    SeedreamNetworkError,
    SeedreamTimeoutError,
    SeedreamValidationError,
    format_error_for_user,
    handle_api_error,
)

# ==================== _classify_generation_error_type：8 分支 ====================


def test_classify_config_error() -> None:
    assert _classify_generation_error_type(SeedreamConfigError("c")) == "config_error"


def test_classify_validation_error() -> None:
    assert _classify_generation_error_type(SeedreamValidationError("v")) == "validation_error"


def test_classify_timeout_error() -> None:
    assert _classify_generation_error_type(SeedreamTimeoutError("t")) == "timeout_error"


def test_classify_network_error() -> None:
    assert _classify_generation_error_type(SeedreamNetworkError("n")) == "network_error"


def test_classify_api_error_401() -> None:
    """APIError status_code=401 归为 auth_error。"""
    exc = SeedreamAPIError("unauthorized", status_code=401)
    assert _classify_generation_error_type(exc) == "auth_error"


def test_classify_api_error_429() -> None:
    """APIError status_code=429 归为 rate_limited。"""
    exc = SeedreamAPIError("too many", status_code=429)
    assert _classify_generation_error_type(exc) == "rate_limited"


def test_classify_api_error_other_status() -> None:
    """APIError 其他状态码归为 api_error。"""
    exc = SeedreamAPIError("server down", status_code=500)
    assert _classify_generation_error_type(exc) == "api_error"


def test_classify_api_error_no_status() -> None:
    """APIError 无状态码（None）也归为 api_error。"""
    exc = SeedreamAPIError("unknown")
    assert _classify_generation_error_type(exc) == "api_error"


def test_classify_unknown_error() -> None:
    """非自定义异常类型归为 generation_failed。"""
    assert _classify_generation_error_type(ValueError("x")) == "generation_failed"


# ==================== handle_api_error：状态码阶梯 ====================


@pytest.mark.parametrize(
    "status,expected_fragment",
    [
        (400, "请求参数错误"),
        (401, "API密钥无效或已过期"),
        (403, "访问被拒绝"),
        (404, "API端点不存在"),
        (429, "请求频率超限"),
        (500, "服务器内部错误"),
        (503, "服务器内部错误"),
    ],
)
def test_handle_api_error_status_ladder(status: int, expected_fragment: str) -> None:
    """各状态码映射到更具体的错误文案前缀。"""
    exc = handle_api_error(status, {})
    assert exc.status_code == status
    assert expected_fragment in exc.message


def test_handle_api_error_default_message_for_unknown_status() -> None:
    """未特判的状态码使用通用默认文案。"""
    exc = handle_api_error(418, {})
    assert "API调用失败" in exc.message


def test_handle_api_error_extracts_upstream_error_dict() -> None:
    """响应体含 error dict 时提取 code 与 message 拼入文案。"""
    exc = handle_api_error(400, {"error": {"code": "BAD_REQUEST", "message": "invalid size"}})
    assert "invalid size" in exc.message
    assert exc.error_code == "BAD_REQUEST"


def test_handle_api_error_drops_non_string_error_code() -> None:
    """上游 error.code 为数字或空串时 error_code 置 None，不臆测转字符串。

    数字码转字符串属臆测语义，与 str | None 注解不符；丢弃时 message 拼装不受影响。
    """
    numeric = handle_api_error(400, {"error": {"code": 40012, "message": "invalid image"}})
    assert numeric.error_code is None
    assert "invalid image" in numeric.message

    empty = handle_api_error(400, {"error": {"code": "", "message": "bad payload"}})
    assert empty.error_code is None
    assert "bad payload" in empty.message


def test_handle_api_error_extracts_upstream_error_string() -> None:
    """响应体 error 为纯字符串时拼入文案。"""
    exc = handle_api_error(400, {"error": "plain error string"})
    assert "plain error string" in exc.message


def test_handle_api_error_extracts_message_field() -> None:
    """响应体无 error 但含 message 字段时拼入文案。"""
    exc = handle_api_error(400, {"message": "msg only"})
    assert "msg only" in exc.message


def test_handle_api_error_preserves_retry_after() -> None:
    """retry_after 原样保留在异常上，由上层判定退避。"""
    exc = handle_api_error(429, {}, retry_after=30.0)
    assert exc.retry_after == 30.0


def test_handle_api_error_preserves_response_data() -> None:
    """原始响应体保留在异常的 response_data 字段。"""
    data = {"error": {"code": "X", "message": "y"}}
    exc = handle_api_error(400, data)
    assert exc.response_data == data


def test_handle_api_error_no_error_code_when_absent() -> None:
    """响应体无 error.code 时 error_code 为 None。"""
    exc = handle_api_error(400, {"message": "no code here"})
    assert exc.error_code is None


# ==================== format_error_for_user：isinstance 分支 ====================


def test_format_config_error() -> None:
    assert "配置错误" in format_error_for_user(SeedreamConfigError("bad config"))


def test_format_api_error_401_hint() -> None:
    """401 错误追加检查 API 密钥的可操作建议。"""
    exc = SeedreamAPIError("token expired", status_code=401)
    result = format_error_for_user(exc)
    assert "认证失败" in result
    assert "API密钥" in result


def test_format_api_error_429_hint() -> None:
    """429 错误追加稍后重试的可操作建议。"""
    exc = SeedreamAPIError("too fast", status_code=429)
    result = format_error_for_user(exc)
    assert "请求频率超限" in result
    assert "稍后重试" in result


def test_format_api_error_other_status() -> None:
    """非 401/429 的 API 错误走通用格式，含错误码提示。"""
    exc = SeedreamAPIError("boom", status_code=500, error_code="INTERNAL")
    result = format_error_for_user(exc)
    assert "API调用失败" in result
    assert "[错误码: INTERNAL]" in result


def test_format_api_error_without_code() -> None:
    """无 error_code 时不追加错误码提示。"""
    exc = SeedreamAPIError("boom", status_code=500)
    result = format_error_for_user(exc)
    assert "API调用失败" in result
    assert "错误码" not in result


def test_format_validation_error() -> None:
    assert "参数验证失败" in format_error_for_user(SeedreamValidationError("bad param"))


def test_format_timeout_error() -> None:
    result = format_error_for_user(SeedreamTimeoutError("timed out"))
    assert "请求超时" in result
    assert "网络" in result


def test_format_network_error() -> None:
    result = format_error_for_user(SeedreamNetworkError("conn refused"))
    assert "网络连接错误" in result


def test_format_generic_seedream_error() -> None:
    """SeedreamMCPError 基类走通用"操作失败"分支。"""
    assert "操作失败" in format_error_for_user(SeedreamMCPError("something"))


def test_format_unknown_error() -> None:
    """非 SeedreamMCPError 异常归为"未知错误"。"""
    assert "未知错误" in format_error_for_user(ValueError("unexpected"))


def test_format_api_error_truncates_long_message() -> None:
    """API 错误 message 超长时被截断，避免上游回显的敏感长片段进入用户可见输出。"""
    long_msg = "x" * 2000
    exc = SeedreamAPIError(long_msg, status_code=500)
    result = format_error_for_user(exc)
    assert len(result) < len(long_msg)
    assert "truncated" in result


def test_format_unknown_error_truncates_long_message() -> None:
    """非自定义异常的长 str 表达式同样被截断。"""
    long_msg = "y" * 2000
    result = format_error_for_user(ValueError(long_msg))
    assert len(result) < len(long_msg)
    assert "truncated" in result


# ==================== handle_api_error：402/413 状态码档案 ====================


def test_handle_api_error_402_payment_required_profile() -> None:
    """402 映射到余额不足档案：message 含余额，结构化错误码为 payment_required。"""
    exc = handle_api_error(402, {})
    assert exc.status_code == 402
    assert "余额" in exc.message
    assert _classify_generation_error_type(exc) == "payment_required"


def test_handle_api_error_402_user_hint_mentions_balance() -> None:
    """402 错误的用户提示含余额相关可操作建议。"""
    exc = handle_api_error(402, {})
    assert "余额" in format_error_for_user(exc)


def test_handle_api_error_413_payload_too_large_profile() -> None:
    """413 映射到请求体过大档案：message 含请求体过大，结构化错误码为 payload_too_large。"""
    exc = handle_api_error(413, {})
    assert exc.status_code == 413
    assert _classify_generation_error_type(exc) == "payload_too_large"


def test_handle_api_error_413_user_hint_mentions_reduce_or_url() -> None:
    """413 错误的用户提示含减小尺寸或改用 URL 的可操作建议。"""
    exc = handle_api_error(413, {})
    result = format_error_for_user(exc)
    assert "减小" in result or "URL" in result
