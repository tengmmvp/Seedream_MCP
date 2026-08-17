"""io_download 下载测试共享的伪网络对象与辅助函数。

供 test_download_manager_security、test_download_image_branches 与
test_auto_save_and_download 复用，避免多处重复定义与语义漂移。流式内容采用多分块
语义，支持单块与多块场景；对端 IP、连接、响应、session 均按 aiohttp 公开接口的
最小子集模拟。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from seedream_mcp.utils.io.io_download import DownloadManager

# 合法 PNG 魔法字节，供成功路径与签名校验对照
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


class _FakeStreamContent:
    """模拟 aiohttp 响应体的分块流，逐块产出。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def iter_chunked(self, size: int):  # type: ignore[no-untyped-def]
        del size
        for chunk in self._chunks:
            yield chunk


class _FakeTransport:
    """模拟底层传输，供 _validate_connected_peer_ip 提取公网对端 IP。"""

    def __init__(self, peer_ip: str = "8.8.8.8") -> None:
        self._peer_ip = peer_ip

    def get_extra_info(self, key: str) -> Any:
        if key == "peername":
            return (self._peer_ip, 443)
        return None


class _FakeConnection:
    def __init__(self, peer_ip: str = "8.8.8.8") -> None:
        self.transport = _FakeTransport(peer_ip)


class _FakeResponse:
    """模拟 aiohttp.ClientResponse：暴露 status/headers/content/connection。

    实现异步上下文管理器协议以支持 ``async with session.get(...) as response``。
    content 经多分块流产出；connection 供 download_image 主循环的对端 IP 复核。
    """

    def __init__(
        self,
        status: int = 200,
        headers: dict | None = None,
        content_chunks: list[bytes] | None = None,
        peer_ip: str = "8.8.8.8",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = _FakeStreamContent(content_chunks or [])
        self.connection = _FakeConnection(peer_ip)

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeSession:
    """按序返回预设响应序列，超出后重复返回最后一个。"""

    def __init__(self, responses: list) -> None:
        self._responses = responses
        self._idx = 0

    def get(self, url: str, **kwargs: object):  # type: ignore[no-untyped-def]
        del url
        # SSRF 防护：download_image 须显式传 allow_redirects=False 以逐跳手动校验，
        # 断言该参数防止退化为自动跟随重定向绕过安全校验
        assert kwargs.get("allow_redirects") is False, "allow_redirects 必须为 False"
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


class _RaisingThenSuccessSession:
    """首次 get 抛指定异常，之后返回预设成功响应。

    覆盖 download_image 各 except 臂的重试路径：get 直接抛出而非经上下文管理器，
    等效模拟网络层在建立连接前即失败的场景，异常仍被同一 except 臂捕获。
    """

    def __init__(self, exc: BaseException, success_response: _FakeResponse) -> None:
        self._exc = exc
        self._success = success_response
        self.call_count = 0

    def get(self, url: str, **kwargs: object) -> _FakeResponse:  # type: ignore[no-untyped-def]
        del url
        assert kwargs.get("allow_redirects") is False, "allow_redirects 必须为 False"
        self.call_count += 1
        if self.call_count == 1:
            raise self._exc
        return self._success


class _TimeoutThenSuccessSession:
    """首次 get 抛出 asyncio.TimeoutError，之后返回预设成功响应。

    覆盖 download_image 中 ``except asyncio.TimeoutError`` 重试分支：连接或读取阶段
    超时经退避后由后续尝试成功落盘。
    """

    def __init__(self, success_response: _FakeResponse) -> None:
        self._success = success_response
        self.call_count = 0

    def get(self, url: str, **kwargs: object) -> _FakeResponse:  # type: ignore[no-untyped-def]
        del url
        assert kwargs.get("allow_redirects") is False, "allow_redirects 必须为 False"
        self.call_count += 1
        if self.call_count == 1:
            raise asyncio.TimeoutError()
        return self._success


def _patch_download_network(
    monkeypatch: pytest.MonkeyPatch, manager: DownloadManager, session: Any
) -> None:
    """跳过依赖真实网络的 URL 校验并注入 fake session，聚焦主循环与分支逻辑。

    _validate_connected_peer_ip 不 mock：fake response 携带公网 peer_ip，真实运行以
    覆盖成功路径中对端 IP 复核的串联。
    """

    async def _pass_url(url: str) -> None:
        del url

    async def _fake_ensure_session() -> Any:
        return session

    monkeypatch.setattr(manager, "_validate_url_for_request", _pass_url)
    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)


def _png_success_response() -> _FakeResponse:
    """构造合法 200 PNG 响应，供重试后成功落盘路径使用。"""
    return _FakeResponse(
        status=200,
        headers={"content-type": "image/png", "content-length": str(len(_PNG_BYTES))},
        content_chunks=[_PNG_BYTES],
    )
