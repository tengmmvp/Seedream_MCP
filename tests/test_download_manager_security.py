"""下载安全测试：DNS 解析 TTL 缓存与连接后对端 IP 公网校验。"""

import asyncio
from pathlib import Path

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


# ---- download_image 端到端：逐跳重定向校验与重试退避 ----
# mock 网络层，验证把各 SSRF 子组件串联起来的 download_image 主循环：
# 重定向目标须重新走静态校验、重定向上限、5xx 退避重试后成功落盘。

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


class _FakeStreamContent:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def iter_chunked(self, size: int):  # type: ignore[no-untyped-def]
        del size
        if self._data:
            yield self._data


class _FakeDownloadResponse:
    def __init__(
        self,
        status: int,
        headers: dict,
        *,
        peer_ip: str = "8.8.8.8",
        content: bytes = b"",
    ) -> None:
        self.status = status
        self.headers = headers
        self.connection = _FakeConnection(peer_ip)
        self.content = _FakeStreamContent(content)

    async def __aenter__(self) -> "_FakeDownloadResponse":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeSession:
    """按序返回预设响应序列，超出后重复返回最后一个。"""

    def __init__(self, responses: list) -> None:
        self._responses = responses
        self._idx = 0

    def get(self, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        del url, kwargs
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


def _patch_download_network(
    monkeypatch: pytest.MonkeyPatch, manager: DownloadManager, session: _FakeSession
) -> None:
    """跳过依赖真实网络的 DNS 校验并注入 fake session，聚焦主循环串联逻辑。

    _validate_connected_peer_ip 不 mock：fake response 携带公网 peer_ip，真实运行以
    覆盖"重定向与响应分支前均做对端 IP 复核"的串联路径。
    """

    async def _pass_url(url: str) -> None:
        del url

    async def _fake_ensure_session() -> _FakeSession:
        return session

    monkeypatch.setattr(manager, "_validate_url_for_request", _pass_url)
    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)


@pytest.mark.asyncio
async def test_download_image_rejects_redirect_to_private_ip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """302 跳转至元数据服务内网地址须被逐跳静态校验拒绝。"""
    manager = DownloadManager()
    session = _FakeSession(
        [_FakeDownloadResponse(302, {"location": "http://169.254.169.254/latest/meta-data/"})]
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
    session = _FakeSession([_FakeDownloadResponse(302, {"location": "http://127.0.0.1/"})])
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_excessive_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过 3 跳的重定向链须被拒绝。"""
    manager = DownloadManager()
    redirects = [
        _FakeDownloadResponse(302, {"location": f"https://example.com/r{i}"}) for i in range(5)
    ]
    session = _FakeSession(redirects)
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError, match="重定向次数过多"):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_retries_5xx_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连续 5xx 后成功：验证退避重试串联与最终原子落盘。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _FakeDownloadResponse(500, {}),
            _FakeDownloadResponse(500, {}),
            _FakeDownloadResponse(200, {"content-type": "image/png"}, content=_PNG_BYTES),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    async def _no_sleep(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.exists()
    assert save_path.read_bytes() == _PNG_BYTES


class _TimeoutThenSuccessSession:
    """首次 get 抛出 asyncio.TimeoutError，之后返回预设成功响应。

    覆盖 download_image 中 ``except asyncio.TimeoutError`` 重试分支：连接/读取阶段
    超时经退避后由后续尝试成功落盘。get 直接抛出而非经上下文管理器，等效模拟网络层
    在建立连接前即超时的场景，异常仍被同一 except 臂捕获。
    """

    def __init__(self, success_response: "_FakeDownloadResponse") -> None:
        self._success = success_response
        self.call_count = 0

    def get(self, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        del url, kwargs
        self.call_count += 1
        if self.call_count == 1:
            raise asyncio.TimeoutError()
        return self._success


@pytest.mark.asyncio
async def test_download_image_exhausts_retries_then_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """所有尝试均返回 5xx → 退避重试用尽后抛出 last_error，文件未落盘。"""
    manager = DownloadManager()
    # _FakeSession 超出序列后重复返回最后一个响应，故所有尝试均为 500
    session = _FakeSession([_FakeDownloadResponse(500, {})])
    _patch_download_network(monkeypatch, manager, session)

    async def _no_sleep(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError):
        await manager.download_image("https://example.com/img.png", save_path)

    assert not save_path.exists()
    # 默认 max_retries=3，共首次 + 3 次重试 = 4 次尝试
    assert session._idx == manager.max_retries + 1


@pytest.mark.asyncio
async def test_download_image_retries_timeout_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次下载超时经退避重试后成功：覆盖 asyncio.TimeoutError except 臂的重试路径。"""
    manager = DownloadManager()
    success = _FakeDownloadResponse(200, {"content-type": "image/png"}, content=_PNG_BYTES)
    session = _TimeoutThenSuccessSession(success)
    _patch_download_network(monkeypatch, manager, session)

    async def _no_sleep(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.exists()
    assert save_path.read_bytes() == _PNG_BYTES
    assert session.call_count == 2
