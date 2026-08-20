"""errors.py 敏感数据脱敏与值截断的契约测试。

覆盖 sanitize_error_text/sanitize_data_text 的自由文本脱敏、_sanitize_output_string
的 Bearer 与 URL userinfo 剥离、_truncate_value_for_output 的截断，以及
format_error_for_user 与 handle_api_error 的用户可见出口净化，确保上游错误体回显
的鉴权信息与大对象不进入用户可见输出。
"""

from __future__ import annotations

import time
import tracemalloc
from typing import Any

from seedream_mcp.utils.core.errors import (
    CONTROL_CHARS_PATTERN,
    SeedreamAPIError,
    _sanitize_output_string,
    _truncate_value_for_output,
    format_error_for_user,
    handle_api_error,
    sanitize_data_text,
    sanitize_error_text,
)

# ==================== Bearer 令牌剥离管线：_sanitize_output_string ====================


def test_bearer_pipeline_replaces_token() -> None:
    """实际脱敏管线上 Bearer 令牌被替换为 ***，前缀保留以维持语义。"""
    assert _sanitize_output_string("Bearer abc") == "Bearer ***"


def test_bearer_pipeline_is_case_insensitive() -> None:
    """Bearer 前缀大小写不敏感，小写形态同样命中剥离。"""
    assert _sanitize_output_string("bearer ABC") == "bearer ***"


def test_bearer_pipeline_preserves_surrounding_text() -> None:
    """非键名前缀的 Bearer 令牌保留前后文本；auth 键名形态优先走键值脱敏整体消隐。"""
    assert _sanitize_output_string("header: Bearer s3cret done") == "header: Bearer *** done"
    assert _sanitize_output_string("auth: Bearer s3cret done") == "auth: ***"


def test_bearer_pipeline_passes_through_non_strings() -> None:
    """非字符串输入原样返回。"""
    assert _sanitize_output_string(123) == 123
    assert _sanitize_output_string(None) is None


# ==================== _truncate_value_for_output ====================


def test_truncate_value_returns_none_unchanged() -> None:
    """None 原样返回。"""
    assert _truncate_value_for_output(None) is None


def test_truncate_value_returns_short_string_unchanged() -> None:
    """限长内的短字符串原样返回。"""
    assert _truncate_value_for_output("abc") == "abc"


def test_truncate_value_truncates_long_string_with_marker() -> None:
    """超长字符串截断并附截断标记。"""
    long_value = "x" * 300

    truncated = _truncate_value_for_output(long_value, limit=200)

    assert truncated == "<truncated:300 chars> " + "x" * 200 + "..."


def test_truncate_value_summarizes_oversized_dict() -> None:
    """超限 dict 以键数摘要替代内容。"""
    oversized = {f"key{i}": "x" * 50 for i in range(10)}

    truncated = _truncate_value_for_output(oversized, limit=200)

    assert truncated == "<truncated:dict, 10 keys>"


def test_truncate_value_summarizes_oversized_list() -> None:
    """超限 list 以元素数摘要替代内容。"""
    oversized = ["x" * 50] * 10

    truncated = _truncate_value_for_output(oversized, limit=200)

    assert truncated == "<truncated:list, 10 items>"


def test_truncate_value_returns_small_container_unchanged() -> None:
    """限长内的小容器原样返回。"""
    small = {"a": 1}

    assert _truncate_value_for_output(small) == small


def test_truncate_value_estimates_large_string_container_without_repr() -> None:
    """大字符串元素的小容器判长不物化整份 repr，tracemalloc 峰值远低于物化形态。

    3 元素各 4MB 的 dict 完整 repr 约 12MB，估计路径按元素 len 判长，峰值以 2MB
    为上界锁定。
    """
    big = {f"field{i}": "x" * (4 * 1024 * 1024) for i in range(3)}

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        truncated = _truncate_value_for_output(big)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert truncated == "<truncated:dict, 3 keys>"
    assert peak < 2 * 1024 * 1024


def test_truncate_value_summarizes_huge_nested_container_quickly() -> None:
    """小外层容器内嵌超大子容器的判长在超限处提前终止，快速返回元素数摘要。"""
    big = {"a": ["x"] * 3_000_000}

    start = time.perf_counter()
    truncated = _truncate_value_for_output(big)
    elapsed = time.perf_counter() - start

    assert truncated == "<truncated:dict, 1 keys>"
    assert elapsed < 0.5


# ==================== sanitize_error_text：敏感键值裸值剥离 ====================


def test_sanitize_error_text_strips_apikey_keyvalue() -> None:
    """apikey=xxx 形态的裸值被剥离，保留键名与分隔符。"""
    assert sanitize_error_text("failed at apikey=sk-123") == "failed at apikey=***"


def test_sanitize_error_text_strips_hyphenated_api_key() -> None:
    """api-key=xxx 连字符键名变体同样剥离。"""
    assert sanitize_error_text("api-key=secret") == "api-key=***"


def test_sanitize_error_text_strips_authorization_basic_scheme() -> None:
    """Authorization: Basic xxx 形态剥离 scheme 与凭据，不泄露凭据。"""
    assert sanitize_error_text("echo Authorization: Basic abc123") == "echo Authorization: ***"


