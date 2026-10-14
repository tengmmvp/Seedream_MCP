"""cli 侧 streamable-http 绑定安全校验测试。

非回环绑定必须配置鉴权令牌与 TLS，回环绑定豁免；错误消息为对外契约，逐字锁定。
回环判定成员经参数化用例锁定。cli_main 的集成路径由 test_server_transport_options
覆盖，本文件锁定校验函数本身。
"""

from __future__ import annotations

import pytest

import seedream_mcp.cli as cli
from seedream_mcp.transport import is_loopback_bind_host

from _cli_fakes import make_run_args


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("[::1]", True),
        ("localhost", False),
        ("0.0.0.0", False),
        ("[fe80::1]", False),
        ("[::]", False),
        ("::0", False),
        ("0:0:0:0:0:0:0:0", False),
    ],
)
def test_is_loopback_bind_host_classification_locked(host: str, expected: bool) -> None:
    """锁定回环判定成员，防 localhost 被误加入免鉴权集合，非回环与通配 IPv6 不因剥方括号被误收。"""
    assert is_loopback_bind_host(host) is expected


def test_validate_http_security_requires_token_for_non_loopback() -> None:
    """非回环绑定缺少鉴权令牌时返回原文错误消息，锁定文案不漂移。"""
    args = make_run_args("streamable-http")
    args.host = "0.0.0.0"

    message = cli.validate_http_security(args, "")

    assert message == (
        "安全错误：streamable-http 绑定到非回环地址 0.0.0.0 必须配置鉴权令牌，"
        "请通过 --auth-token 或 SEEDREAM_HTTP_AUTH_TOKEN 提供，避免未授权访问。"
    )


@pytest.mark.parametrize("host", ["::0", "0:0:0:0:0:0:0:0"])
def test_validate_http_security_treats_wildcard_equivalents_as_non_loopback(
    host: str,
) -> None:
    """::0 与全零展开形态与 0.0.0.0 同按非回环强制令牌，不因通配等价拼写豁免校验。"""
    args = make_run_args("streamable-http")
    args.host = host

    message = cli.validate_http_security(args, "")

    assert message == (
        f"安全错误：streamable-http 绑定到非回环地址 {host} 必须配置鉴权令牌，"
        "请通过 --auth-token 或 SEEDREAM_HTTP_AUTH_TOKEN 提供，避免未授权访问。"
    )


def test_validate_http_security_requires_tls_for_non_loopback() -> None:
    """非回环绑定携带令牌但无 TLS 且未显式豁免时返回原文错误消息。"""
    args = make_run_args("streamable-http")
    args.host = "0.0.0.0"

    message = cli.validate_http_security(args, "s3cret")

    assert message == (
        "安全错误：streamable-http 绑定到非回环地址 0.0.0.0 必须配置 TLS，"
        "请通过 --ssl-certfile/--ssl-keyfile 提供，或在受信反向代理终结 TLS 时"
        "显式传 --insecure-allow-non-tls，避免 Bearer 令牌明文传输被窃听。"
    )


def test_validate_http_security_accepts_non_loopback_with_tls() -> None:
    """非回环绑定携带令牌与 TLS 证书时校验通过。"""
    args = make_run_args("streamable-http")
    args.host = "0.0.0.0"
    args.ssl_certfile = "/fake/cert.pem"
    args.ssl_keyfile = "/fake/key.pem"

    assert cli.validate_http_security(args, "s3cret") is None


def test_validate_http_security_accepts_explicit_non_tls_opt_in() -> None:
    """非回环绑定携带令牌并显式豁免 TLS 时校验通过，适用于反代终结 TLS 场景。"""
    args = make_run_args("streamable-http")
    args.host = "0.0.0.0"
    args.insecure_allow_non_tls = True

    assert cli.validate_http_security(args, "s3cret") is None


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]"])
def test_validate_http_security_exempts_loopback_hosts(host: str) -> None:
    """回环绑定豁免鉴权与 TLS 强制，无令牌无 TLS 也校验通过，方括号形态同豁免。"""
    args = make_run_args("streamable-http")
    args.host = host

    assert cli.validate_http_security(args, "") is None


def test_validate_http_security_skips_stdio_transport() -> None:
    """stdio 传输不涉及 HTTP 绑定安全，非回环 host 与空令牌也不构成错误。"""
    args = make_run_args("stdio")
    args.host = "0.0.0.0"

    assert cli.validate_http_security(args, "") is None
