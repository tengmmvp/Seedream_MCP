"""errors.py 敏感数据脱敏与值截断的契约测试。

覆盖 ``_filter_sensitive_data`` 的敏感字段归零、Bearer 令牌剥离与 list 分支，
以及 ``_truncate_value_for_output`` 的截断标记，确保上游错误体回显的鉴权信息
与大对象不会进入结构化输出或日志。
"""

from __future__ import annotations

from seedream_mcp.utils.errors import (
    SeedreamAPIError,
    SeedreamMCPError,
    _filter_sensitive_data,
    _redact_bearer_tokens,
    _redact_sensitive_message,
    _truncate_value_for_output,
    format_error_for_user,
)

# ==================== _filter_sensitive_data ====================


def test_filter_sensitive_data_redacts_sensitive_keys() -> None:
    """键名命中敏感关键词的值替换为 ***，普通键原样保留。"""
    data = {"authorization": "Bearer xxx", "api_key": "k", "normal": "v"}

    filtered = _filter_sensitive_data(data)

    assert filtered == {"authorization": "***", "api_key": "***", "normal": "v"}


def test_filter_sensitive_data_strips_bearer_in_non_sensitive_value() -> None:
    """非敏感键的字符串值中的 Bearer 令牌被剥离，保留 Bearer 前缀。"""
    filtered = _filter_sensitive_data({"header": "Authorization: Bearer abc123"})

    assert filtered == {"header": "Authorization: Bearer ***"}


def test_filter_sensitive_data_redacts_list_items() -> None:
    """list 分支逐项剥离 Bearer 令牌，非令牌字符串原样保留。"""
    filtered = _filter_sensitive_data(["Bearer abc", "plain"])

    assert filtered == ["Bearer ***", "plain"]


def test_filter_sensitive_data_recurses_nested_structures() -> None:
    """嵌套 dict 内的敏感字段与 Bearer 令牌均被处理。"""
    filtered = _filter_sensitive_data(
        {"outer": {"api_key": "secret", "note": "Bearer xyz"}, "count": 3}
    )

    assert filtered == {
        "outer": {"api_key": "***", "note": "Bearer ***"},
        "count": 3,
    }


def test_filter_sensitive_data_passes_through_scalars() -> None:
    """非 dict/list 的标量原样返回，不做脱敏。"""
    assert _filter_sensitive_data("Bearer abc") == "Bearer abc"
    assert _filter_sensitive_data(42) == 42


# ==================== _redact_bearer_tokens ====================


def test_redact_bearer_tokens_replaces_token() -> None:
    assert _redact_bearer_tokens("Bearer abc") == "Bearer ***"


def test_redact_bearer_tokens_is_case_insensitive() -> None:
    assert _redact_bearer_tokens("bearer ABC") == "bearer ***"


def test_redact_bearer_tokens_preserves_surrounding_text() -> None:
    assert _redact_bearer_tokens("auth: Bearer s3cret done") == "auth: Bearer *** done"


def test_redact_bearer_tokens_passes_through_non_strings() -> None:
    assert _redact_bearer_tokens(123) == 123
    assert _redact_bearer_tokens(None) is None


# ==================== _truncate_value_for_output ====================


def test_truncate_value_returns_none_unchanged() -> None:
    assert _truncate_value_for_output(None) is None


def test_truncate_value_returns_short_string_unchanged() -> None:
    assert _truncate_value_for_output("abc") == "abc"


def test_truncate_value_truncates_long_string_with_marker() -> None:
    long_value = "x" * 300

    truncated = _truncate_value_for_output(long_value, limit=200)

    assert truncated == "<truncated:300 chars> " + "x" * 200 + "..."


def test_truncate_value_summarizes_oversized_dict() -> None:
    oversized = {f"key{i}": "x" * 50 for i in range(10)}

    truncated = _truncate_value_for_output(oversized, limit=200)

    assert truncated == "<truncated:dict, 10 keys>"


def test_truncate_value_summarizes_oversized_list() -> None:
    oversized = ["x" * 50] * 10

    truncated = _truncate_value_for_output(oversized, limit=200)

    assert truncated == "<truncated:list, 10 items>"


def test_truncate_value_returns_small_container_unchanged() -> None:
    small = {"a": 1}

    assert _truncate_value_for_output(small) == small


# ==================== 连字符敏感键名（边界匹配） ====================


def test_filter_sensitive_data_redacts_hyphenated_sensitive_keys() -> None:
    """连字符键名 api-key、x-api-key 命中边界匹配，值被脱敏为 ***，不泄露原始凭据。"""
    data = {"api-key": "secret123", "x-api-key": "secret456"}

    filtered = _filter_sensitive_data(data)

    assert filtered == {"api-key": "***", "x-api-key": "***"}
    assert "secret123" not in str(filtered)
    assert "secret456" not in str(filtered)


# ==================== SeedreamAPIError message 的 Bearer 脱敏（集成） ====================


def test_api_error_to_dict_redacts_bearer_in_message() -> None:
    """SeedreamAPIError.to_dict 的 message 经 Bearer 脱敏，原始令牌不进入结构化输出。"""
    err = SeedreamAPIError(message="Invalid Bearer sk-secret-token-123")

    rendered_message = err.to_dict()["message"]

    assert "sk-secret-token-123" not in rendered_message
    assert "Bearer ***" in rendered_message


def test_format_error_for_user_redacts_bearer_in_api_error_message() -> None:
    """format_error_for_user 对 APIError 的 message 做 Bearer 脱敏，令牌不进入用户可见输出。"""
    err = SeedreamAPIError(message="Invalid Bearer sk-secret-token-123")

    rendered = format_error_for_user(err)

    assert "sk-secret-token-123" not in rendered
    assert "Bearer ***" in rendered


# ==================== details 字段脱敏（to_dict 一致性） ====================


def test_base_error_to_dict_filters_sensitive_details() -> None:
    """SeedreamMCPError.to_dict 的 details 经敏感字段过滤，与 response_data 一致。"""
    err = SeedreamMCPError(
        "msg", details={"api_key": "secret", "authorization": "Bearer x", "normal": "v"}
    )
    dumped = err.to_dict()

    assert dumped["details"] == {"api_key": "***", "authorization": "***", "normal": "v"}


# ==================== _redact_sensitive_message：敏感键值裸值剥离 ====================


def test_redact_sensitive_message_strips_apikey_keyvalue() -> None:
    """apikey=xxx 形态的裸值被剥离，保留键名与分隔符。"""
    assert _redact_sensitive_message("failed at apikey=sk-123") == "failed at apikey=***"


def test_redact_sensitive_message_strips_hyphenated_api_key() -> None:
    """api-key=xxx 连字符键名变体同样剥离。"""
    assert _redact_sensitive_message("api-key=secret") == "api-key=***"


def test_redact_sensitive_message_strips_authorization_basic_scheme() -> None:
    """Authorization: Basic xxx 形态剥离 scheme 与凭据，不泄露凭据。"""
    assert _redact_sensitive_message("echo Authorization: Basic abc123") == (
        "echo Authorization: ***"
    )


def test_redact_sensitive_message_preserves_plain_bearer() -> None:
    """无键名前缀的 Bearer 令牌仍由 Bearer 模式剥离，叠加覆盖。"""
    assert _redact_sensitive_message("Invalid Bearer sk-secret-token-123") == ("Invalid Bearer ***")