def test_sanitize_error_text_preserves_plain_bearer() -> None:
    """无键名前缀的 Bearer 令牌仍由 Bearer 模式剥离，叠加覆盖。"""
    assert sanitize_error_text("Invalid Bearer sk-secret-token-123") == "Invalid Bearer ***"


def test_sanitize_error_text_strips_token_keyvalue() -> None:
    """token=xxx 形态的裸值被剥离，保留键名与分隔符。"""
    assert sanitize_error_text("failed at token=abc123") == "failed at token=***"


def test_sanitize_error_text_strips_secret_keyvalue() -> None:
    """secret=xxx 形态的裸值被剥离，保留键名与分隔符。"""
    assert sanitize_error_text("secret=s3cr3t-value") == "secret=***"


def test_sanitize_error_text_strips_compound_token_and_secret_variants() -> None:
    """access_token / client_secret / auth-token 等复合键名变体同样剥离。"""
    assert sanitize_error_text("access_token=eyJhbGciOi") == "access_token=***"
    assert sanitize_error_text("client_secret: hunter2") == "client_secret: ***"
    assert sanitize_error_text("auth-token: t0k") == "auth-token: ***"


def test_sanitize_error_text_preserves_plain_words_without_separator() -> None:
    """无 : 或 = 分隔符的普通文本 token/secret 词形不受影响，避免误伤非敏感内容。"""
    assert sanitize_error_text("token count exceeded") == "token count exceeded"
    assert sanitize_error_text("the secret sauce") == "the secret sauce"
    assert sanitize_error_text("max_tokens: 4096 exceeded") == "max_tokens: 4096 exceeded"


# ==================== format_error_for_user 集成净化 ====================


def test_format_error_for_user_redacts_bearer_in_api_error_message() -> None:
    """format_error_for_user 对 APIError 的 message 做 Bearer 脱敏，令牌不进入用户可见输出。"""
    err = SeedreamAPIError(message="Invalid Bearer sk-secret-token-123")

    rendered = format_error_for_user(err)

    assert "sk-secret-token-123" not in rendered
    assert "Bearer ***" in rendered


def test_format_error_for_user_sanitizes_error_code_hint() -> None:
    """code_hint 过净化管线：控制字符压平、凭据片段剥离，伪造行不进入用户可见输出。"""
    err = SeedreamAPIError(message="boom", error_code="x\r\nFAKE api_key: SECRET")

    rendered = format_error_for_user(err)

    assert "\r" not in rendered
    assert "\n" not in rendered
    assert "SECRET" not in rendered
    assert "api_key: ***" in rendered


def test_format_error_for_user_truncates_overlong_error_code() -> None:
    """超长错误码在 code_hint 拼接前被截断，用户可见输出长度受上限约束。"""
    err = SeedreamAPIError(message="boom", error_code="C" * 900)

    rendered = format_error_for_user(err)

    assert "<truncated:900 chars>" in rendered
    assert rendered.count("C") == 500


def test_format_error_for_user_strips_crlf_in_message() -> None:
    """APIError message 经 CRLF 剥离，换行不进入用户可见输出。"""
    err = SeedreamAPIError(message="first\r\nsecond")

    rendered = format_error_for_user(err)

    assert "\r" not in rendered
    assert "\n" not in rendered


# ==================== CRLF 日志注入剥离 ====================


def test_sanitize_error_text_strips_crlf_injection() -> None:
    """CR/LF 控制字符被替换为空格，防止上游错误体在日志中伪造行注入误导记录。

    与 io_url.sanitize_url 的控制字符剥离对齐。
    """
    injected = "正常错误\r\nERROR fake-line\napikey=leaked"
    redacted = sanitize_error_text(injected)
    assert "\r" not in redacted
    assert "\n" not in redacted
    # 伪造的 ERROR 行不再独占一行，被压平为同一行内的空格分隔片段
    assert "fake-line" in redacted
    # 叠加键值裸值剥离：apikey=leaked 被脱敏
    assert "leaked" not in redacted


def test_sanitize_error_text_strips_lone_cr_and_lf() -> None:
    """单独的 CR 或 LF 同样被剥离。"""
    assert sanitize_error_text("a\rb\nc") == "a b c"


# ==================== 换行分隔绕过与贪婪多词吸收 ====================


