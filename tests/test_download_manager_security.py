"""下载安全测试：DNS 解析 TTL 缓存与连接后对端 IP 公网校验。"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from seedream_mcp.utils.download_manager import DownloadError, DownloadManager

from _download_fakes import (
    _FakeResponse,
    _FakeSession,
    _PNG_BYTES,
    _TimeoutThenSuccessSession,
    _patch_download_network,
)


class _FakeLoop:
    def __init__(self) -> None:
        self.calls = 0

    async def getaddrinfo(self, host, port, proto):  # type: ignore[no-untyped-def]
        del host, port, proto
        self.calls += 1
        return [
            (None, None, None, None, ("8.8.8.8", 0)),
            (None, None, None, None, ("1.1.1.1", 0)),
        ]


@pytest.mark.asyncio
async def test_validate_public_dns_uses_ttl_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_loop = _FakeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    manager = DownloadManager(dns_cache_ttl=60)
    await manager._validate_public_dns("example.com")
    await manager._validate_public_dns("example.com")

    assert fake_loop.calls == 1


def test_validate_connected_peer_ip_blocks_non_public_ip() -> None:
    manager = DownloadManager()
    fake_response = _FakeResponse(peer_ip="127.0.0.1")

    with pytest.raises(DownloadError, match="非公网地址"):
        manager._validate_connected_peer_ip(  # type: ignore[arg-type]
            fake_response, "https://example.com"
        )


def test_validate_connected_peer_ip_allows_public_ip() -> None:
    manager = DownloadManager()
    fake_response = _FakeResponse(peer_ip="8.8.8.8")

    manager._validate_connected_peer_ip(  # type: ignore[arg-type]
        fake_response, "https://example.com"
    )


# ---- download_image 端到端：逐跳重定向校验与重试退避 ----
# mock 网络层，验证把各 SSRF 子组件串联起来的 download_image 主循环：
# 重定向目标须重新走静态校验、重定向上限、5xx 退避重试后成功落盘。


@pytest.mark.asyncio
async def test_download_image_rejects_redirect_to_private_ips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """302 跳转至元数据服务内网地址须被逐跳静态校验拒绝。"""
    manager = DownloadManager()
    session = _FakeSession(
        [_FakeResponse(302, {"location": "http://169.254.169.254/latest/meta-data/"})]
    )
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_redirect_to_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """302 跳转至回环地址须被逐跳静态校验拒绝。"""
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "http://127.0.0.1/"})])
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_redirect_to_private_ip_via_real_static_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端串联：保留真实 _validate_url_for_request，逐跳重定向到内网 IP 须被静态校验拒绝。

    上述两条重定向用例经 _patch_download_network 把 _validate_url_for_request 架空为直通，
    实测的是重定向上限而非安全拒绝。本用例仅 stub 依赖网络的 DNS 解析与 session 注入，
    保留真实的 _validate_url_for_request 串联，使 302 目标 169.254.169.254 经
    _validate_url_static 命中非公网判定被拒绝，覆盖 SSRF 第四层防护的端到端安全语义。
    """
    manager = DownloadManager()
    session = _FakeSession(
        [_FakeResponse(302, {"location": "http://169.254.169.254/latest/meta-data/"})]
    )

    async def _pass_dns(host: str) -> None:
        del host

    async def _fake_ensure_session() -> Any:
        return session

    monkeypatch.setattr(manager, "_validate_public_dns", _pass_dns)
    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)

    with pytest.raises(DownloadError, match="不安全|非公网"):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_redirect_to_loopback_via_real_static_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端串联：302 跳转至回环地址须被真实 _validate_url_static 拒绝。"""
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "http://127.0.0.1/"})])

    async def _pass_dns(host: str) -> None:
        del host

    async def _fake_ensure_session() -> Any:
        return session

    monkeypatch.setattr(manager, "_validate_public_dns", _pass_dns)
    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)

    with pytest.raises(DownloadError, match="不安全|非公网"):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_excessive_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过 3 跳的重定向链须被拒绝。"""
    manager = DownloadManager()
    redirects = [_FakeResponse(302, {"location": f"https://example.com/r{i}"}) for i in range(5)]
    session = _FakeSession(redirects)
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError, match="重定向次数过多"):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_retries_5xx_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """连续 5xx 后成功：验证退避重试串联与最终原子落盘。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _FakeResponse(500, {}),
            _FakeResponse(500, {}),
            _FakeResponse(200, {"content-type": "image/png"}, content_chunks=[_PNG_BYTES]),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.exists()
    assert save_path.read_bytes() == _PNG_BYTES


@pytest.mark.asyncio
async def test_download_image_exhausts_retries_then_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """所有尝试均返回 5xx → 退避重试用尽后抛出 last_error，文件未落盘。"""
    manager = DownloadManager()
    # _FakeSession 超出序列后重复返回最后一个响应，故所有尝试均为 500
    session = _FakeSession([_FakeResponse(500, {})])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError):
        await manager.download_image("https://example.com/img.png", save_path)

    assert not save_path.exists()
    # 默认 max_retries=3，共首次 + 3 次重试 = 4 次尝试
    assert session._idx == manager.max_retries + 1


@pytest.mark.asyncio
async def test_download_image_retries_timeout_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """首次下载超时经退避重试后成功：覆盖 asyncio.TimeoutError except 臂的重试路径。"""
    manager = DownloadManager()
    success = _FakeResponse(200, {"content-type": "image/png"}, content_chunks=[_PNG_BYTES])
    session = _TimeoutThenSuccessSession(success)
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.exists()
    assert save_path.read_bytes() == _PNG_BYTES
    assert session.call_count == 2
