"""cli 侧 streamable-http 绑定安全校验测试。

非回环绑定必须配置鉴权令牌与 TLS，回环绑定豁免；错误消息为对外契约，逐字锁定。
cli_main 的集成路径由 test_server_transport_options 覆盖，本文件锁定校验函数本身。
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Collection

import pytest

import seedream_mcp.server as server
from seedream_mcp.transport import _LOOPBACK_HOSTS as _PRODUCED_LOOPBACK_HOSTS

# 注入输入直接取生产常量，避免手抄副本静默漂移；显式锁定成员防常量被意外
# 放宽（如把可被 DNS 污染的 localhost 误加入免鉴权集合）。
assert _PRODUCED_LOOPBACK_HOSTS == {"127.0.0.1", "::1"}
_LOOPBACK_HOSTS: Collection[str] = _PRODUCED_LOOPBACK_HOSTS


def _make_http_args(
    transport: str = "streamable-http",
    host: str = "0.0.0.0",
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    insecure_allow_non_tls: bool = False,
) -> Namespace:
    return Namespace(
        transport=transport,
        host=host,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        insecure_allow_non_tls=insecure_allow_non_tls,
    )


def test_validate_http_security_requires_token_for_non_loopback() -> None:
    """非回环绑定缺少鉴权令牌时返回原文错误消息，锁定文案不漂移。"""
    message = server._validate_http_security(_make_http_args(), "", _LOOPBACK_HOSTS)

    assert message == (
        "安全错误：streamable-http 绑定到非回环地址 0.0.0.0 必须配置鉴权令牌，"
        "请通过 --auth-token 或 SEEDREAM_HTTP_AUTH_TOKEN 提供，避免未授权访问。"
    )


def test_validate_http_security_requires_tls_for_non_loopback() -> None:
    """非回环绑定携带令牌但无 TLS 且未显式豁免时返回原文错误消息。"""
    args = _make_http_args()
    message = server._validate_http_security(args, "s3cret", _LOOPBACK_HOSTS)

    assert message == (
        "安全错误：streamable-http 绑定到非回环地址 0.0.0.0 必须配置 TLS，"
        "请通过 --ssl-certfile/--ssl-keyfile 提供，或在受信反向代理终结 TLS 时"
        "显式传 --insecure-allow-non-tls，避免 Bearer 令牌明文传输被窃听。"
    )


def test_validate_http_security_accepts_non_loopback_with_tls() -> None:
    """非回环绑定携带令牌与 TLS 证书时校验通过。"""
    args = _make_http_args(ssl_certfile="/fake/cert.pem", ssl_keyfile="/fake/key.pem")

    assert server._validate_http_security(args, "s3cret", _LOOPBACK_HOSTS) is None


def test_validate_http_security_accepts_explicit_non_tls_opt_in() -> None:
    """非回环绑定携带令牌并显式豁免 TLS 时校验通过，适用于反代终结 TLS 场景。"""
    args = _make_http_args(insecure_allow_non_tls=True)

    assert server._validate_http_security(args, "s3cret", _LOOPBACK_HOSTS) is None


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_validate_http_security_exempts_loopback_hosts(host: str) -> None:
    """回环绑定豁免鉴权与 TLS 强制，无令牌无 TLS 也校验通过。"""
    args = _make_http_args(host=host)

    assert server._validate_http_security(args, "", _LOOPBACK_HOSTS) is None


def test_validate_http_security_skips_stdio_transport() -> None:
    """stdio 传输不涉及 HTTP 绑定安全，非回环 host 与空令牌也不构成错误。"""
    args = _make_http_args(transport="stdio")

    assert server._validate_http_security(args, "", _LOOPBACK_HOSTS) is None