def test_sanitize_error_text_blocks_newline_separator_bypass() -> None:
    """键与值以换行分隔的形态在控制字符压平后被键值模式命中，凭据不得借换行绕过脱敏。"""
    redacted = sanitize_error_text("api_key:\nSK-abcdef1234567890 leaked")
    assert "SK-abcdef1234567890" not in redacted
    assert "api_key:" in redacted

    redacted_auth = sanitize_error_text("Authorization:\r\nBasic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in redacted_auth


def test_sanitize_error_text_absorbs_multiword_values() -> None:
    """多词凭据整体吸收至行尾，第三个及以后的词不再泄露。"""
    assert sanitize_error_text("api_key = secretA secretB secretC") == "api_key = ***"


def test_sanitize_error_text_absorption_stops_at_next_keyvalue() -> None:
    """值吸收在下一个键值形态前停止，同文本中后续键值对仍被独立命中而非整体吞掉。

    键值后的普通尾词无法与凭据多词值区分，按 fail-safe 方向并入值一并脱敏。
    """
    assert sanitize_error_text("token=abc api_key=xyz tail") == "token=*** api_key=***"


def test_sanitize_error_text_preserves_adjacent_non_sensitive_keyvalue() -> None:
    """值吸收在任意键名形态前停止：多键 JSON 只脱敏敏感键，相邻非敏感键值对保留。

    停止前瞻识别任意键名加分隔符的结构，非敏感键不被贪婪吸收吞掉，JSON 括号配平。
    """
    redacted = sanitize_error_text('{"api_key": "SK-1", "note": "keep me"}')
    assert "SK-1" not in redacted
    assert '"note": "keep me"' in redacted
    assert redacted.endswith("}")

    single_quoted = sanitize_error_text("{'api_key': 'a1', 'note': 'n1'}")
    assert "a1" not in single_quoted
    assert "'note': 'n1'" in single_quoted
    assert single_quoted.endswith("}")

    # 裸键值形态同样在非敏感键名前停止吸收，trace_id 保留原文
    assert sanitize_error_text("token=abc trace_id=xyz") == "token=*** trace_id=xyz"


# ==================== 分隔符正则性能守护 ====================


def test_sanitize_data_text_long_space_run_stays_fast() -> None:
    """性能守护：长空格串输入的脱敏在时限内完成，防止分隔符二次方回溯回归。

    同一段空格被两个量词分别吸收时失败匹配呈二次方；数据字段通道的 16KB 预算
    内正则见到完整输入，原文保留断言同时锁定无分隔符不误命中。
    """
    hostile = "token" + " " * 15_000 + "value"

    start = time.perf_counter()
    redacted = sanitize_data_text(hostile)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1
    # 无分隔符不命中键值模式，原文保留
    assert redacted == hostile

    # 错误文本入口先截断约束正则工作长度，同为毫秒级
    start = time.perf_counter()
    sanitize_error_text("token=" + " " * 20_000 + "value")
    assert time.perf_counter() - start < 0.1


def test_sanitize_data_text_unicode_whitespace_run_stays_fast() -> None:
    """性能守护：Unicode 空白长串与引号空白组合的脱敏保持线性，防字符类扩展后回溯回归。"""
    nbsp_run = "token" + chr(0xA0) * 15_000 + "value"
    start = time.perf_counter()
    redacted = sanitize_data_text(nbsp_run)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1
    # 无分隔符不命中键值模式，原文保留
    assert redacted == nbsp_run

    quoted_nbsp = "token '" + chr(0xA0) * 15_000 + "' value"
    start = time.perf_counter()
    sanitize_data_text(quoted_nbsp)
    assert time.perf_counter() - start < 0.1


def test_sanitize_data_text_underscore_chain_stays_fast() -> None:
    """性能守护：键名续段星号的失败回溯保持线性，连字符长链不得触发指数回溯。

    续段字符类跨分隔符字符时嵌套量词切分歧义，200 段即超分钟级。
    """
    hostile = "session" + "_a" * 7_000

    start = time.perf_counter()
    redacted = sanitize_data_text(hostile)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5
    # 无分隔符不命中键值模式，原文保留
    assert redacted == hostile


def test_sanitize_data_text_escaped_quote_run_stays_fast() -> None:
    """性能守护：数千转义引号的对抗输入保持线性耗时，反斜杠容忍不引入回溯退化。"""
    hostile = "noise " + '\\"' * 4_000 + " api_key: sk-1"
    start = time.perf_counter()
    redacted = sanitize_data_text(hostile)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1
    assert "sk-1" not in redacted

    spaced = "token " + '\\"' + " " * 2_000 + '\\"' + " " * 2_000 + " : sk-1"
    start = time.perf_counter()
    redacted_spaced = sanitize_data_text(spaced)
    assert time.perf_counter() - start < 0.1
    assert "sk-1" not in redacted_spaced

    newline_flood = "token" + "\\n" * 4_000
    start = time.perf_counter()
    sanitize_data_text(newline_flood)
    assert time.perf_counter() - start < 0.1


def test_sanitize_error_text_strips_password_and_cookie_keyvalues() -> None:
    """password/cookie 键值形态的裸值同样剥离，凭据不残留在用户可见输出。"""
    assert sanitize_error_text("password=hunter2topsecret") == "password=***"
    assert sanitize_error_text("Cookie: SESSIONID=xyz789") == "Cookie: ***"
    assert "xyz789" not in sanitize_error_text("Cookie: SESSIONID=xyz789")


# ==================== URL userinfo 剥离 ====================


def test_sanitize_error_text_strips_url_userinfo() -> None:
    """错误文本中 URL 的 user:pass@ 凭据被剥离，scheme/host/path 保留。"""
    redacted = sanitize_error_text("failed https://user:pass@example.com/a.png")
    assert redacted == "failed https://example.com/a.png"


def test_sanitize_error_text_strips_url_username_only() -> None:
    """仅用户名无密码的 userinfo 形态同样剥离。"""
    redacted = sanitize_error_text("see https://user@example.com/a.png")
    assert redacted == "see https://example.com/a.png"


def test_sanitize_error_text_keeps_userinfo_free_url() -> None:
    """无凭据 URL 原样保留，不误伤正常链接文本。"""
    url = "https://example.com/a.png"
    assert sanitize_error_text(url) == url


# ==================== 全角分隔符绕过与结果数据路径净化 ====================


def test_sanitize_error_text_blocks_fullwidth_separator_bypass() -> None:
    """全角冒号/等号分隔的键值形态同样剥离，封堵非 ASCII 分隔符绕过。"""
    assert sanitize_error_text("api_key：abc123 leaked") == "api_key：***"
    assert sanitize_error_text("token＝secret") == "token＝***"


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
    """非 SSE 路径的 per-image error.message 净化后返回新列表，传入列表与条目不被修改。"""
    from seedream_mcp.tools.core.results import _sanitize_image_errors

    images = [
        {"url": "https://a/1.png"},
        {"error": {"code": "X", "message": "api_key: sk-leaked"}},
    ]
    # 须持原字典对象的直接引用才能断言其未被就地修改。
    original_dirty_item = images[1]

    sanitized = _sanitize_image_errors(images)

    assert "sk-leaked" not in str(sanitized[1])
    assert "***" in sanitized[1]["error"]["message"]
    # 净化返回新列表，干净条目保持原对象引用。
    assert sanitized is not images
    assert sanitized[0] is images[0]
    assert original_dirty_item["error"]["message"] == "api_key: sk-leaked"


# ==================== 数据字段净化不截断：sanitize_data_text ====================


def test_sanitize_data_text_preserves_long_url_without_truncation() -> None:
    """约 670 字符的签名 URL 原样保留：数据字段不做错误文本的 500 字符截断。"""
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

    token=/Secret= 等查询参数名触发剥离会把查询串整体替换为 ***，数据字段不可用。
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
    """纯 URL 判定先 strip 首尾空白，带空白前缀的签名 URL 走轻量路径。"""
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


def test_api_error_dict_message_sanitized_for_user() -> None:
    """dict 形态 message 归一化为 JSON 后脱敏，api_key 键值凭据不残留。"""
    err = SeedreamAPIError(message={"api_key": "SK-SECRET", "note": "x"})  # type: ignore[arg-type]

    rendered = format_error_for_user(err)

    assert "SK-SECRET" not in rendered
    assert "***" in rendered


def test_handle_api_error_normalizes_dict_upstream_message() -> None:
    """上游响应体的 dict 形态 message 拼接前归一化，用户可见输出凭据不残留。"""
    exc = handle_api_error(400, {"error": {"message": {"api_key": "SK-SECRET"}}})

    user_rendered = format_error_for_user(exc)
    assert "SK-SECRET" not in user_rendered
    assert "***" in user_rendered


def test_handle_api_error_normalizes_list_upstream_message() -> None:
    """list 形态 message 同样归一化，str() 兜底后进入脱敏管线。"""
    exc = handle_api_error(400, {"message": ["api_key=SK-SECRET"]})

    user_rendered = format_error_for_user(exc)
    assert "SK-SECRET" not in user_rendered


def test_api_error_deeply_nested_message_does_not_raise_recursion_error() -> None:
    """十万层嵌套 list 形态 message 归一化不外逃 RecursionError，降级为占位文本。"""
    deep: Any = []
    for _ in range(100_000):
        deep = [deep]

    err = SeedreamAPIError(message=deep)  # type: ignore[arg-type]

    rendered = format_error_for_user(err)
    assert isinstance(rendered, str)
    assert rendered != ""


# ==================== message 的 truncate-first 次序 ====================


def test_format_error_for_user_truncates_before_redaction() -> None:
    """format_error_for_user 先截断后脱敏：丢弃段凭据随截断消失，保留段凭据被剥离。"""
    boundary_split = SeedreamAPIError(message="a" * 495 + "api_key=" + "SECRET" * 200)
    rendered = format_error_for_user(boundary_split)
    assert "SECRET" not in rendered
    assert len(rendered) < 700

    kept_prefix = SeedreamAPIError(message="api_key=leaked " + "x" * 600)
    rendered_kept = format_error_for_user(kept_prefix)
    assert "leaked" not in rendered_kept
    assert "api_key=***" in rendered_kept


# ==================== 引号键值形态与误伤防护 ====================


def test_sanitize_error_text_blocks_quoted_keyvalue_forms() -> None:
    """JSON/Python repr 的引号键值形态同样命中剥离，键名后紧跟引号再接冒号。"""
    assert "xxx" not in sanitize_error_text("{'api_key': 'xxx'}")
    assert "xxx" not in sanitize_error_text('{"api_key": "xxx"}')
    # 值吸收为贪婪多词，收尾引号与花括号并入脱敏值一并消隐，方向 fail-safe。
    assert sanitize_error_text('{"api_key": "xxx"}') == '{"api_key": ***'


def test_sanitize_error_text_quote_variant_no_overmatch() -> None:
    """引号变体不误伤普通文本：无分隔符的引号词形与既有保留样例不受影响。"""
    assert sanitize_error_text('he mentioned "token" in prose') == 'he mentioned "token" in prose'
    assert sanitize_error_text('the "secret" ingredient') == 'the "secret" ingredient'
    assert sanitize_error_text("the token count is fine") == "the token count is fine"


# ==================== Unicode 空白与控制字符分隔绕过回归 ====================


def test_sanitize_error_text_blocks_control_char_separator_bypass() -> None:
    """垂直制表符、换页符与 NEL 分隔的键值在控制字符压平后被键值模式命中。"""
    assert sanitize_error_text("api_key:\x0bSECRET123") == "api_key: ***"
    assert sanitize_error_text("api_key:\x0cSECRET123") == "api_key: ***"
    assert sanitize_error_text("api_key:\x85SECRET123") == "api_key: ***"
    assert "SECRET123" not in sanitize_error_text("token\x0b=SECRET123")


def test_sanitize_error_text_blocks_unicode_whitespace_separator_bypass() -> None:
    """NBSP、全角空格与 em 空白分隔的键值形态同样剥离，凭据不借 Unicode 空白逃逸。"""
    nbsp = chr(0xA0)
    ideographic = chr(0x3000)
    em_space = chr(0x2003)
    assert sanitize_error_text("password" + nbsp + "=" + nbsp + "SECRET") == (
        "password" + nbsp + "=" + nbsp + "***"
    )
    assert sanitize_error_text("token" + ideographic + ":" + ideographic + "SECRET") == (
        "token" + ideographic + ":" + ideographic + "***"
    )
    assert sanitize_error_text("api_key" + em_space + "=" + em_space + "SECRET") == (
        "api_key" + em_space + "=" + em_space + "***"
    )


def test_sanitize_error_text_blocks_quote_space_quote_separator() -> None:
    """「引号-空白-引号」形态的分隔符组合同样命中，secret 不残留。"""
    redacted = sanitize_error_text("api_key '' : secret123")
    assert "secret123" not in redacted
    assert redacted == "api_key '' : ***"


# ==================== 转义引号与字面 \n 分隔绕过 ====================


def test_sanitize_error_text_strips_escaped_quote_keyvalue_forms() -> None:
    """json.dumps/repr 转义引号形态的键值分隔同样命中，凭据不借归一化产物逃逸。"""
    redacted = sanitize_error_text('auth failed for \\"api_key\\": \\"sk-xxx\\"')
    assert "sk-xxx" not in redacted
    assert redacted == 'auth failed for \\"api_key\\": ***'


def test_sanitize_error_text_strips_literal_backslash_n_separator() -> None:
    """字面反斜杠加 n 的转义换行分隔形态同样剥离，凭据不借转义换行逃逸。"""
    assert sanitize_error_text("api_key\\nsk-1") == "api_key\\n***"


def test_handle_api_error_strips_credentials_in_escaped_quote_json_message() -> None:
    """嵌套字符串内的引号经 json.dumps 转义后，键值凭据在用户可见输出被剥离。"""
    resp = {
        "error": {
            "code": "InvalidParameter",
            "message": {"detail": 'auth failed for "api_key": "sk-live-9f8e7d6c"'},
        }
    }
    err = handle_api_error(400, resp)

    user_text = format_error_for_user(err)

    assert "sk-live-9f8e7d6c" not in user_text
    assert "***" in user_text


def test_handle_api_error_strips_newline_separator_after_json_normalization() -> None:
    """实际换行分隔的键值经 json.dumps 归一化为字面 \\n 后仍被剥离。"""
    err = handle_api_error(400, {"error": {"message": {"detail": "api_key\nsk-1"}}})

    user_text = format_error_for_user(err)

    assert "sk-1" not in user_text


# ==================== 控制字符类单一来源 ====================


def test_control_chars_pattern_flattens_c0_del_and_nel() -> None:
    """控制字符类统一覆盖 C0、DEL 与 NEL，errors 与 logs 两模块共用同一常量。"""
    from seedream_mcp.utils.core import errors as errors_module
    from seedream_mcp.utils.core import logs as logs_module

    assert errors_module.CONTROL_CHARS_PATTERN is logs_module._LOG_MESSAGE_CONTROL_CHARS
    for ch in ("\x00", "\x08", "\x0b", "\x0c", "\r", "\n", "\x1f", "\x7f", "\x85"):
        assert errors_module.CONTROL_CHARS_PATTERN.sub(" ", f"a{ch}b") == "a b"
    # 可打印字符不受影响
    assert errors_module.CONTROL_CHARS_PATTERN.sub(" ", "a b中") == "a b中"


def test_control_chars_pattern_flattens_line_paragraph_separators() -> None:
    """行/段分隔符 U+2028/U+2029 与键值空白类口径对齐，压平为空格防日志注入伪造行。"""
    line_sep = chr(0x2028)
    para_sep = chr(0x2029)

    assert CONTROL_CHARS_PATTERN.sub(" ", f"a{line_sep}b{para_sep}c") == "a b c"
    assert sanitize_error_text(f"first{line_sep}FAKE{para_sep}apikey=leaked") == (
        "first FAKE apikey=***"
    )


# ==================== 自由文本键名与 dict 键策略单一来源 ====================


def test_sanitize_error_text_strips_session_jwt_privatekey_keyvalues() -> None:
    """session/jwt/privatekey 等自由文本键名与 dict 键策略同覆盖，裸值剥离。"""
    assert sanitize_error_text("session_id=abc123") == "session_id=***"
    assert sanitize_error_text("session-id=abc123") == "session-id=***"
    assert sanitize_error_text("jwt=eyJhbGciOiJIUzI1NiJ9") == "jwt=***"
    assert sanitize_error_text("privatekey=SECRET") == "privatekey=***"
    assert sanitize_error_text("sshkey=SECRET") == "sshkey=***"
    assert sanitize_error_text("signature=hmac-value") == "signature=***"
    assert sanitize_error_text("nonce=42") == "nonce=***"
    assert sanitize_error_text("saml_assertion=payload") == "saml_assertion=***"


def test_is_sensitive_key_matches_privatekey_and_sshkey() -> None:
    """无分隔复合词 privatekey/sshkey 纳入高确信子串清单。

    与 apikey 策略统一。
    """
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    assert _is_sensitive_key("privatekey") is True
    assert _is_sensitive_key("sshkey") is True
    assert _is_sensitive_key("my-privatekey") is True
    assert _is_sensitive_key("x_sshkey") is True
    # 边界匹配的防误伤语义保留：monkey、keyboard 不因含 key 字面命中
    assert _is_sensitive_key("monkey") is False
    assert _is_sensitive_key("keyboard") is False


# ==================== camelCase 敏感键双路径覆盖 ====================


def test_sanitize_error_text_strips_camelcase_sensitive_keyvalues() -> None:
    """camelCase 敏感键的裸值剥离，凭据不借驼峰命名逃逸。

    覆盖 secretKey/accessKey/sessionKey/authKey 四键。
    """
    assert sanitize_error_text("secretKey=AKIAIOSFODNN7EXAMPLE") == "secretKey=***"
    assert sanitize_error_text("accessKey: AKIAIOSFODNN7EXAMPLE") == "accessKey: ***"
    assert sanitize_error_text("sessionKey=abc123def456") == "sessionKey=***"
    assert sanitize_error_text("authKey=xyz789") == "authKey=***"


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


def test_is_sensitive_key_matches_camelcase_token_secret_compounds() -> None:
    """camelCase token/secret 复合键命中子串清单，与自由文本复合分支同口径。

    refreshToken、clientSecret 一类无分隔复合词此前 dict 键路径不命中，补齐后一致。
    """
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    for key in (
        "refreshToken",
        "accessToken",
        "authToken",
        "sessionToken",
        "apiToken",
        "clientSecret",
        "apiSecret",
        "signingSecret",
        "appSecret",
    ):
        assert _is_sensitive_key(key) is True
    # 对照组：普通词键不因含 token/secret 字面命中
    assert _is_sensitive_key("tokenCount") is False
    assert _is_sensitive_key("secretive") is False


# ==================== 空格复合词 API Key ====================


def test_sanitize_error_text_strips_space_separated_api_key() -> None:
    """空格复合词 "API Key: <凭据>" 同样命中键值脱敏，封堵分隔符前带空格的绕过。"""
    assert sanitize_error_text("API Key: AKIAIOSFODNN7EXAMPLE") == "API Key: ***"
    assert sanitize_error_text("api key = secret123") == "api key = ***"
    # 对照组：连字符与下划线变体仍命中
    assert sanitize_error_text("X-Api-Key: k1") == "X-Api-Key: ***"
    assert sanitize_error_text("api_key=k2") == "api_key=***"


def test_sanitize_error_text_api_key_space_form_no_overmatch() -> None:
    """普通含 api/key 词句不误吞：无分隔符与值的词组保持原文。"""
    assert sanitize_error_text("api key is missing") == "api key is missing"
    assert sanitize_error_text("please rotate the api key regularly") == (
        "please rotate the api key regularly"
    )
    # key 后紧跟普通字母的复合词不命中，仅 "key" 前缀不足以触发
    assert sanitize_error_text("my api keyboard mapping") == "my api keyboard mapping"


def test_is_sensitive_key_matches_space_separated_compound() -> None:
    """空格作为键名边界分隔符与自由文本复合分支同规则："api key" 键名命中。"""
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    assert _is_sensitive_key("api key") is True
    assert _is_sensitive_key("my api key") is True
    # 对照组：X-Api-Key 仍命中，普通复合词不误吞
    assert _is_sensitive_key("X-Api-Key") is True
    assert _is_sensitive_key("rapid api keyboard") is False
    assert _is_sensitive_key("monkey") is False


# ==================== 复合键中段关键词命中 ====================


def test_is_sensitive_key_matches_mid_segment_keyword_forms() -> None:
    """复合键中段的敏感关键词段同样命中，与 docstring 声明及自由文本未锚定口径一致。"""
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    assert _is_sensitive_key("user.session_id") is True
    assert _is_sensitive_key("a.session_id.b") is True
    assert _is_sensitive_key("request_session_id") is True
    assert _is_sensitive_key("request-session-id") is True
    assert _is_sensitive_key("user auth scope") is True
    # 对照组：无敏感段的普通复合键不命中，关键词不吞并整词段的相邻字符
    assert _is_sensitive_key("user.profile.id") is False
    assert _is_sensitive_key("request-id") is False
    assert _is_sensitive_key("sessions") is False


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


def test_sanitize_error_text_preserves_suffix_word_forms() -> None:
    """后缀形态的普通词不受派生分支影响：续段要求以分隔符开头，max_tokens 保留。"""
    assert sanitize_error_text("max_tokens: 4096 exceeded") == "max_tokens: 4096 exceeded"
    assert sanitize_error_text("token count exceeded") == "token count exceeded"


# ==================== 字面转义分隔族与真实控制空白独占分隔 ====================


def test_sanitize_error_text_strips_literal_escape_separator_family() -> None:
    """字面转义序列 \t \r \f 与十六进制转义 \u000b \x0b 分隔的键值同样剥离。

    凭据不借 json.dumps/repr 的转义产物逃逸。
    """
    assert sanitize_error_text("api_key\\tsk-1") == "api_key\\t***"
    assert sanitize_error_text("api_key\\rsk-1") == "api_key\\r***"
    assert sanitize_error_text("api_key\\fsk-1") == "api_key\\f***"
    assert sanitize_error_text("api_key\\u000bsk-1") == "api_key\\u000b***"
    assert sanitize_error_text("api_key\\x0bsk-1") == "api_key\\x0b***"
    # 大写十六进制变体同样命中
    assert "sk-1" not in sanitize_error_text("api_key\\u000Bsk-1")
    assert "sk-1" not in sanitize_error_text("api_key\\x0Bsk-1")


def test_handle_api_error_strips_real_tab_cr_in_nested_message() -> None:
    """嵌套 dict message 含真实 TAB/CR，JSON 转义为字面 \t \r 后用户可见输出被剥离。"""
    resp = {"error": {"code": "E", "message": {"detail": "api_key\tsk-live-1\rjwt=sk-live-2"}}}
    err = handle_api_error(400, resp)

    user_text = format_error_for_user(err)

    assert "sk-live-1" not in user_text
    assert "sk-live-2" not in user_text


def test_sanitize_error_text_blocks_real_control_char_standalone_separator() -> None:
    """真实换行或制表符独占分隔的键值在压平前的首轮匹配命中，不借控制字符绕过。"""
    for sep in ("\n", "\r", "\t"):
        redacted = sanitize_error_text(f"api_key{sep}SK-abcdef1234567890")
        assert "SK-abcdef1234567890" not in redacted
        assert sep not in redacted
        assert redacted.startswith("api_key")

    assert sanitize_error_text("api_key\r\nSK-1") == "api_key  ***"
    assert sanitize_error_text("api_key:\nSK-1") == "api_key: ***"
    assert sanitize_error_text("api_key:\tSK-1") == "api_key: ***"


def test_real_control_separator_credentials_blocked_in_user_output() -> None:
    """真实换行分隔的凭据在 format_error_for_user 出口不残留，换行被压平。"""
    err = SeedreamAPIError(message="上游回显 api_key\nSK-live-abcdef 其余说明")

    user_text = format_error_for_user(err)

    assert "SK-live-abcdef" not in user_text
    assert "\n" not in user_text


def test_sanitize_data_text_control_char_run_stays_fast() -> None:
    """性能守护：控制空白长串在无值形态下失败回溯保持线性。

    控制空白分支与前后空白量词分别吸收同一段空白时呈二次方，前瞻限定后切分唯一。
    """
    hostile = "token" + "\t" * 15_000

    start = time.perf_counter()
    redacted = sanitize_data_text(hostile)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1
    assert "\t" not in redacted


# ==================== userinfo 密码含 @ 与查询串 @ 保留 ====================


def test_sanitize_error_text_strips_userinfo_password_containing_at() -> None:
    """密码含 @ 时贪婪剥到主机前最后一个 @，不再残留 ss@ 片段。"""
    redacted = sanitize_error_text("https://user:p@ss@example.com/a.png")

    assert redacted == "https://example.com/a.png"
    assert "p@ss" not in redacted


def test_sanitize_error_text_keeps_query_at_without_userinfo() -> None:
    """无 userinfo 的 URL 查询串中的 @ 不触发剥离，链接保留原样。"""
    url = "https://example.com/a.png?email=a@b"

    assert sanitize_error_text(url) == url


# ==================== 点号键名两通道一致性 ====================


def test_sanitize_error_text_strips_dotted_sensitive_keyvalues() -> None:
    """点号续段的键名 session.id、api.key、access.token 裸值剥离。"""
    assert sanitize_error_text("session.id=abc123") == "session.id=***"
    assert sanitize_error_text("api.key: SECRET") == "api.key: ***"
    assert sanitize_error_text("access.token=SECRET") == "access.token=***"
    # 对照组：无分隔符与值的点号词组保持原文
    assert sanitize_error_text("session.idea was good") == "session.idea was good"


def test_dotted_sensitive_key_redacts_consistently_across_channels() -> None:
    """session.id 在 dict 键与自由文本两条通道同判敏感，口径一致。"""
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    assert _is_sensitive_key("session.id") is True
    assert sanitize_error_text("session.id=abc") == "session.id=***"


# ==================== 零宽不可见字符与全角引号变体 ====================


def test_sanitize_error_text_blocks_zero_width_key_variants() -> None:
    """零宽字符插入键名内部或键与分隔符之间时先移除再匹配，真实键名照常命中。"""
    zwsp = chr(0x200B)
    zwnj = chr(0x200C)
    soft_hyphen = chr(0x00AD)
    bom = chr(0xFEFF)
    assert sanitize_error_text(f"api{zwsp}key=SECRET") == "apikey=***"
    assert sanitize_error_text(f"session{zwnj}.id=abc") == "session.id=***"
    assert sanitize_error_text(f"token{soft_hyphen}=SECRET") == "token=***"
    assert sanitize_error_text(f"{bom}token=SECRET") == "token=***"


def test_sanitize_error_text_blocks_fullwidth_quote_separator() -> None:
    """全角引号 ＂ ＇ 作为分隔符引号组的变体命中，凭据不借全角引号逃逸。"""
    assert sanitize_error_text("api_key＂: sk-1") == "api_key＂: ***"
    assert sanitize_error_text("api_key＇: sk-1") == "api_key＇: ***"


# ==================== key/auth 受限复合分支两路径一致性 ====================

# 两条脱敏路径应一致判定敏感的复合键名全集：X_key/X_auth 前缀族内的下划线、连字符、
# 点号与空格分隔变体，以及 auth 前缀复合键。dict 键路径按段匹配判敏感，自由文本
# 路径经前缀族复合分支命中，二者不得漂移。
_CONSISTENT_SENSITIVE_COMPOUND_KEYS = (
    "access_key_id",
    "ssh_key",
    "auth_header",
    "encryption_key",
    "user_key",
    "public_key",
    "private_key",
    "gpg_key",
    "signing_key",
    "client_key",
    "app_key",
    "session_key",
    "secret_key",
    "auth_key",
    "user_auth",
    "session_auth",
    "client_auth",
    "api_auth",
    "secret_auth",
    "auth_scheme",
    "auth-mode",
    "access-key",
    "ssh.key",
    "user key",
    "auth",
)

# 两条路径应一致判定为普通的词形：无分隔符复合词与族外前缀的普通词组。
_CONSISTENT_PLAIN_COMPOUND_KEYS = (
    "keyboard",
    "monkey",
    "keynote",
    "author",
    "author_name",
    "user profile",
    "user keyboard",
    "ssh keyboard layout",
)


def test_compound_key_hit_verdicts_align_across_dict_and_free_text_paths() -> None:
    """对抗性一致性锁定：敏感复合键名在 dict 键路径与自由文本键值路径同判敏感。

    两侧判定不一致时失败，防止前缀族分支与段匹配口径漂移。
    """
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    for key in _CONSISTENT_SENSITIVE_COMPOUND_KEYS:
        assert _is_sensitive_key(key) is True, key
        assert sanitize_error_text(f"{key}=cred-value") == f"{key}=***", key


def test_compound_key_plain_verdicts_align_across_dict_and_free_text_paths() -> None:
    """对抗性一致性锁定：普通词形在两条路径同判不敏感，复合分支不误吞。"""
    from seedream_mcp.utils.core.errors import _is_sensitive_key

    for key in _CONSISTENT_PLAIN_COMPOUND_KEYS:
        assert _is_sensitive_key(key) is False, key
        assert sanitize_error_text(f"{key}=cred-value") == f"{key}=cred-value", key


def test_sanitize_error_text_strips_family_compound_keyvalues() -> None:
    """前缀族 X_key/X_auth 复合键名的裸值剥离，dict 键路径判敏感的形态不再穿透。"""
    assert sanitize_error_text("access_key_id=AKID123") == "access_key_id=***"
    assert sanitize_error_text("ssh_key: ssh-rsa AAAA") == "ssh_key: ***"
    assert sanitize_error_text("auth_header=Bearer abc") == "auth_header=***"
    assert sanitize_error_text("encryption_key=aes-256") == "encryption_key=***"
    assert sanitize_error_text("user_key: u-123 leaked") == "user_key: ***"


def test_sanitize_error_text_strips_bare_auth_keyvalue() -> None:
    """独立成段的 auth 键名与 dict 键路径同判敏感，auth: value 的值整体脱敏。"""
    assert sanitize_error_text("auth=Bearer abc123") == "auth=***"
    assert sanitize_error_text("token check auth: SK-9") == "token check auth: ***"


def test_sanitize_error_text_compound_branches_no_overmatch() -> None:
    """无分隔符与族外前缀的普通词形不被复合分支误吞，普通键值对保留原文。"""
    assert sanitize_error_text("keyboard=F1") == "keyboard=F1"
    assert sanitize_error_text("monkey=see") == "monkey=see"
    assert sanitize_error_text("keynote=abc") == "keynote=abc"
    assert sanitize_error_text("author=Jane") == "author=Jane"
    assert sanitize_error_text("oauth=xx") == "oauth=xx"
    assert sanitize_error_text("ssh keyboard layout=us") == "ssh keyboard layout=us"


def test_sanitize_data_text_compound_branches_stay_fast() -> None:
    """性能守护：前缀族复合分支的失败回溯保持线性，分隔长链不触发指数回溯。"""
    hostile = "user_key" + "_a" * 7_000

    start = time.perf_counter()
    redacted = sanitize_data_text(hostile)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5
    # 末尾无分隔符与值，复合键命中失败，原文保留
    assert redacted == hostile

    auth_chain = "auth" + "_a" * 7_000
    start = time.perf_counter()
    redacted_auth = sanitize_data_text(auth_chain)
    assert time.perf_counter() - start < 0.5
    assert redacted_auth == auth_chain
