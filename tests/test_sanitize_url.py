"""io_url.sanitize_url 的脱敏契约测试。

锁定 http/https 的 scheme/host/path 保留与凭据、query 剥离，以及无 authority 形态
收敛为 scheme:<redacted>，防止 data URI 被伪造成 data://、空串输出 :// 进入日志。
"""

from __future__ import annotations

from seedream_mcp.utils.io.io_url import sanitize_url


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
