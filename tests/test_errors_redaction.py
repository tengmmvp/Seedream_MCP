"""errors.py 敏感数据脱敏与值截断的契约测试。

覆盖 ``_filter_sensitive_data`` 的敏感字段归零、Bearer 令牌剥离与 list 分支，
以及 ``_truncate_value_for_output`` 的截断标记，确保上游错误体回显的鉴权信息
与大对象不会进入结构化输出或日志。
"""

from __future__ import annotations

import time
from typing import Any

from seedream_mcp.utils.core.errors import (
    SeedreamAPIError,
    SeedreamMCPError,
    SeedreamValidationError,
    _filter_sensitive_data,
    _redact_sensitive_message,
    _sanitize_output_string,
    _truncate_value_for_output,
    format_error_for_user,
    handle_api_error,
    sanitize_data_text,
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


# ==================== Bearer 令牌剥离管线（_sanitize_output_string） ====================


def test_bearer_pipeline_replaces_token() -> None:
    """实际脱敏管线上 Bearer 令牌被替换为 ***，前缀保留以维持语义。"""
    assert _sanitize_output_string("Bearer abc") == "Bearer ***"


def test_bearer_pipeline_is_case_insensitive() -> None:
    assert _sanitize_output_string("bearer ABC") == "bearer ***"


def test_bearer_pipeline_preserves_surrounding_text() -> None:
    assert _sanitize_output_string("auth: Bearer s3cret done") == "auth: Bearer *** done"


def test_bearer_pipeline_passes_through_non_strings() -> None:
    assert _sanitize_output_string(123) == 123
    assert _sanitize_output_string(None) is None


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


def test_redact_sensitive_message_preserves_adjacent_non_sensitive_keyvalue() -> None:
    """值吸收在任意键名形态前停止：多键 JSON 只脱敏敏感键，相邻非敏感键值对保留。

    停止前瞻识别可选引号加任意键名加分隔符的结构，不限于敏感词；非敏感键不再被
    前一个敏感值的贪婪吸收整体吞掉，JSON 回显的括号得以配平。
    """
    redacted = _redact_sensitive_message('{"api_key": "SK-1", "note": "keep me"}')
    assert "SK-1" not in redacted
    assert '"note": "keep me"' in redacted
    assert redacted.endswith("}")

    single_quoted = _redact_sensitive_message("{'api_key': 'a1', 'note': 'n1'}")
    assert "a1" not in single_quoted
    assert "'note': 'n1'" in single_quoted
    assert single_quoted.endswith("}")

    # 裸键值形态同样在非敏感键名前停止吸收，trace_id 保留原文
    assert _redact_sensitive_message("token=abc trace_id=xyz") == "token=*** trace_id=xyz"


# ==================== 分隔符正则性能守护 ====================


def test_redact_sensitive_message_long_space_run_stays_fast() -> None:
    """性能守护：长空格串输入的脱敏在时限内完成，防止分隔符二次方回溯回归。

    分隔符中允许同一段空格被两个量词分别吸收时，键名后长空格串的失败匹配呈
    二次方回溯，20010 字符输入实测秒级；切分路径唯一化后同输入毫秒级完成。
    """
    hostile = "token" + " " * 20_000 + "value"

    start = time.perf_counter()
    redacted = _redact_sensitive_message(hostile)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1
    # 无分隔符不命中键值模式，原文保留
    assert redacted == hostile

    # 带分隔符变体经 sanitize_error_text 入口：先截断约束正则工作长度，同为毫秒级
    start = time.perf_counter()
    sanitize_error_text("token=" + " " * 20_000 + "value")
    pipeline_elapsed = time.perf_counter() - start

    assert pipeline_elapsed < 0.1


def test_redact_sensitive_message_unicode_whitespace_run_stays_fast() -> None:
    """性能守护：Unicode 空白长串与引号空白组合的脱敏保持线性，防止扩展字符类后的回溯回归。"""
    nbsp_run = "token" + chr(0xA0) * 20_000 + "value"
    start = time.perf_counter()
    redacted = _redact_sensitive_message(nbsp_run)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1
    # 无分隔符不命中键值模式，原文保留
    assert redacted == nbsp_run

    quoted_nbsp = "token '" + chr(0xA0) * 20_000 + "' value"
    start = time.perf_counter()
    _redact_sensitive_message(quoted_nbsp)
    assert time.perf_counter() - start < 0.1


def test_redact_sensitive_message_underscore_chain_stays_fast() -> None:
    """性能守护：键名续段星号的失败回溯保持线性，连字符长链不得触发指数回溯。

    续段字符类若允许跨分隔符字符，嵌套量词的切分歧义会使 session_a_a 一类输入的
    失败回溯指数级膨胀，200 段即超分钟级；边界唯一化后 2 万段仍为毫秒级。
    """
    hostile = "session" + "_a" * 20_000

    start = time.perf_counter()
    redacted = _redact_sensitive_message(hostile)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5
    # 无分隔符不命中键值模式，原文保留
    assert redacted == hostile


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
    """sanitize_error_text 先截断后脱敏：丢弃段凭据随截断消失，保留段凭据被剥离。"""
    # 凭据位于截断点之后：随丢弃段一起消失
    tail_beyond_limit = sanitize_error_text("x" * 600 + "\napi_key: leaked")
    assert "leaked" not in tail_beyond_limit
    assert len(tail_beyond_limit) < 700

    # 凭据位于保留段内：截断后仍被脱敏剥离
    kept_prefix = sanitize_error_text("api_key: leaked " + "x" * 600)
    assert "leaked" not in kept_prefix
    assert "api_key: ***" in kept_prefix


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
    """非 SSE 路径的 per-image error.message 净化并写回列表条目，原字典对象不被修改。"""
    from seedream_mcp.tools.core.results import _sanitize_image_errors

    images = [
        {"url": "https://a/1.png"},
        {"error": {"code": "X", "message": "api_key: sk-leaked"}},
    ]
    # 净化以浅拷贝写回列表位置，须持原字典对象的直接引用才能断言其未被就地修改。
    original_dirty_item = images[1]

    sanitized = _sanitize_image_errors(images)

    assert "sk-leaked" not in str(sanitized[1])
    assert "***" in sanitized[1]["error"]["message"]
    assert sanitized is images
    assert sanitized[0] is images[0]
    assert original_dirty_item["error"]["message"] == "api_key: sk-leaked"


# ==================== 数据字段净化：不截断（sanitize_data_text） ====================


def test_sanitize_data_text_preserves_long_url_without_truncation() -> None:
    """约 674 字符的签名 URL 原样保留：数据字段不做错误文本的 500 字符截断。"""
    signed_url = "https://tos.example.com/obj/a.png?X-Tos-Signature=" + "s" * 620
    assert len(signed_url) > 500

    assert sanitize_data_text(signed_url) == signed_url


def test_sanitize_data_text_strips_credentials_without_truncation() -> None:
    """超长 URL 的 userinfo 凭据剥离仍生效，剥离后的 URL 完整保留。"""
    long_url = "https://AKID:" + "p" * 600 + "@mirror.example.com/a.png?sig=abc"

    redacted = sanitize_data_text(long_url)

    assert redacted == "https://mirror.example.com/a.png?sig=abc"
    assert "truncated" not in redacted


def test_sanitize_data_text_keeps_full_sanitization_modes() -> None:
    """数据字段净化保留全套脱敏：CRLF 压平、敏感键值剥离。"""
    redacted = sanitize_data_text("https://example.com/a.png\r\napi_key=leaked")

    assert "\r" not in redacted
    assert "\n" not in redacted
    assert "leaked" not in redacted


def test_sanitize_data_text_preserves_url_query_params() -> None:
    """纯 URL 数据字段不应用键值脱敏：查询参数是 URL 组成而非凭据回显。

    token=/Secret= 等查询参数名触发键值剥离会把签名 URL 的查询串整体替换为 ***，
    数据字段随之不可用；大小写 scheme 前缀同样按纯 URL 处理。
    """
    url = "https://example.com/a.png?token=abc&x-expires=99&Secret=zzz&api_key=k1"

    assert sanitize_data_text(url) == url
    upper = "HTTP://example.com/a.png?token=abc"
    assert sanitize_data_text(upper) == upper


def test_sanitize_data_text_url_light_path_still_strips_userinfo() -> None:
    """纯 URL 轻量路径仍剥离 userinfo 凭据，查询参数豁免不削弱凭据防护。"""
    redacted = sanitize_data_text("https://AKID:SECRET@mirror.example.com/a.png?token=abc")

    assert redacted == "https://mirror.example.com/a.png?token=abc"
    assert "AKID" not in redacted
    assert "SECRET" not in redacted


def test_sanitize_data_text_strips_padding_before_url_judgment() -> None:
    """纯 URL 判定先 strip 首尾空白：带空白前缀的签名 URL 走轻量路径，查询串不被键值脱敏破坏。"""
    url = "https://example.com/a.png?token=abc&Signature=xyz"

    assert sanitize_data_text("  " + url) == url
    assert sanitize_data_text(url + "  ") == url
    assert sanitize_data_text(f"\t{url}\n") == url


def test_sanitize_data_text_url_prefixed_mixed_text_keeps_keyvalue_redaction() -> None:
    """URL 前缀但含空白或控制字符的混合文本仍走全套脱敏，凭据不借 URL 形态逃逸。"""
    with_crlf = sanitize_data_text("https://example.com/a.png\r\napi_key=leaked")
    assert "leaked" not in with_crlf

    with_space = sanitize_data_text("https://example.com/a.png api_key=leaked tail")
    assert "leaked" not in with_space


def test_sanitize_data_text_non_url_text_keeps_keyvalue_redaction() -> None:
    """非 URL 文本的键值脱敏不受 URL 豁免影响，非敏感键值对同样保留。"""
    redacted = sanitize_data_text("api_key=leaked note=keep")

    assert redacted == "api_key=*** note=keep"


def test_sanitize_data_text_enforces_defensive_limit() -> None:
    """16KB 防御上限仍生效：异常超长数据不撑爆输出。"""
    huge = "x" * (16 * 1024 + 100)

    redacted = sanitize_data_text(huge)

    assert "truncated" in redacted
    assert len(redacted) < len(huge)


def test_sanitize_data_text_passes_through_non_strings() -> None:
    """与 sanitize_error_text 一致，非字符串原样返回。"""
    assert sanitize_data_text(42) == 42
    assert sanitize_data_text(None) is None


# ==================== 非字符串 message 归一化 ====================


def test_api_error_dict_message_sanitized_in_to_dict() -> None:
    """dict 形态 message 归一化为 JSON 后脱敏，api_key 键值凭据不残留。"""
    err = SeedreamAPIError(message={"api_key": "SK-SECRET", "note": "x"})  # type: ignore[arg-type]

    rendered = str(err.to_dict()["message"])

    assert "SK-SECRET" not in rendered
    assert "***" in rendered


def test_api_error_dict_message_sanitized_for_user() -> None:
    """format_error_for_user 同样覆盖 dict 形态 message。"""
    err = SeedreamAPIError(message={"api_key": "SK-SECRET"})  # type: ignore[arg-type]

    rendered = format_error_for_user(err)

    assert "SK-SECRET" not in rendered
    assert "***" in rendered


def test_handle_api_error_normalizes_dict_upstream_message() -> None:
    """上游响应体的 dict 形态 message 拼接前归一化，输出通道凭据不残留。"""
    exc = handle_api_error(400, {"error": {"message": {"api_key": "SK-SECRET"}}})

    rendered = str(exc.to_dict()["message"])
    assert "SK-SECRET" not in rendered
    assert "***" in rendered
    user_rendered = format_error_for_user(exc)
    assert "SK-SECRET" not in user_rendered


def test_handle_api_error_normalizes_list_upstream_message() -> None:
    """list 形态 message 同样归一化，str() 兜底后进入脱敏管线。"""
    exc = handle_api_error(400, {"message": ["api_key=SK-SECRET"]})

    rendered = str(exc.to_dict()["message"])
    assert "SK-SECRET" not in rendered


def test_api_error_deeply_nested_message_does_not_raise_recursion_error() -> None:
    """十万层嵌套 list 形态 message 归一化不外逃 RecursionError，降级为占位文本。

    json.dumps 与 str() 对超深嵌套结构先后触发解释器递归上限，归一化逐级回退到
    类型占位符，to_dict 与用户可见输出通道均不受影响。
    """
    deep: Any = []
    for _ in range(100_000):
        deep = [deep]

    err = SeedreamAPIError(message=deep)  # type: ignore[arg-type]

    rendered = str(err.to_dict()["message"])
    assert isinstance(rendered, str)
    assert rendered != ""
    assert format_error_for_user(err)


# ==================== message 与 details 的 truncate-first 次序 ====================


def test_to_dict_message_truncates_before_redaction() -> None:
    """to_dict 的 message 先截断后脱敏：截断丢弃段凭据随截断消失，保留段凭据被剥离，输出长度受上限约束。"""
    boundary_split = SeedreamAPIError(message="a" * 495 + "api_key=" + "SECRET" * 200)
    rendered = str(boundary_split.to_dict()["message"])
    assert "SECRET" not in rendered
    assert len(rendered) < 700

    kept_prefix = SeedreamAPIError(message="api_key=leaked " + "x" * 600)
    rendered_kept = str(kept_prefix.to_dict()["message"])
    assert "leaked" not in rendered_kept
    assert "api_key=***" in rendered_kept


def test_format_error_for_user_truncates_before_redaction() -> None:
    """format_error_for_user 与 to_dict 同次序：先截断约束正则工作长度，再剥离保留段凭据。"""
    err = SeedreamAPIError(message="a" * 495 + "api_key=" + "SECRET" * 200)
    rendered = format_error_for_user(err)
    assert "SECRET" not in rendered


# ==================== details 深嵌套迭代防护与截断对齐 ====================


def test_to_dict_deeply_nested_details_do_not_raise_recursion_error() -> None:
    """十万层嵌套 dict/list 形态 details 不外逃 RecursionError。

    _filter_sensitive_data 以显式栈替代递归，深嵌套结构的敏感字段过滤不触发
    解释器递归上限，与 _normalize_non_str_message 对超深 message 的兜底口径对齐；
    to_dict 的 truncate-first 管线中 repr 触发的递归失败由 _truncate_value_for_output
    兜底为类型占位符，details 收敛为有界文本。
    """
    deep_dict: Any = {}
    for _ in range(100_000):
        deep_dict = {"a": deep_dict}

    filtered = _filter_sensitive_data(deep_dict)
    assert isinstance(filtered, dict)

    err_dict = SeedreamMCPError("msg", details=deep_dict)
    dumped = err_dict.to_dict()
    assert dumped["details"] == "<dict>"

    deep_list: Any = []
    for _ in range(100_000):
        deep_list = [deep_list]
    assert isinstance(_filter_sensitive_data(deep_list), list)
    err_list = SeedreamMCPError("msg", details=deep_list)
    assert err_list.to_dict()["details"] == "<list>"


def test_to_dict_details_truncated_like_response_data() -> None:
    """details 与 response_data 截断口径对齐：超大容器收敛为元素数摘要，不撑爆结构化输出。"""
    oversized = {f"field{i}": "x" * 50 for i in range(60)}
    err = SeedreamMCPError("msg", details=oversized)

    dumped = err.to_dict()

    assert dumped["details"] == "<truncated:dict, 60 keys>"


def test_redact_sensitive_message_blocks_quoted_keyvalue_forms() -> None:
    """JSON/Python repr 的引号键值形态（键名后紧跟引号再接冒号）同样命中剥离。"""
    assert "xxx" not in _redact_sensitive_message("{'api_key': 'xxx'}")
    assert "xxx" not in _redact_sensitive_message('{"api_key": "xxx"}')
    # 值吸收为贪婪多词，收尾引号与花括号并入脱敏值一并消隐，方向 fail-safe。
    assert _redact_sensitive_message('{"api_key": "xxx"}') == '{"api_key": ***'


def test_redact_sensitive_message_quote_variant_no_overmatch() -> None:
    """引号变体不误伤普通文本：无分隔符的引号词形与既有保留样例不受影响。"""
    assert _redact_sensitive_message('he mentioned "token" in prose') == (
        'he mentioned "token" in prose'
    )
    assert _redact_sensitive_message('the "secret" ingredient') == 'the "secret" ingredient'
    assert _redact_sensitive_message("the token count is fine") == "the token count is fine"


# ==================== Unicode 空白与控制字符分隔绕过（回归） ====================


def test_redact_sensitive_message_blocks_control_char_separator_bypass() -> None:
    """垂直制表符、换页符与 NEL 分隔的键值在控制字符压平后被键值模式命中。"""
    assert _redact_sensitive_message("api_key:\x0bSECRET123") == "api_key: ***"
    assert _redact_sensitive_message("api_key:\x0cSECRET123") == "api_key: ***"
    assert _redact_sensitive_message("api_key:\x85SECRET123") == "api_key: ***"
    assert "SECRET123" not in _redact_sensitive_message("token\x0b=SECRET123")


def test_redact_sensitive_message_blocks_unicode_whitespace_separator_bypass() -> None:
    """NBSP、全角空格与 em 空白分隔的键值形态同样剥离，凭据不借 Unicode 空白逃逸。"""
    nbsp = chr(0xA0)
    ideographic = chr(0x3000)
    em_space = chr(0x2003)
    assert _redact_sensitive_message("password" + nbsp + "=" + nbsp + "SECRET") == (
        "password" + nbsp + "=" + nbsp + "***"
    )
    assert _redact_sensitive_message("token" + ideographic + ":" + ideographic + "SECRET") == (
        "token" + ideographic + ":" + ideographic + "***"
    )
    assert _redact_sensitive_message("api_key" + em_space + "=" + em_space + "SECRET") == (
        "api_key" + em_space + "=" + em_space + "***"
    )


def test_redact_sensitive_message_blocks_quote_space_quote_separator() -> None:
    """「引号-空白-引号」形态的分隔符组合同样命中，secret 不残留。"""
    redacted = _redact_sensitive_message("api_key '' : secret123")
    assert "secret123" not in redacted
    assert redacted == "api_key '' : ***"


def test_control_chars_pattern_flattens_c0_del_and_nel() -> None:
    """控制字符类统一覆盖 C0、DEL 与 NEL，errors 与 logs 两模块共用同一常量。"""
    from seedream_mcp.utils.core import errors as errors_module
    from seedream_mcp.utils.core import logs as logs_module

    assert errors_module.CONTROL_CHARS_PATTERN is logs_module._LOG_MESSAGE_CONTROL_CHARS
    for ch in ("\x00", "\x08", "\x0b", "\x0c", "\r", "\n", "\x1f", "\x7f", "\x85"):
        assert errors_module.CONTROL_CHARS_PATTERN.sub(" ", f"a{ch}b") == "a b"
    # 可打印字符不受影响
    assert errors_module.CONTROL_CHARS_PATTERN.sub(" ", "a b中") == "a b中"


# ==================== 自由文本键名与 dict 键策略单一来源 ====================


def test_redact_sensitive_message_strips_session_jwt_privatekey_keyvalues() -> None:
    """session/jwt/privatekey 等自由文本键名与 dict 键策略同覆盖，裸值剥离。"""
    assert _redact_sensitive_message("session_id=abc123") == "session_id=***"
    assert _redact_sensitive_message("session-id=abc123") == "session-id=***"
    assert _redact_sensitive_message("jwt=eyJhbGciOiJIUzI1NiJ9") == "jwt=***"
    assert _redact_sensitive_message("privatekey=SECRET") == "privatekey=***"
    assert _redact_sensitive_message("sshkey=SECRET") == "sshkey=***"
    assert _redact_sensitive_message("signature=hmac-value") == "signature=***"
    assert _redact_sensitive_message("nonce=42") == "nonce=***"
    assert _redact_sensitive_message("saml_assertion=payload") == "saml_assertion=***"


def test_is_sensitive_key_matches_privatekey_and_sshkey() -> None:
    """无分隔复合词 privatekey/sshkey 纳入高确信子串清单，与 apikey 策略统一。"""
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    assert _is_sensitive_key("privatekey") is True
    assert _is_sensitive_key("sshkey") is True
    assert _is_sensitive_key("my-privatekey") is True
    assert _is_sensitive_key("x_sshkey") is True
    # 边界匹配的防误伤语义保留：monkey、keyboard 不因含 key 字面命中
    assert _is_sensitive_key("monkey") is False
    assert _is_sensitive_key("keyboard") is False


def test_filter_sensitive_data_redacts_privatekey_dict_key() -> None:
    """dict 键路径对 privatekey/sshkey 同样脱敏，两条通道策略一致。"""
    filtered = _filter_sensitive_data({"privatekey": "SECRET", "sshkey": "SECRET", "normal": "v"})
    assert filtered == {"privatekey": "***", "sshkey": "***", "normal": "v"}


# ==================== camelCase 敏感键双路径覆盖 ====================


def test_redact_sensitive_message_strips_camelcase_sensitive_keyvalues() -> None:
    """camelCase 敏感键 secretKey/accessKey/sessionKey/authKey 的裸值剥离，凭据不借驼峰命名逃逸。"""
    assert _redact_sensitive_message("secretKey=AKIAIOSFODNN7EXAMPLE") == "secretKey=***"
    assert _redact_sensitive_message("accessKey: AKIAIOSFODNN7EXAMPLE") == "accessKey: ***"
    assert _redact_sensitive_message("sessionKey=abc123def456") == "sessionKey=***"
    assert _redact_sensitive_message("authKey=xyz789") == "authKey=***"


def test_is_sensitive_key_matches_camelcase_sensitive_compounds() -> None:
    """camelCase 复合键归一化小写后命中高确信子串清单，dict 键路径与自由文本同覆盖。"""
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    assert _is_sensitive_key("secretKey") is True
    assert _is_sensitive_key("accessKey") is True
    assert _is_sensitive_key("sessionKey") is True
    assert _is_sensitive_key("authKey") is True
    assert _is_sensitive_key("my-accessKey") is True
    # 对照组：普通词键不因含 key/auth 字面命中
    assert _is_sensitive_key("monkey") is False
    assert _is_sensitive_key("keyboard") is False
    assert _is_sensitive_key("author") is False


def test_filter_sensitive_data_redacts_camelcase_sensitive_dict_keys() -> None:
    """dict 键路径对 camelCase 敏感键同样脱敏为 ***，两条通道策略一致。"""
    filtered = _filter_sensitive_data(
        {
            "secretKey": "AKIA1",
            "accessKey": "AKIA2",
            "sessionKey": "s1",
            "authKey": "a1",
            "note": "v",
        }
    )
    assert filtered == {
        "secretKey": "***",
        "accessKey": "***",
        "sessionKey": "***",
        "authKey": "***",
        "note": "v",
    }


# ==================== 空格复合词 API Key ====================


def test_redact_sensitive_message_strips_space_separated_api_key() -> None:
    """空格复合词 "API Key: <凭据>" 同样命中键值脱敏，分隔符前不允许空格的绕过封堵。"""
    assert _redact_sensitive_message("API Key: AKIAIOSFODNN7EXAMPLE") == "API Key: ***"
    assert _redact_sensitive_message("api key = secret123") == "api key = ***"
    # 对照组：连字符与下划线变体仍命中
    assert _redact_sensitive_message("X-Api-Key: k1") == "X-Api-Key: ***"
    assert _redact_sensitive_message("api_key=k2") == "api_key=***"


def test_redact_sensitive_message_api_key_space_form_no_overmatch() -> None:
    """普通含 api/key 词句不误吞：无分隔符与值的词组保持原文。"""
    assert _redact_sensitive_message("api key is missing") == "api key is missing"
    assert _redact_sensitive_message("please rotate the api key regularly") == (
        "please rotate the api key regularly"
    )
    # key 后紧跟普通字母的复合词不命中，仅 "key" 前缀不足以触发
    assert _redact_sensitive_message("my api keyboard mapping") == "my api keyboard mapping"


def test_is_sensitive_key_matches_space_separated_compound() -> None:
    """空格作为键名边界分隔符与自由文本复合分支同规则："api key" 键名命中。"""
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    assert _is_sensitive_key("api key") is True
    assert _is_sensitive_key("my api key") is True
    # 对照组：X-Api-Key 仍命中，普通复合词不误吞
    assert _is_sensitive_key("X-Api-Key") is True
    assert _is_sensitive_key("rapid api keyboard") is False
    assert _is_sensitive_key("monkey") is False


def test_filter_sensitive_data_redacts_space_separated_api_key_dict_key() -> None:
    """dict 键 "api key" 的值被脱敏，与自由文本空格复合分支策略一致。"""
    filtered = _filter_sensitive_data({"api key": "AKIA1", "note": "v"})
    assert filtered == {"api key": "***", "note": "v"}


def test_keyvalue_key_branches_derive_from_keyword_lists() -> None:
    """自由文本键名交替组由两清单派生，新增敏感词不会遗漏同步到自由文本通道。"""
    from seedream_mcp.utils.core.errors import (
        _SENSITIVE_KEY_KEYWORDS,
        _SENSITIVE_KEY_SUBSTRINGS,
        _SENSITIVE_KEYVALUE_KEYS,
    )

    ambiguous = {"key", "auth"}
    for word in (*_SENSITIVE_KEY_SUBSTRINGS, *_SENSITIVE_KEY_KEYWORDS):
        if word in ambiguous:
            continue
        assert word in _SENSITIVE_KEYVALUE_KEYS


def test_redact_sensitive_message_preserves_suffix_word_forms() -> None:
    """后缀形态的普通词不受派生分支影响：续段要求以分隔符开头，max_tokens 保留。"""
    assert _redact_sensitive_message("max_tokens: 4096 exceeded") == "max_tokens: 4096 exceeded"
    assert _redact_sensitive_message("token count exceeded") == "token count exceeded"


# ==================== _sanitize_response_data 次序与契约 ====================


def test_sanitize_response_data_summarizes_oversized_container() -> None:
    """超大容器先截断收敛为元素数摘要，脱敏正则不遍历其值，凭据不进入输出。"""
    from seedream_mcp.utils.core.errors import _sanitize_response_data

    big = {f"field{i}": "api_key=SECRET" for i in range(60)}
    result = _sanitize_response_data(big)

    assert result == "<truncated:dict, 60 keys>"
    assert "SECRET" not in str(result)


def test_sanitize_response_data_redacts_values_of_small_container() -> None:
    """小容器保留结构，字符串值中的敏感键值裸值仍被剥离。"""
    from seedream_mcp.utils.core.errors import _sanitize_response_data

    redacted = _sanitize_response_data({"note": "api_key=SECRET", "count": 3})

    assert "SECRET" not in str(redacted)
    assert redacted["note"].startswith("api_key=***")
    assert redacted["count"] == 3
