"""download_image 重试分类与 _download_response_to_temp 内部分支测试。

用 fake session/response 模拟，不依赖真实网络。
"""

import asyncio
import errno
import os
import socket
import time
from pathlib import Path
from typing import Any, List

import aiofiles
import aiohttp
import pytest

from seedream_mcp.utils.io.io_download import (
    DownloadError,
    DownloadManager,
    RetryableDownloadError,
)

from _download_fakes import (
    _FakeResponse,
    _FakeSession,
    _PNG_BYTES,
    _RaisingThenSuccessSession,
    _patch_download_network,
    _png_success_response,
)

# ==================== download_image 重试分类 ====================


async def test_download_image_retries_on_aiohttp_client_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """aiohttp.ClientError 经退避重试，首次失败后第二次成功落盘。"""
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


@pytest.mark.parametrize("status", [408, 429])
async def test_download_retries_on_rate_limit_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None, status: int
) -> None:
    """408 请求超时与 429 限流属可重试：退避后第二次成功落盘。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _FakeResponse(status=status, headers={"content-type": "text/plain"}),
            _png_success_response(),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.read_bytes() == _PNG_BYTES
    assert session._idx == 2


async def test_download_429_exhausts_retries_as_retryable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """429 持续出现时按可重试耗尽上抛，而非单次终态失败。"""
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(status=429, headers={"content-type": "text/plain"})])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(RetryableDownloadError, match="HTTP错误"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 可重试路径按 max_retries 上限反复尝试
    assert session._idx == manager.max_retries + 1
    assert not save_path.exists()


async def test_download_404_is_terminal_single_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """404 属语义明确的终态错误：立即抛 DownloadError，不进入退避重试。"""
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(status=404)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="HTTP错误") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 终态错误单次尝试即上抛，未触发退避重试
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_redirect_without_location_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """302 重定向响应缺少 Location 头属终态错误：立即抛出且消息注明缺少 Location。"""
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {})])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="缺少 Location"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 重定向语义错误单次尝试即上抛，不进入退避重试
    assert session._idx == 1
    assert not save_path.exists()


async def test_download_sniffs_extension_from_bytes_when_suffix_mismatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """URL 派生扩展名与实际字节不符时按字节签名修正，落盘与结果路径一致。"""
    from _download_fakes import _FakeSession

    manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.jpeg"
    result = await manager.download_image("https://cdn.example.com/signed.jpeg", save_path)

    corrected = tmp_path / "out.png"
    assert result["success"] is True
    assert result["file_path"] == str(corrected)
    assert corrected.exists()
    assert corrected.read_bytes() == _PNG_BYTES
    assert not save_path.exists()


async def test_download_keeps_suffix_when_matches_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """URL 扩展名与字节签名一致时路径不变，不产生多余修正。"""
    manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["file_path"] == str(save_path)
    assert save_path.read_bytes() == _PNG_BYTES


async def test_download_image_retries_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """OSError 文件系统错误经退避重试，首次失败后第二次成功落盘。"""
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """非可重试的意外错误立即抛出，不浪费退避等待。"""
    manager = DownloadManager()
    session = _RaisingThenSuccessSession(ValueError("unexpected bug"), _png_success_response())
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(ValueError):
        await manager.download_image("https://example.com/img.png", save_path)

    # 仅尝试一次，原样抛出不重试
    assert session.call_count == 1
    assert not save_path.exists()


async def test_download_image_does_not_retry_on_disk_quota_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """EDQUOT 磁盘配额超限属永久性错误，立即抛出 DownloadError 不重试。"""
    manager = DownloadManager()
    session = _RaisingThenSuccessSession(
        OSError(errno.EDQUOT, "disk quota exceeded"), _png_success_response()
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="文件系统永久错误"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 配额超限需管理员介入，重试无意义，单次尝试即终态
    assert session.call_count == 1
    assert not save_path.exists()


async def test_download_image_does_not_retry_on_invalid_url_client_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """aiohttp.InvalidUrlClientError 属 URL 语法永久错误，立即抛出不重试。"""
    manager = DownloadManager()
    session = _RaisingThenSuccessSession(
        aiohttp.InvalidUrlClientError("http://bad url"), _png_success_response()
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="无效的URL"):
        await manager.download_image("https://example.com/img.png", save_path)

    # URL 语法错误重试无意义，单次尝试即终态抛出
    assert session.call_count == 1
    assert not save_path.exists()


# ==================== DNS 解析错误分类 ====================


def _patch_loop_getaddrinfo(monkeypatch: pytest.MonkeyPatch, fake_getaddrinfo: Any) -> None:
    """替换当前事件循环的 getaddrinfo，聚焦 _resolve_public_ips_uncached 的分类。"""
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)


async def test_download_retries_on_transient_dns_resolution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """EAI_AGAIN 瞬时解析失败按可重试处理，退避后重新解析并成功落盘。"""
    manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    resolve_calls: List[int] = []

    async def _gaierror_then_success(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        resolve_calls.append(1)
        if len(resolve_calls) == 1:
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]

    _patch_loop_getaddrinfo(monkeypatch, _gaierror_then_success)

    async def _fake_ensure_session() -> Any:
        return session

    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.read_bytes() == _PNG_BYTES
    # 首次解析瞬时失败后未缓存失败结果，第二次重试解析成功并完成下载
    assert len(resolve_calls) == 2


async def test_download_dns_resolving_private_ip_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """解析到私网 IP 属 SSRF 第二层防护的终态拒绝，不进入退避重试。"""
    manager = DownloadManager()
    resolve_calls: List[int] = []

    async def _private_ip_getaddrinfo(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        resolve_calls.append(1)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.1", 0))]

    _patch_loop_getaddrinfo(monkeypatch, _private_ip_getaddrinfo)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="不安全地址"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 终态错误单次尝试即上抛，未触发退避重试
    assert len(resolve_calls) == 1
    assert not save_path.exists()


async def test_download_wsa_host_not_found_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """Windows WSAHOST_NOT_FOUND(11001) 域名不存在属终态：单次解析即上抛不退避。

    Windows 的 getaddrinfo 失败 errno 为 WSA 错误码而非 POSIX EAI_* 常量。
    """
    manager = DownloadManager()
    resolve_calls: List[int] = []

    async def _wsa_11001_getaddrinfo(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        resolve_calls.append(1)
        raise socket.gaierror(11001, "Host not found")

    _patch_loop_getaddrinfo(monkeypatch, _wsa_11001_getaddrinfo)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="域名解析失败") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 终态错误且非可重试子类，单次尝试即上抛，未触发退避重试
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert len(resolve_calls) == 1
    assert not save_path.exists()


async def test_download_wsa_try_again_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """Windows WSATRY_AGAIN(11002) 对应 EAI_AGAIN 瞬时故障：按可重试退避。"""
    manager = DownloadManager()
    resolve_calls: List[int] = []

    async def _wsa_11002_getaddrinfo(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        resolve_calls.append(1)
        raise socket.gaierror(11002, "Non-authoritative host not found")

    _patch_loop_getaddrinfo(monkeypatch, _wsa_11002_getaddrinfo)

    save_path = tmp_path / "out.png"
    with pytest.raises(RetryableDownloadError, match="域名解析失败"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 可重试错误按 max_retries 上限反复解析，而非单次终态上抛
    assert len(resolve_calls) == manager.max_retries + 1
    assert not save_path.exists()


# ==================== _download_response_to_temp 内部分支 ====================


async def test_download_response_rejects_invalid_content_length(tmp_path: Path) -> None:
    """content-length 非整数 → DownloadError，不进入文件写入阶段。"""
    manager = DownloadManager()
    response = _FakeResponse(
        headers={"content-type": "image/png", "content-length": "not-a-number"}
    )
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="非法 content-length"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    # 校验失败发生在 mkdir 与文件创建之前，临时文件不应存在
    assert not list(tmp_path.glob("*.part"))


async def test_download_response_rejects_oversized_content_length(tmp_path: Path) -> None:
    """content-length 超 max_file_size → DownloadError 早拒。"""
    manager = DownloadManager(max_file_size=100)
    response = _FakeResponse(headers={"content-type": "image/png", "content-length": "101"})
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="文件过大"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )


async def test_download_response_rejects_negative_content_length(tmp_path: Path) -> None:
    """content-length 为负值 → DownloadError，不进入文件写入阶段。

    int('-1') 合法但负值无意义，原本 -1 > max_file_size 为 False 会绕过预检查。
    """
    manager = DownloadManager()
    response = _FakeResponse(headers={"content-type": "image/png", "content-length": "-1"})
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="非法 content-length"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    assert not list(tmp_path.glob("*.part"))


async def test_download_response_rejects_streaming_cumulative_oversize(
    tmp_path: Path,
) -> None:
    """无 content-length 时按流式累计字节数，超 max_file_size → DownloadError。"""
    manager = DownloadManager(max_file_size=100)
    # 两块各 60 字节，累计 120 超过上限 100
    response = _FakeResponse(
        headers={"content-type": "image/png"}, content_chunks=[b"x" * 60, b"x" * 60]
    )
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="文件过大"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )


async def test_download_response_rejects_byte_signature_mismatch(tmp_path: Path) -> None:
    """Content-Type 声称 image/png 但字节签名非受支持图片格式 → DownloadError。"""
    manager = DownloadManager()
    response = _FakeResponse(
        headers={"content-type": "image/png", "content-length": "13"},
        content_chunks=[b"NOT_AN_IMAGE!"],
    )
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="字节签名"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    # 签名校验失败发生在 replace 之前，最终文件不应落盘
    assert not (tmp_path / "out.png").exists()


async def test_download_response_closes_fd_when_aiofiles_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """aiofiles.open 包装 fd 失败时，落盘骨架在 finally 中 os.close 兜底，恰一次。"""
    manager = DownloadManager()
    response = _FakeResponse(
        headers={"content-type": "image/png", "content-length": str(len(_PNG_BYTES))},
        content_chunks=[_PNG_BYTES],
    )
    temp_suffix = ".png.part"

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

    monkeypatch.setattr(os, "close", _tracking_close)
    monkeypatch.setattr(aiofiles, "open", _raising_aiofiles_open)

    with pytest.raises(OSError, match="aiofiles open boom"):
        await manager._download_response_to_temp(
            response,  # type: ignore[arg-type]
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    # finally 兜底已关闭 open_temp_fd 产出的 fd，避免泄漏
    assert len(closed_fds) == 1
