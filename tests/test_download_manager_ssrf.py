"""SSRF 防护层单元测试。

覆盖 _validate_url_static 的各静态拒绝分支、_resolve_public_ips 的私网与 CGNAT
解析拒绝、_public_ip_rejection_reason 的公网判定与 _PublicIpPinningResolver 的
IP 钉扎。用 fake loop 模拟 DNS 解析，不依赖真实网络。
"""

import asyncio
import ipaddress
from unittest.mock import AsyncMock

import pytest

from seedream_mcp.utils.io.io_download import (
    DownloadError,
    DownloadManager,
    _PublicIpPinningResolver,
    _public_ip_rejection_reason,
)

from _download_fakes import _FakeLoop


def test_validate_url_static_rejects_localhost() -> None:
    with pytest.raises(DownloadError, match="本地主机"):
        DownloadManager()._validate_url_static("http://localhost/x.png")


def test_validate_url_static_rejects_local_suffix() -> None:
    with pytest.raises(DownloadError, match="本地主机"):
        DownloadManager()._validate_url_static("http://myhost.local/x.png")


def test_validate_url_static_rejects_private_ip() -> None:
    with pytest.raises(DownloadError, match="不安全的IP地址"):
        DownloadManager()._validate_url_static("http://192.168.1.1/x.png")


def test_validate_url_static_rejects_loopback_ip() -> None:
    with pytest.raises(DownloadError, match="不安全的IP地址"):
        DownloadManager()._validate_url_static("http://127.0.0.1/x.png")


def test_validate_url_static_rejects_ipv6_loopback_literal() -> None:
    """IPv6 回环字面量经 urlparse 方括号剥离后命中非公网判定，URL 级静态拒绝。"""
    with pytest.raises(DownloadError, match="不安全的IP地址"):
        DownloadManager()._validate_url_static("http://[::1]/x.png")


def test_validate_url_static_rejects_ipv4_mapped_ipv6_literal() -> None:
    """IPv4-mapped 段内嵌私网 10.0.0.1 的 IPv6 字面量须在 URL 级静态拒绝。"""
    with pytest.raises(DownloadError, match="不安全的IP地址"):
        DownloadManager()._validate_url_static("http://[::ffff:10.0.0.1]/x.png")


def test_validate_url_static_rejects_nat64_embedded_private_ipv6_literal() -> None:
    """NAT64 段内嵌私网 192.168.1.1 的 IPv6 字面量须在 URL 级静态拒绝。

    64:ff9b:: 前缀本身可全局路由，须递归校验内嵌 IPv4 才能识别私网目标。
    """
    with pytest.raises(DownloadError, match="不安全的IP地址"):
        DownloadManager()._validate_url_static("http://[64:ff9b::c0a8:101]/x.png")


def test_validate_url_static_rejects_credentials_in_url() -> None:
    with pytest.raises(DownloadError, match="账号或密码"):
        DownloadManager()._validate_url_static("http://user:pass@host/x.png")


def test_validate_url_static_rejects_non_http_scheme() -> None:
    with pytest.raises(DownloadError, match="URL协议"):
        DownloadManager()._validate_url_static("ftp://host/x.png")


def test_validate_url_static_rejects_missing_host() -> None:
    with pytest.raises(DownloadError, match="主机名"):
        DownloadManager()._validate_url_static("http:///path")


def test_validate_url_static_allows_public_ip_without_dns() -> None:
    host, needs_dns = DownloadManager()._validate_url_static("http://8.8.8.8/x.png")
    assert host == "8.8.8.8"
    assert needs_dns is False


def test_validate_url_static_requires_dns_for_hostname() -> None:
    host, needs_dns = DownloadManager()._validate_url_static("http://example.com/x.png")
    assert host == "example.com"
    assert needs_dns is True


