"""io_url 的 URL 辅助契约测试：sanitize_url 脱敏与 get_file_extension_from_url 推断。

锁定 http/https 的 scheme/host/path 保留与凭据、query 剥离，以及无 authority 形态
收敛为 scheme:<redacted>，防止 data URI 被伪造成 data://、空串输出 :// 进入日志。
"""

from __future__ import annotations

from seedream_mcp.utils.io.io_url import get_file_extension_from_url, sanitize_url


def test_sanitize_url_preserves_scheme_host_path() -> None:
    """常规 http/https URL 保留 scheme/host/path，凭据与 query 剥离。"""
    assert sanitize_url("https://example.com/a/b.png") == "https://example.com/a/b.png"
    assert (
        sanitize_url("https://user:pass@example.com/a/b.png?sig=secret")
        == "https://example.com/a/b.png?<query-redacted>"
    )


def test_sanitize_url_redacts_data_uri() -> None:
    """data URI 无 authority，按 scheme:// 重建会伪造成 data://，收敛为 data:<redacted>。"""
    assert sanitize_url("data:image/png;base64,AAAA") == "data:<redacted>"


def test_sanitize_url_redacts_empty_and_authority_less_http() -> None:
    """空串与无 host 无 path 的形态不输出 :// 空壳，收敛为 scheme:<redacted>。"""
    assert sanitize_url("") == ":<redacted>"
    assert sanitize_url("http://") == "http:<redacted>"


def test_sanitize_url_redacts_non_http_scheme_with_authority() -> None:
    """scheme 非 http/https 时即便带 host 也整体收敛，不重建 scheme:// 形态。"""
    assert sanitize_url("ftp://files.example.com/pub/x.png") == "ftp:<redacted>"


def test_sanitize_url_rebuilds_ipv6_host_with_port() -> None:
    """IPv6 字面量 hostname 剥方括号后补回重建，host 小写归一，端口保留。"""
    assert sanitize_url("https://[2001:DB8::1]:8443/a.png?x=1") == (
        "https://[2001:db8::1]:8443/a.png?<query-redacted>"
    )
    assert sanitize_url("https://[2001:db8::1]/a.png") == "https://[2001:db8::1]/a.png"


def test_sanitize_url_drops_invalid_port_only() -> None:
    """端口越界解析失败仅丢弃端口，scheme/host/path 保留供日志定位目标主机。"""
    assert sanitize_url("https://example.com:99999/a.png") == "https://example.com/a.png"
    assert sanitize_url("https://example.com:8443/a.png") == "https://example.com:8443/a.png"


def test_sanitize_url_strips_control_chars_from_result() -> None:
    """上游 URL 携带的 CRLF 在解析阶段剥离，不借日志伪造行注入误导记录。"""
    assert sanitize_url("https://example.com/a\r\nFAKE: x.png") == (
        "https://example.com/aFAKE: x.png"
    )


def test_sanitize_url_falls_back_on_invalid_ipv6() -> None:
    """非法 IPv6 字面量使解析抛错，整体收敛为 <invalid-url>。"""
    assert sanitize_url("https://[::1") == "<invalid-url>"


def test_get_file_extension_from_url_direct_cases() -> None:
    """路径后缀含点号归一小写；无后缀回落 .jpeg 默认值；query 不参与推断。"""
    assert get_file_extension_from_url("https://example.com/a/b.png") == ".png"
    assert get_file_extension_from_url("https://example.com/a/image") == ".jpeg"
    assert get_file_extension_from_url("https://example.com/a/b.png?fmt=jpg&sig=x") == ".png"
    assert get_file_extension_from_url("https://example.com/a/B.PNG") == ".png"


def test_get_file_extension_from_url_invalid_ipv6_falls_back_to_default() -> None:
    """非法 IPv6 使路径取值抛 ValueError，降级返回默认扩展名。"""
    assert get_file_extension_from_url("https://[::1/a.png") == ".jpeg"
