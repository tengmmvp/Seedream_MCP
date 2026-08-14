"""errors.py 敏感数据脱敏与值截断的契约测试。

覆盖 ``_filter_sensitive_data`` 的敏感字段归零、Bearer 令牌剥离与 list 分支，
以及 ``_truncate_value_for_output`` 的截断标记，确保上游错误体回显的鉴权信息
与大对象不会进入结构化输出或日志。
"""

from __future__ import annotations

from seedream_mcp.utils.core.errors import (
    SeedreamAPIError,
    SeedreamMCPError,
    SeedreamValidationError,
    _filter_sensitive_data,
    _redact_bearer_tokens,
    _redact_sensitive_message,
    _truncate_value_for_output,
    format_error_for_user,
    sanitize_error_text,
)

# ==================== _filter_sensitive_data ====================


def test_filter_sensitive_data_redacts_sensitive_keys() -> None:
    """键名命中敏感关键词的值替换为 ***，普通键原样保留。"""
    data = {"authorization": "Bearer xxx", "api_key": "k", "normal": "v"}

    filtered = _filter_sensitive_data(data)

    assert filtered == {"authorization": "***", "api_key": "***", "normal": "v"}


def test_filter_sensitive_data_strips_credentials_in_non_sensitive_value() -> None:
    """非敏感键的字符串值中内嵌的鉴权信息被剥离，令牌不残留于结构化输出。

    authorization: 键名形态触发键值裸值剥离，吸收 scheme 词并替换值为 ***，与 message 层
    对 Authorization 的脱敏一致；纯 Bearer 令牌由 Bearer 模式保留前缀，见 list 测试。
    """
    filtered = _filter_sensitive_data({"header": "Authorization: Bearer abc123"})

    assert filtered == {"header": "Authorization: ***"}
    assert "abc123" not in str(filtered)


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


def test_redact_sensitive_message_strips_token_keyvalue() -> None:
    """token=xxx 形态的裸值被剥离，保留键名与分隔符。"""
    assert _redact_sensitive_message("failed at token=abc123") == "failed at token=***"


def test_redact_sensitive_message_strips_secret_keyvalue() -> None:
    """secret=xxx 形态的裸值被剥离，保留键名与分隔符。"""
    assert _redact_sensitive_message("secret=s3cr3t-value") == "secret=***"


def test_redact_sensitive_message_strips_compound_token_and_secret_variants() -> None:
    """access_token / client_secret / auth-token 等复合键名变体同样剥离。"""
    assert _redact_sensitive_message("access_token=eyJhbGciOi") == "access_token=***"
    assert _redact_sensitive_message("client_secret: hunter2") == "client_secret: ***"
    assert _redact_sensitive_message("auth-token: t0k") == "auth-token: ***"


def test_redact_sensitive_message_preserves_plain_words_without_separator() -> None:
    """无 : 或 = 分隔符的普通文本 token/secret 词形不受影响，避免误伤非敏感内容。"""
    assert _redact_sensitive_message("token count exceeded") == "token count exceeded"
    assert _redact_sensitive_message("the secret sauce") == "the secret sauce"
    assert _redact_sensitive_message("max_tokens: 4096 exceeded") == "max_tokens: 4096 exceeded"


# ==================== CRLF 日志注入剥离 ====================


def test_redact_sensitive_message_strips_crlf_injection() -> None:
    """CR/LF 控制字符被替换为空格，防止上游错误体在日志中伪造行注入误导记录。

    与 io_download.sanitize_url 的控制字符剥离对齐。
    """
    injected = "正常错误\r\nERROR fake-line\napikey=leaked"
    redacted = _redact_sensitive_message(injected)
    assert "\r" not in redacted
    assert "\n" not in redacted
    # 伪造的 ERROR 行不再独占一行，被压平为同一行内的空格分隔片段
    assert "fake-line" in redacted
    # 叠加键值裸值剥离：apikey=leaked 被脱敏
    assert "leaked" not in redacted


def test_redact_sensitive_message_strips_lone_cr_and_lf() -> None:
    """单独的 CR 或 LF 同样被剥离。"""
    assert _redact_sensitive_message("a\rb\nc") == "a b c"


def test_api_error_to_dict_strips_crlf_in_message() -> None:
    """SeedreamAPIError.to_dict 的 message 经 CRLF 剥离，换行不进入结构化输出。"""
    err = SeedreamAPIError(message="first\r\nsecond")
    rendered_message = err.to_dict()["message"]
    assert "\r" not in rendered_message
    assert "\n" not in rendered_message


def test_base_error_to_dict_strips_crlf_in_details() -> None:
    """details 内字符串值的 CRLF 被剥离，与 message 层防护对齐，防结构化输出日志注入。"""
    err = SeedreamMCPError(message="ok", details={"trace": "a\r\nFAKE\napikey=leaked"})
    dumped = err.to_dict()
    assert "\r" not in str(dumped["details"])
    assert "\n" not in str(dumped["details"])
    assert "leaked" not in str(dumped["details"])


def test_api_error_to_dict_strips_crlf_in_response_data() -> None:
    """response_data 内字符串值（含嵌套）的 CRLF 被剥离，与 message 层对齐。"""
    err = SeedreamAPIError(
        message="ok", response_data={"trace": "a\r\nFAKE", "nested": {"deep": "x\ny"}}
    )
    response_data = err.to_dict()["response_data"]
    assert "\r" not in str(response_data)
    assert "\n" not in str(response_data)


