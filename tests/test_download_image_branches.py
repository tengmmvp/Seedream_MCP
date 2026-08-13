"""download_image 重试分类与 _download_response_to_temp 内部分支测试。

覆盖 download_image 的可重试/终态错误分类（aiohttp.ClientError/OSError 重试，
泛 Exception 不重试），以及 _download_response_to_temp 的 content-length 解析失败、
流式累计超限、字节签名不匹配、fd 泄漏兜底四条分支。用 fake session/response 模拟，
不依赖真实网络。
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any, List

import aiofiles
import aiohttp
import pytest

from seedream_mcp.utils import download_manager as dm_module
from seedream_mcp.utils.download_manager import DownloadError, DownloadManager

# 合法 PNG 魔法字节，供成功路径与签名校验对照
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


# ==================== fake 响应与 session ====================


class _FakeStreamContent:
    """模拟 aiohttp 响应体的分块流。"""

    def __init__(self, chunks: List[bytes]) -> None:
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


class _FakeResp:
    """模拟 aiohttp.ClientResponse：暴露 status/headers/content/connection。

    实现异步上下文管理器协议以支持 ``async with session.get(...) as response``。
    content/connection 仅在经 download_image 主流程时被访问，直接调用
    _download_response_to_temp 时仅用 headers 与 content。
    """

    def __init__(
        self,
        status: int = 200,
        headers: dict | None = None,
        content_chunks: List[bytes] | None = None,
        peer_ip: str = "8.8.8.8",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = _FakeStreamContent(content_chunks or [])
        self.connection = _FakeConnection(peer_ip)

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _RaisingThenSuccessSession:
    """首次 get 抛指定异常，之后返回预设成功响应。

    覆盖 download_image 各 except 臂的重试路径：get 直接抛出而非经上下文管理器，
    等效模拟网络层在建立连接前即失败的场景，异常仍被同一 except 臂捕获。
    """

    def __init__(self, exc: BaseException, success_response: _FakeResp) -> None:
        self._exc = exc
        self._success = success_response
        self.call_count = 0

    def get(self, url: str, **kwargs: object) -> _FakeResp:  # type: ignore[no-untyped-def]
        del url, kwargs
        self.call_count += 1
        if self.call_count == 1:
            raise self._exc
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


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(asyncio, "sleep", _sleep)


def _png_success_response() -> _FakeResp:
    """构造合法 200 PNG 响应，供重试后成功落盘路径使用。"""
    return _FakeResp(
        status=200,
        headers={"content-type": "image/png", "content-length": str(len(_PNG_BYTES))},
        content_chunks=[_PNG_BYTES],
    )


# ==================== download_image 重试分类 ====================


async def test_download_image_retries_on_aiohttp_client_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """aiohttp.ClientError 经退避重试，首次失败后第二次成功落盘。"""
    _no_sleep(monkeypatch)
    manager = DownloadManager()
    session = _RaisingThenSuccessSession(
        aiohttp.ClientError("connection reset"), _png_success_response()
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.exists()
    assert save_path.read_bytes() == _PNG_BYTES
    assert session.call_count == 2


async def test_download_image_retries_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError 文件系统错误经退避重试，首次失败后第二次成功落盘。"""
    _no_sleep(monkeypatch)
    manager = DownloadManager()
    session = _RaisingThenSuccessSession(
        OSError("no space left on device"), _png_success_response()
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.read_bytes() == _PNG_BYTES
    assert session.call_count == 2


async def test_download_image_does_not_retry_generic_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非可重试的意外错误立即抛出，不浪费退避等待。"""
    _no_sleep(monkeypatch)
    manager = DownloadManager()
    session = _RaisingThenSuccessSession(ValueError("unexpected bug"), _png_success_response())
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(ValueError):
        await manager.download_image("https://example.com/img.png", save_path)

    # 仅尝试一次，原样抛出不重试
    assert session.call_count == 1
    assert not save_path.exists()


# ==================== _download_response_to_temp 内部分支 ====================


async def test_download_response_rejects_invalid_content_length(tmp_path: Path) -> None:
    """content-length 非整数 → DownloadError，不进入文件写入阶段。"""
    manager = DownloadManager()
    response = _FakeResp(headers={"content-type": "image/png", "content-length": "not-a-number"})
    temp_path = manager._temp_path_for(tmp_path / "out.png")

    with pytest.raises(DownloadError, match="非法 content-length"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_path,
            "image/png",
            0,
            time.monotonic(),
        )

    # 校验失败发生在 mkdir 与文件创建之前，临时文件不应存在
    assert not temp_path.exists()


async def test_download_response_rejects_oversized_content_length(tmp_path: Path) -> None:
    """content-length 超 max_file_size → DownloadError 早拒。"""
    manager = DownloadManager(max_file_size=100)
    response = _FakeResp(headers={"content-type": "image/png", "content-length": "101"})
    temp_path = manager._temp_path_for(tmp_path / "out.png")

    with pytest.raises(DownloadError, match="文件过大"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_path,
            "image/png",
            0,
            time.monotonic(),
        )


async def test_download_response_rejects_streaming_cumulative_oversize(
    tmp_path: Path,
) -> None:
    """无 content-length 时按流式累计字节数，超 max_file_size → DownloadError。"""
    manager = DownloadManager(max_file_size=100)
    # 两块各 60 字节，累计 120 超过上限 100
    response = _FakeResp(
        headers={"content-type": "image/png"}, content_chunks=[b"x" * 60, b"x" * 60]
    )
    temp_path = manager._temp_path_for(tmp_path / "out.png")

    with pytest.raises(DownloadError, match="文件过大"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_path,
            "image/png",
            0,
            time.monotonic(),
        )


async def test_download_response_rejects_byte_signature_mismatch(tmp_path: Path) -> None:
    """Content-Type 声称 image/png 但字节签名非受支持图片格式 → DownloadError。

    防御 Content-Type 伪造使非图片或可执行内容落盘。
    """
    manager = DownloadManager()
    response = _FakeResp(
        headers={"content-type": "image/png", "content-length": "13"},
        content_chunks=[b"NOT_AN_IMAGE!"],
    )
    temp_path = manager._temp_path_for(tmp_path / "out.png")

    with pytest.raises(DownloadError, match="字节签名"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_path,
            "image/png",
            0,
            time.monotonic(),
        )

    # 签名校验失败发生在 replace 之前，最终文件不应落盘
    assert not (tmp_path / "out.png").exists()


async def test_download_response_closes_fd_when_aiofiles_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """aiofiles.open 接管 fd 前抛错时，finally 兜底 os.close 关闭 fd 避免泄漏。

    open_no_follow_fd 已成功返回 fd，但 aiofiles.open 包装该 fd 时失败：fd_handed_off
    仍为 False，finally 分支手动 os.close(fd)。此处追踪 os.close 调用以确认兜底生效。
    """
    manager = DownloadManager()
    response = _FakeResp(
        headers={"content-type": "image/png", "content-length": str(len(_PNG_BYTES))},
        content_chunks=[_PNG_BYTES],
    )
    temp_path = manager._temp_path_for(tmp_path / "out.png")

    closed_fds: List[int] = []
    real_os_close = os.close

    def _tracking_close(fd: int) -> None:
        closed_fds.append(fd)
        real_os_close(fd)

    class _RaisingAiofilesCtx:
        async def __aenter__(self) -> None:
            raise OSError("aiofiles open boom")

        async def __aexit__(self, *args: object) -> None:
            return None

    def _raising_aiofiles_open(*args: object, **kwargs: object) -> Any:
        return _RaisingAiofilesCtx()

    monkeypatch.setattr(dm_module.os, "close", _tracking_close)
    monkeypatch.setattr(aiofiles, "open", _raising_aiofiles_open)

    with pytest.raises(OSError, match="aiofiles open boom"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_path,
            "image/png",
            0,
            time.monotonic(),
        )

    # finally 兜底已关闭 open_no_follow_fd 产出的 fd，避免泄漏
    assert len(closed_fds) == 1
