"""errors.py 敏感数据脱敏与值截断的契约测试。

覆盖 ``_filter_sensitive_data`` 的敏感字段归零、Bearer 令牌剥离与 list 分支，
以及 ``_truncate_value_for_output`` 的截断标记，确保上游错误体回显的鉴权信息
与大对象不会进入结构化输出或日志。
"""

from __future__ import annotations

from seedream_mcp.utils.errors import (
    _filter_sensitive_data,
    _redact_bearer_tokens,
    _truncate_value_for_output,
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