def test_validation_error_to_dict_strips_crlf_in_value() -> None:
    """SeedreamValidationError.to_dict 的 value 经 CRLF 剥离，换行不进入结构化输出。"""
    err = SeedreamValidationError(message="bad size", field="size", value="2048\r\nFAKE")
    dumped = err.to_dict()
    assert "\r" not in str(dumped["value"])
    assert "\n" not in str(dumped["value"])


# ==================== 换行分隔绕过与贪婪多词吸收 ====================


def test_redact_sensitive_message_blocks_newline_separator_bypass() -> None:
    """键与值以换行分隔的形态在控制字符压平后被键值模式命中，凭据不得借换行绕过脱敏。"""
    redacted = _redact_sensitive_message("api_key:\nSK-abcdef1234567890 leaked")
    assert "SK-abcdef1234567890" not in redacted
    assert "api_key:" in redacted

    redacted_auth = _redact_sensitive_message("Authorization:\r\nBasic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in redacted_auth


def test_redact_sensitive_message_absorbs_multiword_values() -> None:
    """多词凭据整体吸收至行尾，第三个及以后的词不再泄露。"""
    assert _redact_sensitive_message("api_key = secretA secretB secretC") == "api_key = ***"


def test_redact_sensitive_message_absorption_stops_at_next_keyvalue() -> None:
    """值吸收在下一个键值形态前停止，同文本中后续键值对仍被独立命中而非整体吞掉。

    键值后的普通尾词无法与凭据多词值区分，按 fail-safe 方向并入值一并脱敏。
    """
    assert _redact_sensitive_message("token=abc api_key=xyz tail") == "token=*** api_key=***"


def test_redact_sensitive_message_strips_password_and_cookie_keyvalues() -> None:
    """password/cookie 键值形态的裸值同样剥离，凭据不残留在用户可见输出。"""
    assert _redact_sensitive_message("password=hunter2topsecret") == "password=***"
    assert _redact_sensitive_message("Cookie: SESSIONID=xyz789") == "Cookie: ***"
    assert "xyz789" not in _redact_sensitive_message("Cookie: SESSIONID=xyz789")


# ==================== URL userinfo 剥离 ====================


def test_redact_sensitive_message_strips_url_userinfo() -> None:
    """错误文本中 URL 的 user:pass@ 凭据被剥离，scheme/host/path 保留。"""
    redacted = _redact_sensitive_message("failed https://user:pass@example.com/a.png")
    assert redacted == "failed https://example.com/a.png"


def test_redact_sensitive_message_strips_url_username_only() -> None:
    """仅用户名无密码的 userinfo 形态同样剥离。"""
    redacted = _redact_sensitive_message("see https://user@example.com/a.png")
    assert redacted == "see https://example.com/a.png"


def test_redact_sensitive_message_keeps_userinfo_free_url() -> None:
    """无凭据 URL 原样保留，不误伤正常链接文本。"""
    url = "https://example.com/a.png"
    assert _redact_sensitive_message(url) == url


def test_validation_error_to_dict_strips_url_userinfo_in_value() -> None:
    """userinfo URL 被拒后经 to_dict 回显时凭据剥离，主机与路径保留供纠错。"""
    err = SeedreamValidationError(
        message="URL 不允许携带用户名密码",
        field="image",
        value="https://AKID:SECRET@mirror.example.com/ref.png",
    )
    dumped = err.to_dict()
    rendered_value = str(dumped["value"])
    assert "AKID:SECRET@" not in rendered_value
    assert "mirror.example.com" in rendered_value


# ==================== 全角分隔符绕过与结果数据路径净化 ====================


def test_redact_sensitive_message_blocks_fullwidth_separator_bypass() -> None:
    """全角冒号/等号分隔的键值形态同样剥离，封堵非 ASCII 分隔符绕过。"""
    assert _redact_sensitive_message("api_key：abc123 leaked") == "api_key：***"
    assert _redact_sensitive_message("token＝secret") == "token＝***"


def test_sanitize_error_text_passes_through_non_strings() -> None:
    """sanitize_error_text 对非字符串原样返回，供结构化字段安全复用。"""
    assert sanitize_error_text(42) == 42
    assert sanitize_error_text(None) is None


def test_sanitize_error_text_redacts_and_truncates() -> None:
    """sanitize_error_text 先脱敏后截断，长文本中的凭据不因截断位置而残留。"""
    redacted = sanitize_error_text("x" * 600 + "\napi_key: leaked")
    assert "leaked" not in redacted
    assert len(redacted) < 700


def test_format_sse_failed_event_sanitizes_error_message() -> None:
    """SSE 失败事件的 error.message 经统一脱敏，被劫持中间层回显的凭据不进图片项。"""
    from seedream_mcp.utils.io.io_sse import format_sse_failed_event

    event = {
        "error": {"code": "E", "message": "upstream echo Authorization: Bearer sk-123"},
        "image_index": 0,
    }

    item = format_sse_failed_event(event, "model-x")

    assert "sk-123" not in str(item)
    assert "***" in item["error"]["message"]


def test_sanitize_image_errors_redacts_per_image_error_message() -> None:
    """非 SSE 路径的 per-image error.message 净化后进结构化输出，原结果对象不被修改。"""
    from seedream_mcp.tools.core.results import _sanitize_image_errors

    images = [
        {"url": "https://a/1.png"},
        {"error": {"code": "X", "message": "api_key: sk-leaked"}},
    ]

    sanitized = _sanitize_image_errors(images)

    assert "sk-leaked" not in str(sanitized[1])
    assert "***" in sanitized[1]["error"]["message"]
    assert sanitized[0] is images[0]
    assert images[1]["error"]["message"] == "api_key: sk-leaked"
