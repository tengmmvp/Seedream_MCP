import asyncio

import pytest

from seedream_mcp.utils.download_manager import DownloadError, DownloadManager


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


class _FakeTransport:
    def __init__(self, peer_ip: str) -> None:
        self._peer_ip = peer_ip

    def get_extra_info(self, key: str):  # type: ignore[no-untyped-def]
        if key == "peername":
            return (self._peer_ip, 443)
        return None


class _FakeConnection:
    def __init__(self, peer_ip: str) -> None:
        self.transport = _FakeTransport(peer_ip)


class _FakeResponse:
    def __init__(self, peer_ip: str) -> None:
        self.connection = _FakeConnection(peer_ip)


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
    fake_response = _FakeResponse("127.0.0.1")

    with pytest.raises(DownloadError, match="非公网地址"):
        manager._validate_connected_peer_ip(  # type: ignore[arg-type]
            fake_response, "https://example.com"
        )


def test_validate_connected_peer_ip_allows_public_ip() -> None:
    manager = DownloadManager()
    fake_response = _FakeResponse("8.8.8.8")

    manager._validate_connected_peer_ip(  # type: ignore[arg-type]
        fake_response, "https://example.com"
    )
