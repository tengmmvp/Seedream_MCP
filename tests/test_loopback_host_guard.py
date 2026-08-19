"""_LoopbackHostGuardMiddleware 的 Host 头校验测试，守护回环绑定下的 DNS rebinding 防线。

覆盖 http 与 websocket 的回环放行、外部 Host 拒绝与 Host 缺失 fail-closed。
"""

from __future__ import annotations

from typing import Any

import pytest

from seedream_mcp.transport import _LoopbackHostGuardMiddleware


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


class _MessageSink:
    """收集 ASGI send 消息，供断言短路响应状态码与 websocket 关闭码。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def status(self) -> int | None:
        for message in self.messages:
            if message.get("type") == "http.response.start":
                return int(message["status"])
        return None

    def websocket_close_code(self) -> int | None:
        for message in self.messages:
            if message.get("type") == "websocket.close":
                return int(message["code"])
        return None


class _InnerApp:
    """记录是否被放行调用的内层应用替身。"""

    def __init__(self) -> None:
        self.called_scopes: list[dict[str, Any]] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called_scopes.append(scope)


def _http_scope(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}


def _websocket_scope(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {"type": "websocket", "path": "/mcp", "headers": headers}


@pytest.mark.parametrize(
    "host",
    [
        b"127.0.0.1",
        b"127.0.0.1:8000",
        b"localhost",
        b"localhost:8000",
        b"[::1]",
        b"[::1]:8000",
    ],
)
async def test_guard_allows_loopback_hosts(host: bytes) -> None:
    """回环 Host（含带端口与 IPv6 方括号形态）放行到内层应用，不产生 403。"""
    inner = _InnerApp()
    sink = _MessageSink()
    guard = _LoopbackHostGuardMiddleware(inner)

    await guard(_http_scope([(b"host", host)]), _noop_receive, sink)

    assert len(inner.called_scopes) == 1
    assert sink.status() is None


@pytest.mark.parametrize(
    "host",
    [
        b"evil.example.com",
        b"evil.example.com:8000",
        b"192.168.1.5",
        b"[fe80::1]",
        b"sub.localhost.evil.com",
    ],
)
async def test_guard_rejects_non_loopback_hosts(host: bytes) -> None:
    """外部域名与私网 Host 头一律 403 拒绝，阻断 DNS rebinding 同源请求。"""
    inner = _InnerApp()
    sink = _MessageSink()
    guard = _LoopbackHostGuardMiddleware(inner)

    await guard(_http_scope([(b"host", host)]), _noop_receive, sink)

    assert inner.called_scopes == []
    assert sink.status() == 403


@pytest.mark.parametrize("host", [b"LOCALHOST", b"LOCALHOST:8000", b"EVIL.EXAMPLE.COM"])
async def test_guard_rejects_uppercase_host_forms(host: bytes) -> None:
    """Host 比较为大小写敏感口径，大写回环 Host 同样 403 拒绝。

    与 SDK 内层 Host 校验同为精确比较，大写回环 Host 在内层也不匹配回环白名单，
    本层拒绝不产生内外层判定分歧。
    """
    inner = _InnerApp()
    sink = _MessageSink()
    guard = _LoopbackHostGuardMiddleware(inner)

    await guard(_http_scope([(b"host", host)]), _noop_receive, sink)

    assert inner.called_scopes == []
    assert sink.status() == 403


async def test_guard_rejects_missing_host_header() -> None:
    """Host 头缺失（HTTP/1.0 等路径）按 403 fail-closed，不留免校验放行缺口。"""
    inner = _InnerApp()
    sink = _MessageSink()
    guard = _LoopbackHostGuardMiddleware(inner)

    await guard(_http_scope([]), _noop_receive, sink)

    assert inner.called_scopes == []
    assert sink.status() == 403


@pytest.mark.parametrize("host", [b"evil.example.com", b"evil.example.com:8000", b"192.168.1.5"])
async def test_guard_closes_websocket_with_non_loopback_host(host: bytes) -> None:
    """websocket scope 携带外部 Host 时以 1008 关闭，不进入内层应用。

    websocket 无状态码可回，参照 Bearer 鉴权中间件关闭握手；不校验会让 rebinding
    借 websocket 通道绕过 Host 防线。
    """
    inner = _InnerApp()
    sink = _MessageSink()
    guard = _LoopbackHostGuardMiddleware(inner)

    await guard(_websocket_scope([(b"host", host)]), _noop_receive, sink)

    assert inner.called_scopes == []
    assert sink.websocket_close_code() == 1008
    assert sink.status() is None


async def test_guard_closes_websocket_with_missing_host_header() -> None:
    """websocket 缺失 Host 头同样 fail-closed 关闭，与 http 路径取向一致。"""
    inner = _InnerApp()
    sink = _MessageSink()
    guard = _LoopbackHostGuardMiddleware(inner)

    await guard(_websocket_scope([]), _noop_receive, sink)

    assert inner.called_scopes == []
    assert sink.websocket_close_code() == 1008


@pytest.mark.parametrize("host", [b"127.0.0.1:8000", b"localhost", b"[::1]"])
async def test_guard_allows_websocket_with_loopback_host(host: bytes) -> None:
    """websocket 携带回环 Host 正常放行，本机客户端不受影响。"""
    inner = _InnerApp()
    sink = _MessageSink()
    guard = _LoopbackHostGuardMiddleware(inner)

    await guard(_websocket_scope([(b"host", host)]), _noop_receive, sink)

    assert len(inner.called_scopes) == 1
    assert sink.websocket_close_code() is None


async def test_guard_passes_through_lifespan_scope() -> None:
    """lifespan scope 原样透传，Host 校验仅作用于 http 与 websocket 流量。"""
    inner = _InnerApp()
    sink = _MessageSink()
    guard = _LoopbackHostGuardMiddleware(inner)
    scope = {"type": "lifespan", "headers": [(b"host", b"evil.example.com")]}

    await guard(scope, _noop_receive, sink)

    assert len(inner.called_scopes) == 1
    assert sink.status() is None
