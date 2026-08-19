"""错误分类、HTTP 错误归约、用户可见格式化与失败排查建议选择的测试。

覆盖 _classify_generation_error_type 的 8 个分支、handle_api_error 的状态码阶梯文案
与上游错误体提取、format_error_for_user 的 isinstance 分支与 message 截断、
_resolve_failure_guidance 的查表与流水线降级文案拼接。guidance 拼接语义：归约
档案携带 user_hint 时该建议即最终建议，档案无建议时才以查表值补充。
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core._helpers import (
    _FAILURE_GUIDANCE_BY_ERROR_CODE,
    _FAILURE_GUIDANCE_INTENTIONAL_DEFAULT_CODES,
    _resolve_failure_guidance,
)
from seedream_mcp.tools.core.common import (
    ToolMetadata,
    _classify_generation_error_type,
    execute_generation_handler,
)
from seedream_mcp.tools.core.schemas import TextToImageInput
from seedream_mcp.utils.core import errors as errors_module
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
    """上游 error.code 为数字或空串时 error_code 置 None，不臆测转字符串。"""
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
    """SeedreamMCPError 基类走通用「操作失败」分支。"""
    assert "操作失败" in format_error_for_user(SeedreamMCPError("something"))


def test_format_unknown_error() -> None:
    """非 SeedreamMCPError 异常归为「未知错误」。"""
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
    """413 映射到请求体过大档案，结构化错误码为 payload_too_large，message 含请求体过大。"""
    exc = handle_api_error(413, {})
    assert exc.status_code == 413
    assert _classify_generation_error_type(exc) == "payload_too_large"


def test_handle_api_error_413_user_hint_mentions_reduce_or_url() -> None:
    """413 错误的用户提示含减小尺寸或改用 URL 的可操作建议。"""
    exc = handle_api_error(413, {})
    result = format_error_for_user(exc)
    assert "减小" in result or "URL" in result


# ==================== user_hint 覆盖补齐 ====================


def test_format_validation_error_includes_range_hint() -> None:
    """参数验证失败追加检查参数取值范围的可操作建议。"""
    result = format_error_for_user(SeedreamValidationError("bad param"))
    assert "参数验证失败" in result
    assert "取值范围" in result


def test_handle_api_error_400_user_hint_mentions_check_params() -> None:
    """400 错误的用户提示含核对请求参数的建议。"""
    exc = handle_api_error(400, {})
    result = format_error_for_user(exc)
    assert "请核对请求参数" in result


def test_handle_api_error_404_user_hint_mentions_endpoint_config() -> None:
    """404 错误的用户提示含确认 API 端点配置的建议。"""
    exc = handle_api_error(404, {})
    result = format_error_for_user(exc)
    assert "请确认 API 端点配置" in result


def test_handle_api_error_5xx_user_hint_mentions_retry_later() -> None:
    """5xx 兜底档案追加服务端暂时不可用、稍后重试的建议。"""
    for status in (500, 503):
        exc = SeedreamAPIError("boom", status_code=status)
        result = format_error_for_user(exc)
        assert "服务端暂时不可用" in result
        assert "稍后重试" in result


# ==================== 失败排查建议按错误类型选择 ====================


def test_resolve_failure_guidance_validation_error_avoids_api_key() -> None:
    """参数类错误的排查建议引导调整参数，不出现 API Key 与网络指引。"""
    guidance = _resolve_failure_guidance(SeedreamValidationError("bad size"))
    assert guidance == "请根据错误信息调整对应参数取值。"
    assert "API Key" not in guidance


def test_resolve_failure_guidance_network_error_keeps_credential_hint() -> None:
    """网络类错误的排查建议保留凭据与网络指引。"""
    guidance = _resolve_failure_guidance(SeedreamNetworkError("conn refused"))
    assert "API Key" in guidance
    assert "网络" in guidance


def test_resolve_failure_guidance_timeout_and_auth_keep_credential_hint() -> None:
    """超时与认证类错误同样保留凭据与网络指引。"""
    assert "API Key" in _resolve_failure_guidance(SeedreamTimeoutError("t"))
    assert "API Key" in _resolve_failure_guidance(SeedreamAPIError("unauthorized", status_code=401))


def test_resolve_failure_guidance_unknown_code_falls_back_to_generic() -> None:
    """未列举错误码回退到通用排查建议。"""
    assert _resolve_failure_guidance(ValueError("x")) == "请根据错误信息排查后重试。"


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "请核对请求参数。"),
        (401, "请确认 API Key 和网络可用后重试。"),
        (402, "请检查账户余额与配额。"),
        (404, "请确认 API 端点配置。"),
        (429, "请稍后重试。"),
    ],
)
def test_resolve_failure_guidance_prefers_status_over_error_code(
    status: int, expected: str
) -> None:
    """携带状态码的 API 错误按状态级建议表取值，400/404 不落到凭据与网络指引。"""
    guidance = _resolve_failure_guidance(SeedreamAPIError("boom", status_code=status))
    assert guidance == expected
    if status in (400, 404):
        assert "API Key" not in guidance


def test_resolve_failure_guidance_api_error_without_status_falls_back_to_error_code() -> None:
    """无状态码的 API 错误按错误码兜底，api_error 走凭据与网络指引。"""
    guidance = _resolve_failure_guidance(SeedreamAPIError("unspecified failure"))
    assert guidance == "请确认 API Key 和网络可用后重试。"


def test_failure_guidance_table_covers_all_profile_error_codes() -> None:
    """归约档案的全部错误码均可经查表解析或显式登记为默认建议，双向锁定。

    档案错误码全集须与 _FAILURE_GUIDANCE_BY_ERROR_CODE 的键及默认登记集一致：
    新增档案未同步维护查表或查表残留废弃码均在此失败。
    """
    profile_codes = {profile.error_code for profile in errors_module._HTTP_STATUS_PROFILES.values()}
    profile_codes |= {profile.error_code for _, profile in errors_module._EXCEPTION_PROFILES}
    profile_codes |= {
        errors_module._HTTP_5XX_PROFILE.error_code,
        errors_module._HTTP_DEFAULT_PROFILE.error_code,
        errors_module._GENERIC_MCP_PROFILE.error_code,
        errors_module._UNKNOWN_PROFILE.error_code,
    }
    assert profile_codes == (
        set(_FAILURE_GUIDANCE_BY_ERROR_CODE) | set(_FAILURE_GUIDANCE_INTENTIONAL_DEFAULT_CODES)
    )


async def _run_failing_handler(exc: Exception):
    """以给定异常驱动 execute_generation_handler 的降级分支，返回结果文本。"""
    config = SeedreamConfig(api_key="test_key")

    async def failing_executor(client: Any, context: Any) -> dict[str, Any]:
        del client, context
        raise exc

    metadata = ToolMetadata(
        tool_name="text_to_image",
        completion_title="文生图任务完成",
        failure_prefix="文生图生成",
        start_log_message="",
        start_log_values_builder=lambda c: (),
    )
    result = await execute_generation_handler(
        params=TextToImageInput(prompt="test prompt", auto_save=False),
        config=config,
        module_logger=MagicMock(),
        metadata=metadata,
        request_executor=failing_executor,
    )
    assert result.is_error is True
    return result.content[0].text


async def test_handler_failure_text_validation_error_uses_profile_hint_only() -> None:
    """档案携带 user_hint 的参数类错误：建议只来自档案，不叠加查表排查行。"""
    text = await _run_failing_handler(SeedreamValidationError("尺寸超出允许范围"))

    assert "API Key" not in text
    assert "请检查对应参数的取值范围。" in text
    # user_hint 即最终建议，查表值不再拼接，避免双源叠加。
    assert "请根据错误信息调整对应参数取值。" not in text


async def test_handler_failure_text_network_error_uses_profile_hint_only() -> None:
    """档案携带 user_hint 的网络类错误：建议只来自档案，不叠加查表排查行。"""
    text = await _run_failing_handler(SeedreamNetworkError("connection refused"))

    assert "请检查网络连接。" in text
    assert "请确认 API Key 和网络可用后重试。" not in text


async def test_handler_failure_text_without_hint_appends_table_guidance() -> None:
    """档案无 user_hint 的错误（如未知异常）才追加查表排查建议行。"""
    text = await _run_failing_handler(ValueError("unexpected"))

    assert text.endswith("请根据错误信息排查后重试。")


async def test_handler_failure_text_429_retry_hint_appears_exactly_once() -> None:
    """429 降级文案中「请稍后重试」全文恰好出现一次，锁定 user_hint 与查表不叠加。"""
    text = await _run_failing_handler(SeedreamAPIError("rate limited", status_code=429))

    assert text.count("请稍后重试") == 1


async def test_handler_failure_text_400_mentions_params_without_api_key() -> None:
    """400 降级文案引导核对请求参数，不出现与参数错误矛盾的 API Key 指引。"""
    text = await _run_failing_handler(SeedreamAPIError("invalid size", status_code=400))

    assert "请核对请求参数。" in text
    assert "API Key" not in text