async def test_resolve_public_ips_rejects_private_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNS 解析到私网 IP 时必须拒绝，封堵 DNS-rebinding 前置门禁。"""
    manager = DownloadManager()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _FakeLoop(["192.168.0.1"]))

    with pytest.raises(DownloadError, match="非公网"):
        await manager._resolve_public_ips("evil.example.com")


async def test_resolve_public_ips_accepts_public_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = DownloadManager()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _FakeLoop(["8.8.8.8", "1.1.1.1"]))

    ips = await manager._resolve_public_ips("ok.example.com")
    assert set(ips) == {"1.1.1.1", "8.8.8.8"}


def test_validate_url_static_rejects_cgnat_ip() -> None:
    """RFC 6598 CGNAT 段 100.64.0.0/10 不被 ipaddress.is_global 排除，须显式拒绝。"""
    with pytest.raises(DownloadError, match="CGNAT"):
        DownloadManager()._validate_url_static("http://100.64.0.1/x.png")


async def test_resolve_public_ips_rejects_cgnat_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = DownloadManager()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _FakeLoop(["100.64.10.20"]))

    with pytest.raises(DownloadError, match="CGNAT"):
        await manager._resolve_public_ips("cgnat.example.com")


def test_public_ip_rejection_rejects_nat64_embedded_private() -> None:
    """NAT64 段内嵌私有 IPv4 须拒绝，is_global 无法识别须递归校验。"""
    ip = ipaddress.ip_address("64:ff9b::192.168.0.1")
    assert _public_ip_rejection_reason(ip) is not None


def test_public_ip_rejection_rejects_ipv4_compat_embedded_private() -> None:
    """IPv4-compatible 段内嵌私有 IPv4 须拒绝。"""
    ip = ipaddress.ip_address("::192.168.0.1")
    assert _public_ip_rejection_reason(ip) is not None


def test_public_ip_rejection_accepts_nat64_embedded_public() -> None:
    """NAT64 段内嵌公网 IPv4 须放行。"""
    ip = ipaddress.ip_address("64:ff9b::8.8.8.8")
    assert _public_ip_rejection_reason(ip) is None


def test_public_ip_rejection_rejects_6to4_address() -> None:
    """6to4 段 2002::/16 的地址可封装任意 IPv4 路由，须拒绝。

    内嵌的 203.0.113.0 虽为公网文档段，但 6to4 隧道允许封装内网路由。
    """
    ip = ipaddress.ip_address("2002:cb00:7100::1")
    assert _public_ip_rejection_reason(ip) is not None


def test_public_ip_rejection_rejects_ipv4_mapped_embedded_private() -> None:
    """IPv4-mapped 段 ::ffff:0:0/96 内嵌私网 IPv4 须拒绝。"""
    ip = ipaddress.ip_address("::ffff:192.168.0.1")
    assert _public_ip_rejection_reason(ip) is not None


# ==================== _PublicIpPinningResolver：resolve-and-pin ====================


async def test_public_ip_pinning_resolver_returns_only_public_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve 只返回校验通过的公网 IP，hostname 保留原 host 维护 SNI。"""
    manager = DownloadManager()
    monkeypatch.setattr(manager, "_resolve_public_ips", AsyncMock(return_value=("203.0.113.5",)))

    resolver = _PublicIpPinningResolver(manager)
    results = await resolver.resolve(host="example.com", port=443)

    assert len(results) == 1
    assert results[0]["host"] == "203.0.113.5"
    assert results[0]["hostname"] == "example.com"


async def test_public_ip_pinning_resolver_preserves_hostname_for_multiple_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多个公网 IP 均返回，每条 hostname 保留为原 host，供 TLS SNI 与证书校验使用。"""
    manager = DownloadManager()
    monkeypatch.setattr(
        manager,
        "_resolve_public_ips",
        AsyncMock(return_value=("203.0.113.5", "198.51.100.7")),
    )

    resolver = _PublicIpPinningResolver(manager)
    results = await resolver.resolve(host="example.com", port=443)

    assert len(results) == 2
    assert {r["host"] for r in results} == {"203.0.113.5", "198.51.100.7"}
    assert all(r["hostname"] == "example.com" for r in results)


async def test_public_ip_pinning_resolver_no_entries_when_no_public_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_public_ips 返回空时 resolve 不返回可用条目，私网与非法 IP 无从连接。"""
    manager = DownloadManager()
    monkeypatch.setattr(manager, "_resolve_public_ips", AsyncMock(return_value=()))

    resolver = _PublicIpPinningResolver(manager)
    results = await resolver.resolve(host="example.com", port=443)

    assert results == []


async def test_public_ip_pinning_resolver_propagates_private_ip_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_public_ips 拒绝私网解析时 resolve 不返回可用条目，错误向上传播。"""
    manager = DownloadManager()
    monkeypatch.setattr(
        manager,
        "_resolve_public_ips",
        AsyncMock(side_effect=DownloadError("域名解析到不安全地址(非公网地址)")),
    )

    resolver = _PublicIpPinningResolver(manager)
    with pytest.raises(DownloadError, match="非公网"):
        await resolver.resolve(host="example.com", port=443)
