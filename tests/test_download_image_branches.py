"""download_image 重试分类、_stream_response_to_resume 内部分支、断点续传、出站请求头钉死与取消语义测试。

用 fake session/response 模拟，不依赖真实网络。
"""

from __future__ import annotations

import asyncio
import errno
import gzip
import os
import random
import socket
import threading
import time
import zlib
from collections.abc import Buffer, Callable, Iterator
from pathlib import Path
from typing import Any, cast

import aiohttp
import pytest

from seedream_mcp.utils.core import formats
from seedream_mcp.utils.core.formats import SNIFF_HEAD_BYTES_FLOOR
from seedream_mcp.utils.io import io_download as io_download_module
from seedream_mcp.utils.io import io_file
from seedream_mcp.utils.io.io_download import (
    DownloadError,
    DownloadManager,
    RetryableDownloadError,
    _ResumeState,
    _int_or_none,
    _parse_content_range,
    _resume_validator,
)

from _download_fakes import (
    _FakeResponse,
    _FakeSession,
    _FakeStreamContent,
    _InterruptedResponse,
    _InterruptedStreamContent,
    _PNG_BYTES,
    _RaisingThenSuccessSession,
    _patch_download_network,
    _png_success_response,
    _single_trip_to_temp,
)
from _os_fakes import _install_close_counter, _install_replace_fail_once

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


async def test_download_linear_backoff_sequence_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """线性退避取值契约：抖动归零后为 retry_delay*(attempt+1) 的递增序列。"""
    manager = DownloadManager(max_retries=3)
    session = _FakeSession([_FakeResponse(status=429, headers={"content-type": "text/plain"})])
    _patch_download_network(monkeypatch, manager, session)
    sleeps: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _capture_sleep)
    monkeypatch.setattr(random, "uniform", lambda low, high: 0.0)

    save_path = tmp_path / "out.png"
    with pytest.raises(RetryableDownloadError):
        await manager.download_image("https://example.com/img.png", save_path)

    assert sleeps == [1.0, 2.0, 3.0]


async def test_download_manager_negative_max_retries_raises_download_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_retries<0 时重试循环零次执行：抛 DownloadError 而非 attempt 未绑定的 NameError。"""
    manager = DownloadManager(max_retries=-1)
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="下载失败") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 循环零次执行即失败收场，无网络请求发出。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 0
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


@pytest.mark.parametrize(
    ("exc", "match"),
    [
        pytest.param(OSError(errno.EDQUOT, "disk quota exceeded"), "文件系统永久错误", id="edquot"),
        pytest.param(
            PermissionError(errno.EACCES, "Permission denied"), "权限拒绝，不可重试", id="eacces"
        ),
    ],
)
async def test_download_image_does_not_retry_on_permanent_fs_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    exc: OSError,
    match: str,
) -> None:
    """配额超限与权限拒绝均需人工介入，单次尝试即终态不进入退避重试。"""
    manager = DownloadManager()
    session = _RaisingThenSuccessSession(exc, _png_success_response())
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match=match):
        await manager.download_image("https://example.com/img.png", save_path)

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
    resolve_calls: list[int] = []

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
    resolve_calls: list[int] = []

    async def _private_ip_getaddrinfo(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        resolve_calls.append(1)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.1", 0))]

    _patch_loop_getaddrinfo(monkeypatch, _private_ip_getaddrinfo)

    save_path = tmp_path / "out.png"
    # 经上下文管理器进入使真实会话在断言后确定关闭，避免依赖 GC 兜底
    async with DownloadManager() as manager:
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
    resolve_calls: list[int] = []

    async def _wsa_11001_getaddrinfo(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        resolve_calls.append(1)
        raise socket.gaierror(11001, "Host not found")

    _patch_loop_getaddrinfo(monkeypatch, _wsa_11001_getaddrinfo)

    save_path = tmp_path / "out.png"
    # 经上下文管理器进入使真实会话在断言后确定关闭，避免依赖 GC 兜底
    async with DownloadManager() as manager:
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
    resolve_calls: list[int] = []

    async def _wsa_11002_getaddrinfo(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        resolve_calls.append(1)
        raise socket.gaierror(11002, "Non-authoritative host not found")

    _patch_loop_getaddrinfo(monkeypatch, _wsa_11002_getaddrinfo)

    save_path = tmp_path / "out.png"
    # 经上下文管理器进入使真实会话在断言后确定关闭，避免依赖 GC 兜底
    async with DownloadManager() as manager:
        with pytest.raises(RetryableDownloadError, match="域名解析失败"):
            await manager.download_image("https://example.com/img.png", save_path)

        # 可重试错误按 max_retries 上限反复解析，而非单次终态上抛
        assert len(resolve_calls) == manager.max_retries + 1
    assert not save_path.exists()


async def test_download_dns_resolution_timeout_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """getaddrinfo 超出解析时限时按下载超时分类，纳入退避重试而非单次终态。"""

    async def _hanging_getaddrinfo(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        await asyncio.Event().wait()

    _patch_loop_getaddrinfo(monkeypatch, _hanging_getaddrinfo)

    # timeout=0 时 wait_for 在协程首步执行前即取消，解析计数改挂在入口处
    manager = DownloadManager(timeout=0)
    resolve_calls: list[int] = []
    real_resolve = manager._resolve_public_ips

    async def _counting_resolve(host: str) -> tuple[str, ...]:
        resolve_calls.append(1)
        return await real_resolve(host)

    monkeypatch.setattr(manager, "_resolve_public_ips", _counting_resolve)

    save_path = tmp_path / "out.png"
    # 经上下文管理器进入使真实会话在断言后确定关闭，避免依赖 GC 兜底
    async with manager:
        with pytest.raises(DownloadError, match="下载超时"):
            await manager.download_image("https://example.com/img.png", save_path)

    # 解析超时未写缓存，每次尝试都重新解析
    assert len(resolve_calls) == manager.max_retries + 1
    assert not save_path.exists()


class _TruthfulBarrenInfos:
    """bool 为真而迭代为空的病态解析结果，驱动空 resolved_ips 的兜底分支。"""

    def __bool__(self) -> bool:
        return True

    def __iter__(self) -> Iterator[Any]:
        return iter(())


@pytest.mark.parametrize(
    ("resolution", "match"),
    [
        pytest.param([], "域名解析结果为空", id="empty"),
        pytest.param(
            [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("not-an-ip", 0))],
            "域名解析返回非法IP",
            id="illegal-ip",
        ),
        pytest.param(_TruthfulBarrenInfos(), "域名解析结果为空", id="barren-infos"),
    ],
)
async def test_download_dns_malformed_resolution_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    resolution: Any,
    match: str,
) -> None:
    """畸形解析结果按终态拒绝，单次尝试即上抛且不退避。"""
    resolve_calls: list[int] = []

    async def _getaddrinfo(host: str, port: int, **kwargs: object) -> Any:
        del host, port, kwargs
        resolve_calls.append(1)
        return resolution

    _patch_loop_getaddrinfo(monkeypatch, _getaddrinfo)

    save_path = tmp_path / "out.png"
    async with DownloadManager() as manager:
        with pytest.raises(DownloadError, match=match) as excinfo:
            await manager.download_image("https://example.com/img.png", save_path)

    # 终态错误且非可重试子类，单次尝试即上抛，未触发退避重试
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert len(resolve_calls) == 1
    assert not save_path.exists()


# ==================== _stream_response_to_resume 内部分支 ====================


async def test_download_response_rejects_invalid_content_length(tmp_path: Path) -> None:
    """content-length 非整数时抛出 DownloadError，不进入文件写入阶段。"""
    manager = DownloadManager()
    response = _FakeResponse(
        headers={"content-type": "image/png", "content-length": "not-a-number"}
    )
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="非法 content-length"):
        await _single_trip_to_temp(
            manager,
            response,
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    # 校验失败发生在 mkdir 与文件创建之前，临时文件不应存在
    assert not list(tmp_path.glob("*.part"))


async def test_download_response_rejects_oversized_content_length(tmp_path: Path) -> None:
    """content-length 超 max_file_size 时提前抛出 DownloadError。"""
    manager = DownloadManager(max_file_size=100)
    response = _FakeResponse(headers={"content-type": "image/png", "content-length": "101"})
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="文件过大"):
        await _single_trip_to_temp(
            manager,
            response,
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )


async def test_download_response_rejects_negative_content_length(tmp_path: Path) -> None:
    """content-length 为负值时抛出 DownloadError，不进入文件写入阶段。

    负号非 ASCII 数字形态，严格解析归 None 走畸形分支，原本 -1 > max_file_size
    为 False 会绕过预检查。
    """
    manager = DownloadManager()
    response = _FakeResponse(headers={"content-type": "image/png", "content-length": "-1"})
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="非法 content-length"):
        await _single_trip_to_temp(
            manager,
            response,
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    assert not list(tmp_path.glob("*.part"))


# HTTP 整数头的非法形态：裸 int() 可解析但按 RFC 9110 语法非法、Unicode 数字
# 绕过 isdigit，或空串一类平凡畸形；Content-Range 与 Content-Length 共用同一拒绝口径。
_MALFORMED_HTTP_INTEGERS = [
    pytest.param("+5", id="plus-sign"),
    pytest.param(" 5", id="leading-space"),
    pytest.param("1_0", id="underscore"),
    pytest.param("", id="empty"),
    pytest.param("１２３", id="fullwidth"),
    pytest.param("١٢٣", id="arabic-indic"),
]


def test_int_or_none_accepts_pure_ascii_digits() -> None:
    """纯 ASCII 十进制数字正常解析，前导零形态同样受理。"""
    assert _int_or_none("0") == 0
    assert _int_or_none("007") == 7
    assert _int_or_none("123456789") == 123456789


@pytest.mark.parametrize("value", _MALFORMED_HTTP_INTEGERS)
def test_int_or_none_rejects_non_ascii_digit_forms(value: str) -> None:
    """非纯 ASCII 十进制数字形态一律归 None，不经裸 int() 宽松解析。"""
    assert _int_or_none(value) is None


def test_int_or_none_normalizes_overlong_digit_run() -> None:
    """超长数字串超出 int 字串转换上限，按畸形归一 None 不抛 ValueError。"""
    assert _int_or_none("9" * 4301) is None


@pytest.mark.parametrize("value", _MALFORMED_HTTP_INTEGERS)
def test_parse_content_range_rejects_loose_integer_forms(value: str) -> None:
    """畸形分量归 None 走既有畸形分支，不得解析出错误锚定的偏移或总长。"""
    total_malformed = _parse_content_range(f"bytes 0-199/{value}")
    end_malformed = _parse_content_range(f"bytes 0-{value}/1000")

    assert total_malformed.total is None
    assert end_malformed.end is None
    # 起点分量的前置空白已被单元分隔的 split 规范化吞并，其余形态原样到达严格解析。
    if not value[:1].isspace():
        assert _parse_content_range(f"bytes {value}-199/1000").start is None


@pytest.mark.parametrize("value", _MALFORMED_HTTP_INTEGERS)
async def test_download_rejects_malformed_content_length(tmp_path: Path, value: str) -> None:
    """携带畸形 content-length 的响应按终态拒绝，不进入文件写入阶段。"""
    manager = DownloadManager()
    response = _FakeResponse(headers={"content-type": "image/png", "content-length": value})

    with pytest.raises(DownloadError, match="非法 content-length"):
        await _single_trip_to_temp(
            manager,
            response,
            tmp_path / "out.png",
            ".png.part",
            "image/png",
            0,
            time.monotonic(),
        )

    assert not list(tmp_path.glob("*.part"))


async def test_download_response_rejects_streaming_cumulative_oversize(
    tmp_path: Path,
) -> None:
    """无 content-length 时按流式累计字节数，超 max_file_size 抛出 DownloadError。"""
    manager = DownloadManager(max_file_size=100)
    # 两块各 60 字节，累计 120 超过上限 100
    response = _FakeResponse(
        headers={"content-type": "image/png"}, content_chunks=[b"x" * 60, b"x" * 60]
    )
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="文件过大"):
        await _single_trip_to_temp(
            manager,
            response,
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )


async def test_download_response_rejects_byte_signature_mismatch(tmp_path: Path) -> None:
    """Content-Type 声称 image/png 但字节签名非受支持图片格式时抛出 DownloadError。"""
    manager = DownloadManager()
    response = _FakeResponse(
        headers={"content-type": "image/png", "content-length": "13"},
        content_chunks=[b"NOT_AN_IMAGE!"],
    )
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="字节签名"):
        await _single_trip_to_temp(
            manager,
            response,
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    # 签名校验失败发生在 replace 之前，最终文件不应落盘
    assert not (tmp_path / "out.png").exists()


async def test_download_response_rejects_signature_mismatch_in_first_chunk(
    tmp_path: Path,
) -> None:
    """首块字节即填满嗅探窗口且非图片签名时流内即时拒绝，不消费后续分块。"""
    manager = DownloadManager()
    # 首块达 64 字节嗅探窗口下界，伪造 Content-Type 的响应在首块后即被拒
    forged_head = b"<html>" + b"x" * (SNIFF_HEAD_BYTES_FLOOR - len(b"<html>"))
    trailing_chunk = b"y" * 16
    response = _FakeResponse(
        headers={
            "content-type": "image/png",
            "content-length": str(len(forged_head) + len(trailing_chunk)),
        },
        content_chunks=[forged_head, trailing_chunk],
    )
    temp_suffix = ".png.part"

    with pytest.raises(DownloadError, match="字节签名"):
        await _single_trip_to_temp(
            manager,
            response,
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    # 流内即时拒绝仅消费首块，第二块未被读取，不再占用后续带宽与磁盘写入
    assert response.content._chunk_idx == 1
    assert not (tmp_path / "out.png").exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_signature_check_runs_once_per_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """嗅探窗口集满后流内判定每 attempt 只跑一次，不再逐块重跑。"""
    manager = DownloadManager()
    # 首块 32 字节未达 64 字节窗口，第二块填满触发判定，后续块不应重跑。
    body = _PNG_BYTES + b"x" * 228
    response = _FakeResponse(
        headers={"content-type": "image/png", "content-length": str(len(body))},
        content_chunks=[_PNG_BYTES, b"x" * 100, b"x" * 64, b"x" * 64],
    )
    session = _FakeSession([response])
    _patch_download_network(monkeypatch, manager, session)

    calls: list[bytes] = []
    real_check = formats.is_known_image_bytes

    def _counting_check(head_bytes: bytes) -> bool:
        calls.append(head_bytes)
        return real_check(head_bytes)

    monkeypatch.setattr(io_download_module, "is_known_image_bytes", _counting_check)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.read_bytes() == body
    # 流内窗口在第二块填满时判定 1 次、终局复核 1 次，而非逐块重跑。
    assert len(calls) == 2


async def test_download_signature_latch_resets_for_resumed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """续传趟窗口已预填：签名判定按新 attempt 重走一次而非沿用上趟锁存。"""
    manager = DownloadManager()
    session = _FakeSession([_interrupted_first_attempt(), _resumed_response(_RESUME_SPLIT)])
    _patch_download_network(monkeypatch, manager, session)

    calls: list[bytes] = []
    real_check = formats.is_known_image_bytes

    def _counting_check(head_bytes: bytes) -> bool:
        calls.append(head_bytes)
        return real_check(head_bytes)

    monkeypatch.setattr(io_download_module, "is_known_image_bytes", _counting_check)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    # 首趟窗口填满判定 1 次、续传趟预填窗口重走判定 1 次、终局复核 1 次。
    assert len(calls) == 3


async def test_download_response_closes_fd_when_part_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落盘写入的 .part 打开失败时：异常上抛，续传 fd 由 dispose 关闭恰一次。"""
    manager = DownloadManager()
    response = _FakeResponse(
        headers={"content-type": "image/png", "content-length": str(len(_PNG_BYTES))},
        content_chunks=[_PNG_BYTES],
    )
    temp_suffix = ".png.part"

    closed = _install_close_counter(monkeypatch)

    def _raising_open_write_fd(path: Path) -> int:
        del path
        raise OSError("part open boom")

    monkeypatch.setattr(io_download_module, "open_no_follow_write_fd", _raising_open_write_fd)

    with pytest.raises(OSError, match="part open boom"):
        await _single_trip_to_temp(
            manager,
            response,
            tmp_path / "out.png",
            temp_suffix,
            "image/png",
            0,
            time.monotonic(),
        )

    # 续传状态持有的 fd 经 dispose 关闭恰一次，写入线程内无 fd 可泄漏。
    assert len(closed) == 1
    assert not list(tmp_path.glob("*.part"))


# ==================== 断点续传 ====================

# 续传测试体：PNG 签名 + 零填充，已确认偏移 100 覆盖 64 字节嗅探窗口，部分字节可跨尝试保留。
_RESUME_BODY = b"\x89PNG\r\n\x1a\n" + b"\x00" * 192
_RESUME_ETAG = '"resume-v1"'
_RESUME_SPLIT = 100


def _interrupted_first_attempt() -> _InterruptedResponse:
    """构造首趟响应：交付前 100 字节后连接中断，携带源校验器。"""
    return _InterruptedResponse([_RESUME_BODY[:_RESUME_SPLIT]], _RESUME_ETAG, len(_RESUME_BODY))


# 弱 ETag 场景的响应头取值：弱标记与 Last-Modified 均不作 If-Range 验证器。
_RESUME_WEAK_ETAG = 'W/"resume-weak"'
_RESUME_LAST_MODIFIED = "Wed, 21 Oct 2015 07:28:00 GMT"


def _interrupted_weak_etag_attempt(last_modified: str | None) -> _InterruptedResponse:
    """构造弱 ETag 的中断首趟，last_modified 非 None 时附带不作验证器的 Last-Modified。"""
    response = _InterruptedResponse(
        [_RESUME_BODY[:_RESUME_SPLIT]], _RESUME_WEAK_ETAG, len(_RESUME_BODY)
    )
    if last_modified is not None:
        response.headers["last-modified"] = last_modified
    return response


def _interrupted_last_modified_only_attempt() -> _FakeResponse:
    """构造仅 Last-Modified 无 ETag 的中断首趟：弱验证器不作续传依据。"""
    response = _FakeResponse(
        status=200,
        headers={
            "content-type": "image/png",
            "content-length": str(len(_RESUME_BODY)),
            "last-modified": _RESUME_LAST_MODIFIED,
        },
    )
    response.content = cast(
        "_FakeStreamContent", _InterruptedStreamContent([_RESUME_BODY[:_RESUME_SPLIT]])
    )
    return response


def _interrupted_no_validator_attempt() -> _FakeResponse:
    """构造无校验器的中断首趟：已存部分字节但重试退回全量，调用方 Range 在入口剥离、出站无 Range。"""
    response = _FakeResponse(
        status=200,
        headers={"content-type": "image/png", "content-length": str(len(_RESUME_BODY))},
    )
    response.content = cast(
        "_FakeStreamContent", _InterruptedStreamContent([_RESUME_BODY[:_RESUME_SPLIT]])
    )
    return response


def _resumed_response(offset: int) -> _FakeResponse:
    """构造续传 206 响应：Content-Range 起点对齐指定偏移，仅含剩余字节。"""
    remaining = _RESUME_BODY[offset:]
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": _RESUME_ETAG,
            "content-range": f"bytes {offset}-{len(_RESUME_BODY) - 1}/{len(_RESUME_BODY)}",
            "content-length": str(len(remaining)),
        },
        content_chunks=[remaining],
    )


def _capped_resume_response() -> _FakeResponse:
    """构造区间上限 206：仅交付 50 字节分片，Content-Range 声明的总长未达。"""
    capped = _RESUME_BODY[_RESUME_SPLIT : _RESUME_SPLIT + 50]
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": _RESUME_ETAG,
            "content-range": (
                f"bytes {_RESUME_SPLIT}-{_RESUME_SPLIT + len(capped) - 1}/{len(_RESUME_BODY)}"
            ),
            "content-length": str(len(capped)),
        },
        content_chunks=[capped],
    )


def _unknown_total_resume_response(offset: int, delivered: int) -> _FakeResponse:
    """构造总长未知的续传 206：``bytes N-/*`` 且无 Content-Length，流干净收尾。"""
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": _RESUME_ETAG,
            "content-range": f"bytes {offset}/*",
        },
        content_chunks=[_RESUME_BODY[offset : offset + delivered]],
    )


def _full_retry_response(etag: str) -> _FakeResponse:
    """构造全量 200 响应：携带整个测试体，模拟不支持续传或源已变化的服务端。"""
    return _FakeResponse(
        status=200,
        headers={
            "content-type": "image/png",
            "etag": etag,
            "content-length": str(len(_RESUME_BODY)),
        },
        content_chunks=[_RESUME_BODY],
    )


async def test_download_resume_continues_from_confirmed_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """中断重试携带 Range/If-Range 续传：已存前缀保留，仅补齐剩余字节。"""
    manager = DownloadManager()
    session = _FakeSession([_interrupted_first_attempt(), _resumed_response(_RESUME_SPLIT)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert result["file_size"] == len(_RESUME_BODY)
    assert save_path.read_bytes() == _RESUME_BODY
    # 首趟不带区间头；重试趟以已确认偏移与源校验器发起续传。
    assert "Range" not in session.captured_headers[0]
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert session.captured_headers[1]["If-Range"] == _RESUME_ETAG


async def test_download_resume_falls_back_to_full_when_range_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """服务端对 Range 请求回 200 全量体时作废已存字节从零重下，内容完整。"""
    manager = DownloadManager()
    session = _FakeSession([_interrupted_first_attempt(), _full_retry_response(_RESUME_ETAG)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["file_size"] == len(_RESUME_BODY)
    assert save_path.read_bytes() == _RESUME_BODY
    # 已发起续传请求，服务端不支持而回全量体。
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"


async def test_download_resume_falls_back_to_full_when_etag_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """If-Range 命中源变化回 200 新 ETag 时退回全量重下，不拼接陈旧前缀。"""
    manager = DownloadManager()
    session = _FakeSession([_interrupted_first_attempt(), _full_retry_response('"resume-v2"')])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.read_bytes() == _RESUME_BODY
    # 续传请求携带的是首趟记录的旧 ETag，据此检出源已变化。
    assert session.captured_headers[1]["If-Range"] == _RESUME_ETAG


def _renewed_etag_resume_response() -> _FakeResponse:
    """构造无视 If-Range 回新表示字节的 206：区间起点恰合偏移而 ETag 已变化。"""
    remaining = _RESUME_BODY[_RESUME_SPLIT:]
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": '"resume-renewed"',
            "content-range": f"bytes {_RESUME_SPLIT}-{len(_RESUME_BODY) - 1}/{len(_RESUME_BODY)}",
            "content-length": str(len(remaining)),
        },
        content_chunks=[remaining],
    )


async def test_download_resume_etag_mismatch_restarts_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """206 携带与源校验器不符的 ETag 即表示已变化：作废前缀退回全量，不拼新尾部。"""
    manager = DownloadManager()
    renewed = _renewed_etag_resume_response()
    session = _FakeSession(
        [_interrupted_first_attempt(), renewed, _full_retry_response(_RESUME_ETAG)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # ETag 不符作废前缀后，第三趟不再携带注定失败的同一 Range。
    assert session.captured_headers[2].get("Range") is None
    # 作废前缀前先排空响应体，连接得以回池。
    assert renewed.content._chunk_idx == 1


def _etagless_resume_response(offset: int, total: str | int) -> _FakeResponse:
    """构造未携带 ETag 的续传 206：Content-Range 总长取传入值，无法核验表示身份。"""
    remaining = _RESUME_BODY[offset:]
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "content-range": f"bytes {offset}-{len(_RESUME_BODY) - 1}/{total}",
            "content-length": str(len(remaining)),
        },
        content_chunks=[remaining],
    )


async def test_download_resume_etagless_206_keeps_resuming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """206 未携带 ETag 时无法核验表示身份：区间核验通过即照常续传落盘。"""
    manager = DownloadManager()
    session = _FakeSession(
        [_interrupted_first_attempt(), _etagless_resume_response(_RESUME_SPLIT, len(_RESUME_BODY))]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY


async def test_download_resume_declared_total_mismatch_restarts_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """206 声明总长与已知总长不符即表示已变化：作废前缀退回全量，不拼新尾部。"""
    manager = DownloadManager()
    renewed = _etagless_resume_response(_RESUME_SPLIT, len(_RESUME_BODY) + 50)
    session = _FakeSession(
        [_interrupted_first_attempt(), renewed, _full_retry_response(_RESUME_ETAG)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 总长不符作废前缀后，第三趟不再携带注定失败的同一 Range。
    assert "Range" not in session.captured_headers[2]
    # 作废前缀前先排空响应体，连接得以回池。
    assert renewed.content._chunk_idx == 1


@pytest.mark.parametrize(
    "total",
    [
        pytest.param(len(_RESUME_BODY), id="match"),
        pytest.param("*", id="missing"),
    ],
)
async def test_download_resume_declared_total_match_or_missing_keeps_resuming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None, total: str | int
) -> None:
    """206 声明总长与已知总长一致或缺失：维持现状照常续传落盘。"""
    manager = DownloadManager()
    session = _FakeSession(
        [_interrupted_first_attempt(), _etagless_resume_response(_RESUME_SPLIT, total)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY


async def test_download_resume_weak_etag_with_last_modified_redownloads_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """弱 ETag 加 Last-Modified 不作验证器续传：弱语义可拼接静默坏图，退回全量。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _interrupted_weak_etag_attempt(_RESUME_LAST_MODIFIED),
            _full_retry_response(_RESUME_WEAK_ETAG),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY
    # Last-Modified 弱语义不作 If-Range 验证器：重试退回全量请求，二者不进请求头。
    retry_headers = session.captured_headers[1]
    assert "Range" not in retry_headers
    assert "If-Range" not in retry_headers
    assert _RESUME_WEAK_ETAG not in retry_headers.values()
    assert _RESUME_LAST_MODIFIED not in retry_headers.values()


async def test_download_resume_weak_etag_without_fallback_redownloads_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """弱 ETag 且无 Last-Modified 时不发 Range/If-Range：无强验证器可依，退回全量请求由 200 自愈。"""
    manager = DownloadManager()
    session = _FakeSession(
        [_interrupted_weak_etag_attempt(None), _full_retry_response(_RESUME_WEAK_ETAG)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY
    assert "Range" not in session.captured_headers[1]
    assert "If-Range" not in session.captured_headers[1]


def test_resume_validator_strong_etag_only() -> None:
    """仅强 ETag 作续传验证器：弱 ETag 与 Last-Modified 弱语义一律返回 None。"""
    weak = _FakeResponse(
        headers={"etag": _RESUME_WEAK_ETAG, "last-modified": _RESUME_LAST_MODIFIED}
    )
    last_modified_only = _FakeResponse(headers={"last-modified": _RESUME_LAST_MODIFIED})
    strong = _FakeResponse(headers={"etag": _RESUME_ETAG, "last-modified": _RESUME_LAST_MODIFIED})

    assert _resume_validator(cast(aiohttp.ClientResponse, weak)) is None
    assert _resume_validator(cast(aiohttp.ClientResponse, last_modified_only)) is None
    assert _resume_validator(cast(aiohttp.ClientResponse, strong)) == _RESUME_ETAG


async def test_download_resume_last_modified_only_redownloads_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """无强 ETag 仅 Last-Modified：弱验证器同秒重写可拼接静默坏图，重试退回全量。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _interrupted_last_modified_only_attempt(),
            _full_retry_response(_RESUME_ETAG),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY
    assert "Range" not in session.captured_headers[1]
    assert "If-Range" not in session.captured_headers[1]


async def test_download_resume_mismatched_content_range_restarts_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """206 的 Content-Range 起点与已确认偏移不符时作废部分字节，下一趟全量重下。"""
    mismatched = _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "content-range": f"bytes 50-{len(_RESUME_BODY) - 1}/{len(_RESUME_BODY)}",
            "content-length": str(len(_RESUME_BODY) - 50),
        },
        content_chunks=[_RESUME_BODY[50:]],
    )
    manager = DownloadManager()
    session = _FakeSession(
        [_interrupted_first_attempt(), mismatched, _full_retry_response(_RESUME_ETAG)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 区间不符后部分字节已作废，第三趟不再携带 Range。
    assert "Range" not in session.captured_headers[2]
    # 作废前缀前先排空响应体，连接得以回池。
    assert mismatched.content._chunk_idx == 1


async def test_download_resume_short_range_body_not_finalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """区间上限的短体 206 流收尾正常但不判完成：续传补齐后内容完整。"""
    manager = DownloadManager()
    capped_end = _RESUME_SPLIT + 50
    session = _FakeSession(
        [
            _interrupted_first_attempt(),
            _capped_resume_response(),
            _resumed_response(capped_end),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 短体趟确认到 150 字节，下一趟自新偏移续传而非误判完成落盘截断文件。
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert session.captured_headers[2]["Range"] == f"bytes={capped_end}-"


async def test_download_resume_unknown_total_short_body_not_finalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """总长未知续传 206 的干净短体：沿用上趟已知总长判短体失败，不得判成功落盘。"""
    manager = DownloadManager(max_retries=1)
    session = _FakeSession(
        [_interrupted_first_attempt(), _unknown_total_resume_response(_RESUME_SPLIT, 50)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(RetryableDownloadError, match="响应体短于声明总量: 150/200"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 短体判定锚定首趟 200 的声明总长，截断体不落盘。
    assert session._idx == 2
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_resume_unknown_total_completes_at_known_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """总长未知续传 206 补齐至上趟已知总长：沿用总长判定完成并成功落盘。"""
    manager = DownloadManager()
    remaining = len(_RESUME_BODY) - _RESUME_SPLIT
    session = _FakeSession(
        [_interrupted_first_attempt(), _unknown_total_resume_response(_RESUME_SPLIT, remaining)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert result["file_size"] == len(_RESUME_BODY)
    assert save_path.read_bytes() == _RESUME_BODY


async def test_download_resume_416_restarts_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """416 区间不可满足作废部分字节退回全量重下，不以终态失败收场。"""
    unsatisfiable = _FakeResponse(
        status=416, headers={"content-type": "image/png"}, content_chunks=[b"x" * 16]
    )
    manager = DownloadManager()
    session = _FakeSession(
        [_interrupted_first_attempt(), unsatisfiable, _full_retry_response(_RESUME_ETAG)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 416 作废部分字节后，第三趟不再携带 Range。
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert "Range" not in session.captured_headers[2]
    # 作废前缀前先排空响应体，连接得以回池。
    assert unsatisfiable.content._chunk_idx == 1


# ==================== 调用方自带 Range 头：不并入续传语义 ====================


def _caller_range_fulfilling_206() -> _FakeResponse:
    """构造对调用方 Range 的完整 206 应答：区间自零起，交付整个测试体。"""
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "content-range": f"bytes 0-{len(_RESUME_BODY) - 1}/{len(_RESUME_BODY)}",
            "content-length": str(len(_RESUME_BODY)),
        },
        content_chunks=[_RESUME_BODY],
    )


async def test_download_caller_range_206_without_partial_state_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """调用方自带 Range 且无部分状态：206 落非 200 终态，不进入续传语义。"""
    manager = DownloadManager()
    session = _FakeSession([_caller_range_fulfilling_206()])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="HTTP错误") as excinfo:
        await manager.download_image(
            "https://example.com/img.png", save_path, headers={"Range": "bytes=0-"}
        )

    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_caller_range_206_with_partial_state_not_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """调用方自带 Range 且有部分状态而无校验器时，206 因出站无 Range 落非 200 终态。

    该 206 区间起点与已确认偏移不符，若按响应头扫描比对偏移会误作废前缀重启重试。
    """
    manager = DownloadManager()
    session = _FakeSession(
        [
            _interrupted_no_validator_attempt(),
            _caller_range_fulfilling_206(),
            _full_retry_response(_RESUME_ETAG),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="HTTP错误") as excinfo:
        await manager.download_image(
            "https://example.com/img.png", save_path, headers={"Range": "bytes=0-"}
        )

    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 2
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


# ==================== 出站请求头钉死 Accept-Encoding: identity ====================


async def test_download_pins_identity_accept_encoding_across_hops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """出站各跳请求恒钉死 Accept-Encoding: identity：透明解压会破坏等量核算口径。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _FakeResponse(302, {"location": "https://cdn.example.net/img.png"}),
            _png_success_response(),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert len(session.captured_headers) == 2
    # 起始请求与跨源重定向跳均携带 identity，gzip 应答无从进入等量核算。
    assert all(headers["Accept-Encoding"] == "identity" for headers in session.captured_headers)


async def test_download_overrides_caller_accept_encoding_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """调用方自带 Accept-Encoding 被入口覆盖为 identity：等量核算口径不随调用方头漂移。"""
    manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image(
        "https://example.com/img.png",
        save_path,
        headers={"User-Agent": "Seedream-MCP/test", "Accept-Encoding": "gzip"},
    )

    assert result["success"] is True
    assert session.captured_headers[0] == {
        "User-Agent": "Seedream-MCP/test",
        "Accept-Encoding": "identity",
    }


async def test_download_session_disables_auto_decompress() -> None:
    """会话创建即禁用透明解压：等量核算以传输字节为准，不依赖服务器遵守 identity。"""
    manager = DownloadManager()
    try:
        session = await manager._ensure_session()
        assert isinstance(session, aiohttp.ClientSession)
        assert session.auto_decompress is False
    finally:
        await manager.close()


# ==================== 强压源站的终验解码回退 ====================

# 解码回退成功测试体：PNG 魔数 + 确定性随机字节，不可压缩使压缩体长度越过嗅探
# 窗口，驱动流内压缩容器放行与终验解码两条路径。
_DECODE_BODY = b"\x89PNG\r\n\x1a\n" + random.Random(20260101).randbytes(200)
# 解压炸弹测试体：PNG 魔数 + 零填充，解码后远超小上限而压缩体自身很小。
_DECODE_BOMB_BODY = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400


def _compressed_success_response(body: bytes) -> _FakeResponse:
    """构造无视 identity 强返压缩体的 200 响应：Content-Type 仍声称图片。"""
    return _FakeResponse(
        status=200,
        headers={"content-type": "image/png", "content-length": str(len(body))},
        content_chunks=[body],
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(gzip.compress(_DECODE_BODY), id="gzip"),
        pytest.param(zlib.compress(_DECODE_BODY), id="zlib"),
    ],
)
async def test_download_forced_compressed_body_decoded_and_saved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    body: bytes,
) -> None:
    """强返压缩体的源站：终验解压复核后保存解码图片，扩展名与 MIME 以解码字节重定。"""
    manager = DownloadManager()
    session = _FakeSession([_compressed_success_response(body)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.jpeg"
    result = await manager.download_image("https://example.com/img.jpeg", save_path)

    corrected = tmp_path / "out.png"
    assert result["success"] is True
    assert result["attempts"] == 1
    assert result["file_path"] == str(corrected)
    assert result["file_size"] == len(_DECODE_BODY)
    assert result["content_type"] == "image/png"
    assert corrected.read_bytes() == _DECODE_BODY
    assert not save_path.exists()


async def test_download_forced_gzip_decode_over_cap_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """解码输出超 max_file_size 属解压炸弹形态：维持终态签名拒绝不落盘。"""
    manager = DownloadManager(max_file_size=100)
    session = _FakeSession([_compressed_success_response(gzip.compress(_DECODE_BOMB_BODY))])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="字节签名") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"\x1f\x8b" + b"G" * 80, id="corrupt-gzip"),
        pytest.param(gzip.compress(_DECODE_BODY)[:-8], id="truncated-gzip"),
        pytest.param(zlib.compress(_DECODE_BODY)[:-5], id="truncated-zlib"),
        pytest.param(gzip.compress(b"NOT_AN_IMAGE_" + b"x" * 80), id="decoded-not-image"),
    ],
)
async def test_download_forced_compressed_body_undecodable_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    body: bytes,
) -> None:
    """损坏或截断的压缩流、解码字节仍非受支持图片：维持终态签名拒绝。"""
    manager = DownloadManager()
    session = _FakeSession([_compressed_success_response(body)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="字节签名") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


# 跨块测试体：解码输出越过单轮 max_length（_DOWNLOAD_CHUNK_SIZE=256KB），驱动
# 多轮解压收束路径；确定性随机保证不可压缩、压缩体越过嗅探窗口。
_DECODE_CROSS_CHUNK_BODY = b"\x89PNG\r\n\x1a\n" + random.Random(20260103).randbytes(300_000)


async def test_download_forced_gzip_cross_chunk_output_decoded_and_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """解码输出跨多轮 max_length 收束：正常解压落盘，跨块轮次不误判残留。"""
    manager = DownloadManager()
    session = _FakeSession([_compressed_success_response(gzip.compress(_DECODE_CROSS_CHUNK_BODY))])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 1
    assert result["file_size"] == len(_DECODE_CROSS_CHUNK_BODY)
    assert save_path.read_bytes() == _DECODE_CROSS_CHUNK_BODY
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(gzip.compress(_DECODE_CROSS_CHUNK_BODY) + b"XX", id="trailing-bytes"),
        pytest.param(
            gzip.compress(_DECODE_CROSS_CHUNK_BODY) + gzip.compress(_DECODE_BODY),
            id="multi-member",
        ),
    ],
)
async def test_download_forced_gzip_cross_chunk_residue_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    body: bytes,
) -> None:
    """跨块输出收束后仍有残留输入（附加字节或多成员 gzip）：终态拒绝而非死循环。

    wait_for 使死循环回归快速失败而非挂起测试套件。
    """
    manager = DownloadManager()
    session = _FakeSession([_compressed_success_response(body)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="字节签名") as excinfo:
        await asyncio.wait_for(
            manager.download_image("https://example.com/img.png", save_path), timeout=30
        )

    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_forced_zlib_small_read_chunks_residue_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """压缩体按块流式读取：收束恰在读取块边界、残留始于后续块时终态拒绝。"""
    monkeypatch.setattr(io_download_module, "_DOWNLOAD_CHUNK_SIZE", 1)
    body = zlib.compress(_DECODE_BODY) + b"XX"
    manager = DownloadManager()
    session = _FakeSession([_compressed_success_response(body)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="字节签名") as excinfo:
        await asyncio.wait_for(
            manager.download_image("https://example.com/img.png", save_path), timeout=30
        )

    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


def test_rewrite_part_file_zero_write_progress_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.write 零进展返回 0 时按自截断起失败上抛可重试错误，而非空转烧线程。"""
    part = tmp_path / "out.png.part"
    part.write_bytes(b"stale")
    monkeypatch.setattr(os, "write", lambda fd, buffer: 0)

    with pytest.raises(RetryableDownloadError, match="临时文件已毁"):
        io_download_module._rewrite_part_file(part, b"rewritten")


def test_rewrite_part_file_close_failure_does_not_mask_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写失败路径上 fd 关闭的 OSError 经静默关闭吸收，写失败经可重试错误链保留。"""
    part = tmp_path / "out.png.part"
    part.write_bytes(b"stale")

    def _raising_write(fd: int, buffer: object) -> int:
        del fd, buffer
        raise OSError("写入失败")

    def _raising_close(fd: int) -> None:
        del fd
        raise OSError("关闭失败")

    monkeypatch.setattr(os, "write", _raising_write)
    monkeypatch.setattr(os, "close", _raising_close)

    with pytest.raises(RetryableDownloadError, match="临时文件已毁") as excinfo:
        io_download_module._rewrite_part_file(part, b"rewritten")

    # 关闭失败不顶替原始写入异常，写失败仍可经异常因果链观测。
    assert isinstance(excinfo.value.__cause__, OSError)
    assert str(excinfo.value.__cause__) == "写入失败"


def test_transient_file_lock_error_subclass_and_unified_message() -> None:
    """瞬时持锁经单点辅助归一为可重试子类，统一文案可被断言。"""
    assert issubclass(io_download_module.TransientFileLockError, RetryableDownloadError)

    with pytest.raises(
        io_download_module.TransientFileLockError, match="落盘写入瞬时持锁，重试"
    ) as excinfo:
        io_download_module._raise_transient_file_lock(
            "落盘写入", PermissionError(errno.EACCES, "simulated sharing violation")
        )

    assert isinstance(excinfo.value.__cause__, PermissionError)


async def test_download_decode_rewrite_locked_once_preserves_part_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """解码回退写回遭瞬时持锁（PermissionError）：保留压缩 .part，重试重解码后落盘。

    read_bytes 断言同时钉死状态改写晚于写回成功的次序：重排会使重试趟跳过解码
    直接落盘压缩字节。
    """
    real_open_write_fd = io_file.open_no_follow_write_fd
    rewrite_calls = {"count": 0}

    def _locked_once_open_write_fd(path: Path) -> int:
        rewrite_calls["count"] += 1
        # 首次打开是首趟数据写入，持锁发生在第二次（解码回退写回）。
        if rewrite_calls["count"] == 2:
            raise PermissionError(errno.EACCES, "simulated sharing violation")
        return real_open_write_fd(path)

    monkeypatch.setattr(io_download_module, "open_no_follow_write_fd", _locked_once_open_write_fd)
    manager = DownloadManager()
    session = _FakeSession([_compressed_success_response(gzip.compress(_DECODE_BODY))])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert result["file_size"] == len(_DECODE_BODY)
    assert save_path.read_bytes() == _DECODE_BODY
    # 持锁不毁数据：完成态保留不发网络请求，第二趟重解码后写回落盘。
    assert rewrite_calls["count"] == 3
    assert session._idx == 1
    assert not list(tmp_path.glob("*.part"))


async def test_download_decode_rewrite_write_failure_redownloads_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """解码回退写回在截断后写失败：作废完成态退回全量重下自愈，而非重解已毁文件。

    旧路径完成态保留会使重试直达终局，对已毁 .part 重解压得终态签名拒绝。
    """
    real_truncate = os.ftruncate
    real_write = os.write
    gate = {"rewrite": False, "fired": False}

    def _gated_truncate(fd: int, length: int) -> None:
        # 写回恒截断到 0，撕裂尾修正截断到已确认偏移；重试趟偏移归零后两处截断同形，
        # 误触发由 fired 单次置位吸收。
        if length == 0:
            gate["rewrite"] = True
        return real_truncate(fd, length)

    def _gated_write(fd: int, buffer: Buffer) -> int:
        if gate["rewrite"] and not gate["fired"]:
            gate["fired"] = True
            raise OSError("simulated write failure")
        return real_write(fd, buffer)

    monkeypatch.setattr(os, "ftruncate", _gated_truncate)
    monkeypatch.setattr(os, "write", _gated_write)
    manager = DownloadManager()
    session = _FakeSession(
        [
            _compressed_success_response(gzip.compress(_DECODE_BODY)),
            _compressed_success_response(gzip.compress(_DECODE_BODY)),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert result["file_size"] == len(_DECODE_BODY)
    assert save_path.read_bytes() == _DECODE_BODY
    # 写回失败作废完成态：重试退回全量重下发起新请求，而非对已毁 .part 重解码。
    assert gate["fired"] is True
    assert session._idx == 2
    assert not list(tmp_path.glob("*.part"))


async def test_download_decode_rewrite_persistent_fs_error_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """解码回退写段 ENOSPC：原样上抛走「文件系统永久错误」终态，不烧重试预算。"""
    real_truncate = os.ftruncate
    real_write = os.write
    gate = {"rewrite": False}

    def _gated_truncate(fd: int, length: int) -> None:
        # 写回恒截断到 0：门控只挂在解码回退的写段上。
        if length == 0:
            gate["rewrite"] = True
        return real_truncate(fd, length)

    def _gated_write(fd: int, buffer: Buffer) -> int:
        if gate["rewrite"]:
            raise OSError(errno.ENOSPC, "simulated no space left")
        return real_write(fd, buffer)

    monkeypatch.setattr(os, "ftruncate", _gated_truncate)
    monkeypatch.setattr(os, "write", _gated_write)
    manager = DownloadManager()
    session = _FakeSession([_compressed_success_response(gzip.compress(_DECODE_BODY))])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="文件系统永久错误") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 永久 errno 原样上抛走终态分类：单次尝试即失败，不进入退避重试。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_data_write_locked_once_retries_and_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """首趟数据写入遭瞬时持锁（PermissionError）：归可重试保留续传状态，重试后完整落盘。"""
    real_open_write_fd = io_file.open_no_follow_write_fd
    write_opens = {"count": 0}

    def _locked_once_open_write_fd(path: Path) -> int:
        write_opens["count"] += 1
        if write_opens["count"] == 1:
            raise PermissionError(errno.EACCES, "simulated sharing violation")
        return real_open_write_fd(path)

    monkeypatch.setattr(io_download_module, "open_no_follow_write_fd", _locked_once_open_write_fd)
    manager = DownloadManager()
    session = _FakeSession([_full_retry_response(_RESUME_ETAG), _full_retry_response(_RESUME_ETAG)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY
    # 删除该持锁分类会使 PermissionError 落"权限拒绝，不可重试"终态，attempts 断言即红。
    assert write_opens["count"] == 2
    assert session._idx == 2
    assert not list(tmp_path.glob("*.part"))


async def test_download_torn_tail_truncate_locked_once_retries_and_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """撕裂尾截断遭瞬时持锁（PermissionError）：保留续传状态重试，补齐后完整落盘。"""
    real_ftruncate = os.ftruncate
    truncate_calls = {"count": 0}

    def _locked_once_ftruncate(fd: int, length: int) -> None:
        truncate_calls["count"] += 1
        if truncate_calls["count"] == 1:
            raise PermissionError(errno.EACCES, "simulated sharing violation")
        return real_ftruncate(fd, length)

    monkeypatch.setattr(os, "ftruncate", _locked_once_ftruncate)
    manager = DownloadManager()
    session = _FakeSession(
        [
            _interrupted_first_attempt(),
            _resumed_response(_RESUME_SPLIT),
            _resumed_response(_RESUME_SPLIT),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 持锁趟文件未动、状态保留：第三趟仍自偏移 100 续传而非全量重下。
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert session.captured_headers[2]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert truncate_calls["count"] == 2


async def test_download_decode_read_locked_once_retries_and_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """解码回退读取遭瞬时持锁（PermissionError）：保留压缩 .part，重试重解码后落盘。"""
    real_open_read = io_file.open_no_follow_read
    read_calls = {"count": 0}

    def _locked_once_open_read(path: Path) -> Any:
        read_calls["count"] += 1
        if read_calls["count"] == 1:
            raise PermissionError(errno.EACCES, "simulated sharing violation")
        return real_open_read(path)

    monkeypatch.setattr(io_download_module, "open_no_follow_read", _locked_once_open_read)
    manager = DownloadManager()
    session = _FakeSession([_compressed_success_response(gzip.compress(_DECODE_BODY))])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert result["file_size"] == len(_DECODE_BODY)
    assert save_path.read_bytes() == _DECODE_BODY
    # 持锁不毁数据：完成态保留不发网络请求，第二趟重走解码与终局。
    assert read_calls["count"] == 2
    assert session._idx == 1


# ==================== 206 声明口径回归：越界交付、虚高总长与畸形 Content-Range ====================

# 越界测试体：PNG 签名 + 零填充至 600 字节，已确认偏移 500 与声明总长 600 构造越界交付。
_OVERFLOW_BODY = b"\x89PNG\r\n\x1a\n" + b"\x00" * 592
_OVERFLOW_SPLIT = 500


def _overflow_interrupted_attempt() -> _InterruptedResponse:
    """构造越界场景首趟：交付前 500 字节后中断，携带源校验器。"""
    return _InterruptedResponse(
        [_OVERFLOW_BODY[:_OVERFLOW_SPLIT]], _RESUME_ETAG, len(_OVERFLOW_BODY)
    )


def _overflow_resume_response(*, interrupted: bool = False) -> _FakeResponse:
    """构造越界 206：自偏移 500 交付 500 字节，而 Content-Range 声明总长仅 600。

    interrupted 为真时交付全部字节后断流，而非干净收尾。
    """
    tail = _OVERFLOW_BODY[_OVERFLOW_SPLIT:] + b"G" * 400
    response = _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": _RESUME_ETAG,
            "content-range": f"bytes {_OVERFLOW_SPLIT}-{_OVERFLOW_SPLIT + len(tail) - 1}/600",
            "content-length": str(len(tail)),
        },
        content_chunks=[] if interrupted else [tail],
    )
    if interrupted:
        response.content = cast("_FakeStreamContent", _InterruptedStreamContent([tail]))
    return response


async def test_download_resume_overflow_range_body_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """206 实际交付越过声明总长属对端违约：终态失败不 restart，不烧重试预算。"""
    manager = DownloadManager()
    session = _FakeSession([_overflow_interrupted_attempt(), _overflow_resume_response()])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="响应体过大") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 恒定超长源确定性违约，越界当趟即终态失败。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 2
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_resume_overflow_interrupted_delivery_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """越界交付叠加中途断流：异常趟落盘后复核越界按终态失败，完成态捷径不得伪成功。"""
    manager = DownloadManager()
    session = _FakeSession(
        [_overflow_interrupted_attempt(), _overflow_resume_response(interrupted=True)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="响应体过大") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 断流不遮蔽越界违约：异常当趟即终态失败，下一趟不落入完成态捷径持久化越界文件。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 2
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_constant_overlong_delivery_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """恒定超长源每次都交付多于声明总长的字节，单次尝试即终态失败，不再重试。"""
    overlong = _FakeResponse(
        status=200,
        headers={"content-type": "image/png", "content-length": str(len(_RESUME_BODY))},
        content_chunks=[_RESUME_BODY + b"G" * 40],
    )
    manager = DownloadManager()
    session = _FakeSession([overlong])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="响应体过大") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # attempts 计数锁定：越界交付单次尝试即上抛，不进入退避重试。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_resume_inflated_range_total_rejected_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """206 虚高 Content-Range 总长与 Content-Length 路径同口径触发文件过大早拒。"""
    inflated = _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": _RESUME_ETAG,
            "content-range": f"bytes {_RESUME_SPLIT}-{_RESUME_SPLIT + 9}/999999999",
            "content-length": "10",
        },
        content_chunks=[_RESUME_BODY[_RESUME_SPLIT : _RESUME_SPLIT + 10]],
    )
    manager = DownloadManager(max_file_size=1000)
    # 首趟无声明总长：总长交叉核对无从比对，虚高总长由上限早拒路径拦下。
    session = _FakeSession([_interrupted_unknown_total_attempt(), inflated])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="文件过大") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 早拒属终态错误，不空烧重试预算。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 2
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_resume_inflated_total_mismatch_beats_size_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """已知总长叠加虚高总长（超上限）：先判总长不符作废重下，重下后被上限早拒终态。

    总长核验先于上限早拒是有意的裁定顺序：表示已变化的证据优先于尺寸拒绝。
    """
    manager = DownloadManager(max_file_size=1000)
    oversized_full = _FakeResponse(
        status=200,
        headers={"content-type": "image/png", "content-length": "2000"},
    )
    session = _FakeSession(
        [
            _interrupted_first_attempt(),
            _etagless_resume_response(_RESUME_SPLIT, 999999999),
            oversized_full,
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="文件过大") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 虚高总长先触发总长不符作废重下，全量重下的超限 Content-Length 再被上限早拒。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session.captured_headers[2].get("Range") is None
    assert session._idx == 3
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize(
    "content_range",
    [
        pytest.param(None, id="missing"),
        pytest.param("bytes abc-def/xyz", id="malformed"),
        pytest.param("not-bytes 100-199/200", id="wrong-unit"),
    ],
)
async def test_download_resume_missing_content_range_restarts_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    content_range: str | None,
) -> None:
    """206 缺失或畸形 Content-Range：作废已存前缀退回全量，下一趟无 Range 拿 200 自愈。"""
    headers = {
        "content-type": "image/png",
        "etag": _RESUME_ETAG,
        "content-length": str(len(_RESUME_BODY) - _RESUME_SPLIT),
    }
    if content_range is not None:
        headers["content-range"] = content_range
    headless = _FakeResponse(
        status=206,
        headers=headers,
        content_chunks=[_RESUME_BODY[_RESUME_SPLIT:]],
    )
    manager = DownloadManager()
    session = _FakeSession(
        [_interrupted_first_attempt(), headless, _full_retry_response(_RESUME_ETAG)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 畸形 206 作废前缀后第三趟不再携带注定失败的同一 Range，退回全量请求。
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert "Range" not in session.captured_headers[2]
    # 作废前缀前先排空响应体，连接得以回池。
    assert headless.content._chunk_idx == 1


# ==================== 206 分片长推导：区间优先，Content-Length 仅作区间缺失时的回退 ====================


def _interrupted_unknown_total_attempt() -> _FakeResponse:
    """构造无声明总长的中断首趟：携带 ETag 而无 Content-Length，重试仍走续传。"""
    response = _FakeResponse(
        status=200,
        headers={"content-type": "image/png", "etag": _RESUME_ETAG},
    )
    response.content = cast(
        "_FakeStreamContent", _InterruptedStreamContent([_RESUME_BODY[:_RESUME_SPLIT]])
    )
    return response


def _explicit_range_unknown_total_resume_response(content_length: int) -> _FakeResponse:
    """构造总长未知、区间明确的 206：Content-Length 取传入值，供全量/分片两形态共用。"""
    remaining = _RESUME_BODY[_RESUME_SPLIT:]
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": _RESUME_ETAG,
            "content-range": f"bytes {_RESUME_SPLIT}-{len(_RESUME_BODY) - 1}/*",
            "content-length": str(content_length),
        },
        content_chunks=[remaining],
    )


@pytest.mark.parametrize(
    "content_length",
    [
        pytest.param(len(_RESUME_BODY) - _RESUME_SPLIT, id="segment-cl"),
        pytest.param(len(_RESUME_BODY), id="full-cl"),
    ],
)
async def test_download_resume_explicit_range_cl_variants_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    content_length: int,
) -> None:
    """区间明确、总长未知的 206 分片长以区间为准：全量或分片 Content-Length 均一次续传即落盘。

    全量长度 Content-Length 属违规变体，若按叠加偏移推导总长会虚高致短体误判，
    形成重试不动点。
    """
    manager = DownloadManager()
    session = _FakeSession(
        [
            _interrupted_first_attempt(),
            _explicit_range_unknown_total_resume_response(content_length),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"


def _no_end_range_resume_response(
    offset: int, delivered: int, content_length: int
) -> _FakeResponse:
    """构造无区间终点的 206（``bytes N-/*``）：Content-Length 作分片长回退基准。"""
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": _RESUME_ETAG,
            "content-range": f"bytes {offset}/*",
            "content-length": str(content_length),
        },
        content_chunks=[_RESUME_BODY[offset : offset + delivered]],
    )


def _ranged_unknown_total_resume_response(offset: int, delivered: int) -> _FakeResponse:
    """构造区间明确、总长未知的 206：交付恰满声明区间后流干净收尾。"""
    end = offset + delivered - 1
    return _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "etag": _RESUME_ETAG,
            "content-range": f"bytes {offset}-{end}/*",
        },
        content_chunks=[_RESUME_BODY[offset : offset + delivered]],
    )


def _interrupted_ranged_unknown_total_resume_response(offset: int, delivered: int) -> _FakeResponse:
    """构造区间明确、总长未知且交付后断流的 206：偏移推进而无干净收尾完成。"""
    response = _ranged_unknown_total_resume_response(offset, delivered)
    response.content = cast(
        "_FakeStreamContent",
        _InterruptedStreamContent([_RESUME_BODY[offset : offset + delivered]]),
    )
    return response


async def test_download_resume_no_end_range_falls_back_to_cl_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """无区间终点的 206 以 Content-Length 为分片长回退：短于分片可重试续传，补齐后落盘。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            _no_end_range_resume_response(_RESUME_SPLIT, 50, len(_RESUME_BODY) - _RESUME_SPLIT),
            _resumed_response(_RESUME_SPLIT + 50),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 续传趟接收 50 字节短于回退分片长 100，下一趟自偏移 150 续传补齐。
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert session.captured_headers[2]["Range"] == f"bytes={_RESUME_SPLIT + 50}-"


async def test_download_resume_unknown_total_clean_tail_end_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """首趟 chunked 200 中断、总长未知 206 干净收尾交付剩余全部：判完成落盘。

    出站区间恒开至表示尾，干净收尾即尾部已尽，不因总量未知陷入无限重试。
    """
    manager = DownloadManager()
    remaining = len(_RESUME_BODY) - _RESUME_SPLIT
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            _ranged_unknown_total_resume_response(_RESUME_SPLIT, remaining),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert result["file_size"] == len(_RESUME_BODY)
    assert save_path.read_bytes() == _RESUME_BODY


async def test_download_resume_unknown_total_interrupted_stream_retries_then_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """总长未知续传 206 中途断流：连接异常中断仍走可重试，自新偏移续传补齐落盘。"""
    manager = DownloadManager()
    tail = len(_RESUME_BODY) - _RESUME_SPLIT - 50
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            _interrupted_ranged_unknown_total_resume_response(_RESUME_SPLIT, 50),
            _ranged_unknown_total_resume_response(_RESUME_SPLIT + 50, tail),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 断流趟确认到 150 字节，下一趟自偏移 150 续传，截断不伪判完成。
    assert session.captured_headers[2]["Range"] == f"bytes={_RESUME_SPLIT + 50}-"


# ==================== 416 的不可满足声明：完成态与终态失败 ====================


def _unsatisfiable_416_response(declared_total: int, etag: str | None = None) -> _FakeResponse:
    """构造 416 响应：携带 RFC 9110 的 ``bytes */总长`` 声明当前表示总长。"""
    headers = {"content-type": "image/png", "content-range": f"bytes */{declared_total}"}
    if etag is not None:
        headers["etag"] = etag
    return _FakeResponse(
        status=416,
        headers=headers,
        content_chunks=[b"range-not-satisfiable"],
    )


def _unsatisfiable_unknown_total_416_response(etag: str | None = None) -> _FakeResponse:
    """构造 416 响应：携带总长未知的 ``bytes */*`` 不可满足声明。"""
    headers = {"content-type": "image/png", "content-range": "bytes */*"}
    if etag is not None:
        headers["etag"] = etag
    return _FakeResponse(
        status=416,
        headers=headers,
        content_chunks=[b"range-not-satisfiable"],
    )


async def test_download_resume_unknown_total_completes_via_416_declared_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """总长未知 206 断流收满后下趟 416 声明总长恰达已确认偏移：判完成态落盘。

    续传 206 须以断流收尾：干净收尾会直接判完成，416 完成路径无从到达。
    """
    manager = DownloadManager()
    remaining = len(_RESUME_BODY) - _RESUME_SPLIT
    unsatisfiable = _unsatisfiable_416_response(len(_RESUME_BODY))
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            _interrupted_ranged_unknown_total_resume_response(_RESUME_SPLIT, remaining),
            unsatisfiable,
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert result["file_size"] == len(_RESUME_BODY)
    assert save_path.read_bytes() == _RESUME_BODY
    # 第三趟 Range 判不可满足但总长声明恰等于已确认偏移：直接落盘，无第四趟全量重下。
    assert session.captured_headers[2]["Range"] == f"bytes={len(_RESUME_BODY)}-"
    assert session._idx == 3
    # 判完成态前先排空响应体，连接得以回池。
    assert unsatisfiable.content._chunk_idx == 1
    assert not list(tmp_path.glob("*.part"))


async def test_download_resume_416_unknown_total_declares_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """416 以 ``bytes */*`` 声明不可满足：开放式区间起点已达总长，判完成不作废前缀。"""
    manager = DownloadManager()
    remaining = len(_RESUME_BODY) - _RESUME_SPLIT
    unsatisfiable = _unsatisfiable_unknown_total_416_response()
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            _interrupted_ranged_unknown_total_resume_response(_RESUME_SPLIT, remaining),
            unsatisfiable,
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert result["file_size"] == len(_RESUME_BODY)
    assert save_path.read_bytes() == _RESUME_BODY
    # 总长未知的不可满足声明同样补记完成，无第四趟全量重下。
    assert session.captured_headers[2]["Range"] == f"bytes={len(_RESUME_BODY)}-"
    assert session._idx == 3
    assert unsatisfiable.content._chunk_idx == 1
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize(
    "build_unsatisfiable",
    [
        pytest.param(
            lambda: _unsatisfiable_416_response(len(_RESUME_BODY), '"resume-renewed"'),
            id="declared-total",
        ),
        pytest.param(
            lambda: _unsatisfiable_unknown_total_416_response('"resume-renewed"'),
            id="unknown-total",
        ),
    ],
)
async def test_download_resume_416_etag_mismatch_restarts_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    build_unsatisfiable: Callable[[], _FakeResponse],
) -> None:
    """416 判完成前同 206 口径核验表示身份：ETag 不符作废前缀退回全量，不判完成。"""
    manager = DownloadManager()
    unsatisfiable = build_unsatisfiable()
    remaining = len(_RESUME_BODY) - _RESUME_SPLIT
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            _interrupted_ranged_unknown_total_resume_response(_RESUME_SPLIT, remaining),
            unsatisfiable,
            _full_retry_response(_RESUME_ETAG),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 4
    assert save_path.read_bytes() == _RESUME_BODY
    # ETag 不符不判完成而作废前缀，第四趟退回全量、不再携带注定失败的同一 Range。
    assert session.captured_headers[2]["Range"] == f"bytes={len(_RESUME_BODY)}-"
    assert session.captured_headers[3].get("Range") is None
    # 作废前缀前先排空响应体，连接得以回池。
    assert unsatisfiable.content._chunk_idx == 1


async def test_download_resume_416_matching_etag_still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """416 携带与源校验器相符的 ETag：完成态判定不变，直接落盘无全量重下。"""
    manager = DownloadManager()
    remaining = len(_RESUME_BODY) - _RESUME_SPLIT
    unsatisfiable = _unsatisfiable_416_response(len(_RESUME_BODY), _RESUME_ETAG)
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            _interrupted_ranged_unknown_total_resume_response(_RESUME_SPLIT, remaining),
            unsatisfiable,
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert result["file_size"] == len(_RESUME_BODY)
    assert save_path.read_bytes() == _RESUME_BODY
    assert session._idx == 3
    assert not list(tmp_path.glob("*.part"))


def _malformed_total_416_response() -> _FakeResponse:
    """构造总长畸形的不可满足 416 声明（``bytes */abc``）。"""
    return _FakeResponse(
        status=416,
        headers={"content-type": "image/png", "content-range": "bytes */abc"},
        content_chunks=[b"range-not-satisfiable"],
    )


async def test_download_resume_416_malformed_unknown_total_restarts_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """``bytes */abc`` 总长畸形不并入 ``*/*`` 完成态：作废前缀退回全量重下自愈。"""
    manager = DownloadManager()
    malformed = _malformed_total_416_response()
    remaining = len(_RESUME_BODY) - _RESUME_SPLIT
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            _interrupted_ranged_unknown_total_resume_response(_RESUME_SPLIT, remaining),
            malformed,
            _full_retry_response(_RESUME_ETAG),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 4
    assert save_path.read_bytes() == _RESUME_BODY
    # 第三趟 416 声明不可满足但总长畸形：不判完成而作废前缀，第四趟退回全量。
    assert session.captured_headers[2]["Range"] == f"bytes={len(_RESUME_BODY)}-"
    assert session.captured_headers[3].get("Range") is None
    # 作废前缀前先排空响应体，连接得以回池。
    assert malformed.content._chunk_idx == 1


async def test_download_resume_416_declared_total_above_offset_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """416 声明总长大于已确认偏移属对端违约：重试请求逐字节相同，首现即终态失败。"""
    manager = DownloadManager()
    unsatisfiable = _unsatisfiable_416_response(len(_RESUME_BODY))
    session = _FakeSession(
        [
            _interrupted_unknown_total_attempt(),
            unsatisfiable,
            _unknown_total_resume_response(_RESUME_SPLIT, len(_RESUME_BODY) - _RESUME_SPLIT),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="区间应可满足") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 区间按声明本可满足：重试只能逐字节重发同一请求，首现即终态不烧重试预算。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert session._idx == 2
    # 判终态前先排空响应体，连接得以回池。
    assert unsatisfiable.content._chunk_idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


# 源站换图后的新表示测试体：与 _RESUME_BODY 同长而字节不同，锚定作废重下落盘的是新字节。
_RENEWED_BODY = b"\x89PNG\r\n\x1a\n" + b"\x01" * 192


def _renewed_full_response() -> _FakeResponse:
    """构造源站换图后的全量 200：携带新表示字节与新 ETag。"""
    return _FakeResponse(
        status=200,
        headers={
            "content-type": "image/png",
            "etag": '"resume-renewed"',
            "content-length": str(len(_RENEWED_BODY)),
        },
        content_chunks=[_RENEWED_BODY],
    )


@pytest.mark.parametrize(
    "build_unsatisfiable",
    [
        pytest.param(
            lambda: _unsatisfiable_416_response(_RESUME_SPLIT, _RESUME_ETAG),
            id="declared-total",
        ),
        pytest.param(
            lambda: _unsatisfiable_unknown_total_416_response(_RESUME_ETAG),
            id="unknown-total",
        ),
    ],
)
async def test_download_resume_416_total_mismatch_restarts_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_sleep: None,
    build_unsatisfiable: Callable[[], _FakeResponse],
) -> None:
    """已知总长下 416 完成态声明与已知总长不符：作废前缀退回全量，存新表示字节。

    ``bytes */100`` 恰等偏移或 ``*/*`` 无声明而总长大于偏移，均与已知总长 200
    矛盾，旧前缀不得判完成落盘；ETag 与源校验器一致，总长核验独立触发。
    """
    manager = DownloadManager()
    unsatisfiable = build_unsatisfiable()
    session = _FakeSession([_interrupted_first_attempt(), unsatisfiable, _renewed_full_response()])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RENEWED_BODY
    # 总长矛盾作废前缀后，第三趟退回全量、不再携带注定失败的同一 Range。
    assert session.captured_headers[1]["Range"] == f"bytes={_RESUME_SPLIT}-"
    assert "Range" not in session.captured_headers[2]
    # 作废前缀前先排空响应体，连接得以回池。
    assert unsatisfiable.content._chunk_idx == 1


async def test_download_resume_416_consistent_declared_total_keeps_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """416 声明总长与已知总长一致：维持既有「区间应可满足」终态，不被总长核验作废。"""
    manager = DownloadManager()
    unsatisfiable = _unsatisfiable_416_response(len(_RESUME_BODY), _RESUME_ETAG)
    session = _FakeSession(
        [_interrupted_first_attempt(), unsatisfiable, _full_retry_response(_RESUME_ETAG)]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="区间应可满足") as excinfo:
        await manager.download_image("https://example.com/img.png", save_path)

    # 总长一致时核验不触发：既有的对端违约终态裁决保持不变。
    assert not isinstance(excinfo.value, RetryableDownloadError)
    assert session._idx == 2
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


async def test_download_final_failure_cleans_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """重试耗尽后统一清理续传临时文件，目录无 .part 残留。"""
    manager = DownloadManager()
    responses = [_interrupted_first_attempt() for _ in range(manager.max_retries + 1)]
    session = _FakeSession(responses)
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="网络错误"):
        await manager.download_image("https://example.com/img.png", save_path)

    assert not save_path.exists()
    assert session._idx == manager.max_retries + 1
    assert not list(tmp_path.glob("*.part"))


# ==================== 完成态一致性：响应体已全部确认落盘后的重试不再发请求 ====================


def _interrupted_full_delivery() -> _InterruptedResponse:
    """构造全量字节交付后在终结块前断流的首趟响应，已确认字节已达总长。"""
    return _InterruptedResponse([_RESUME_BODY], _RESUME_ETAG, len(_RESUME_BODY))


async def test_download_completed_retry_skips_range_request_before_416(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """全量字节确认后断流：重试判定完成态直接落盘，Range 请求的 416 不会终态化。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _interrupted_full_delivery(),
            _FakeResponse(status=416, headers={"content-type": "image/png"}),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY
    # 完成态重试不发请求，预置的 416 响应未被消费。
    assert session._idx == 1
    assert not list(tmp_path.glob("*.part"))


async def test_download_completed_retry_finalizes_without_new_request_or_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """终局替换瞬时失败后的重试：重走既有终局，不另建临时文件、不留孤儿 .part。"""
    _install_replace_fail_once(monkeypatch)
    manager = DownloadManager()
    session = _FakeSession([_full_retry_response(_RESUME_ETAG)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY
    # 完成态重试不发请求也不另建临时文件，原临时文件经重试替换落盘。
    assert session._idx == 1
    assert not list(tmp_path.glob("*.part"))


async def test_download_completed_retry_rejects_empty_206_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """终局替换失败后病态空体 206 不得改写完成态：重试直接落盘原临时文件。

    预置的空体 206 起点与已确认偏移对齐；若完成态重试仍发请求并另建空临时文件，
    空文件会在终局替换时顶替归档完整数据。
    """
    _install_replace_fail_once(monkeypatch)
    empty_206 = _FakeResponse(
        status=206,
        headers={
            "content-type": "image/png",
            "content-range": f"bytes {len(_RESUME_BODY)}-{len(_RESUME_BODY) - 1}/{len(_RESUME_BODY)}",
            "content-length": "0",
        },
        content_chunks=[],
    )
    manager = DownloadManager()
    session = _FakeSession([_full_retry_response(_RESUME_ETAG), empty_206])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.read_bytes() == _RESUME_BODY
    assert session._idx == 1
    assert not list(tmp_path.glob("*.part"))


async def test_download_completed_retry_part_missing_redownloads_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """完成态重开发现 .part 被外部删除：作废已确认字节退回全量重下并成功落盘。"""
    _install_replace_fail_once(monkeypatch)
    real_finalize = io_file.finalize_atomic_replace
    finalize_calls = {"count": 0}

    async def _unlink_part_after_first_finalize(
        fd: int, path: Path, target: Path, *, fsync: bool
    ) -> None:
        finalize_calls["count"] += 1
        try:
            await real_finalize(fd, path, target, fsync=fsync)
        except OSError:
            if finalize_calls["count"] == 1:
                # 首次终局失败后模拟 .part 被外部删除。
                path.unlink(missing_ok=True)
            raise

    monkeypatch.setattr(
        io_download_module, "finalize_atomic_replace", _unlink_part_after_first_finalize
    )
    manager = DownloadManager()
    session = _FakeSession([_full_retry_response(_RESUME_ETAG), _full_retry_response(_RESUME_ETAG)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 完成态重开失败作废状态后重新发全量请求，而非重开空烧剩余重试。
    assert session._idx == 2
    assert not list(tmp_path.glob("*.part"))


async def test_download_completed_retry_locked_reopen_preserves_part_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """完成态重开遭共享冲突持锁（PermissionError）：保留 .part 数据重试重开后落盘。"""
    _install_replace_fail_once(monkeypatch)
    real_open_write_fd = io_file.open_no_follow_write_fd
    reopen_calls = {"count": 0}

    def _locked_once_open_write_fd(path: Path) -> int:
        reopen_calls["count"] += 1
        # 首次打开是首趟数据写入，持锁发生在第二次（完成态重开）。
        if reopen_calls["count"] == 2:
            raise PermissionError(errno.EACCES, "simulated sharing violation")
        return real_open_write_fd(path)

    monkeypatch.setattr(io_download_module, "open_no_follow_write_fd", _locked_once_open_write_fd)
    manager = DownloadManager()
    session = _FakeSession([_full_retry_response(_RESUME_ETAG)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 3
    assert save_path.read_bytes() == _RESUME_BODY
    # 持锁不毁数据：完成态保留不发全量重下请求，第二次重开直接落盘。
    assert reopen_calls["count"] == 3
    assert session._idx == 1
    assert not list(tmp_path.glob("*.part"))


def _install_replace_permission_locked(
    monkeypatch: pytest.MonkeyPatch, *, fail_always: bool = False
) -> dict[str, int]:
    """monkeypatch Path.replace 为抛 PermissionError 的持锁替身，返回调用计数。"""
    real_replace = Path.replace
    calls = {"count": 0}

    def _locked_replace(self: Path, target: Path) -> Path:
        calls["count"] += 1
        if fail_always or calls["count"] == 1:
            raise PermissionError(errno.EACCES, "simulated sharing violation")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _locked_replace)
    return calls


async def test_download_finalize_locked_replace_preserves_part_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """终局替换遭杀软瞬时持锁（PermissionError）：归为可重试保留 .part，重走终局后落盘。"""
    replace_calls = _install_replace_permission_locked(monkeypatch)
    manager = DownloadManager()
    session = _FakeSession([_full_retry_response(_RESUME_ETAG)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["attempts"] == 2
    assert save_path.read_bytes() == _RESUME_BODY
    # .part 全量数据跨重试保留：完成态重试不发请求，重开原文件直接替换落盘。
    assert replace_calls["count"] == 2
    assert session._idx == 1
    assert not list(tmp_path.glob("*.part"))


async def test_download_finalize_persistent_permission_lock_exhausts_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """终局持锁重试耗尽：按可重试错误终局失败并清理 .part，不落半成品。"""
    replace_calls = _install_replace_permission_locked(monkeypatch, fail_always=True)
    manager = DownloadManager(max_retries=2)
    session = _FakeSession([_full_retry_response(_RESUME_ETAG)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(io_download_module.TransientFileLockError, match="终局落盘瞬时持锁，重试"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 子类经重试循环按可重试处理：完成态重试不发请求，重试预算全部耗在重走终局上。
    assert replace_calls["count"] == manager.max_retries + 1
    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


# ==================== 部分写入：文件内容与已确认偏移严格同步 ====================


async def test_download_flush_partial_write_resubmits_remainder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.write 部分写入：按实际写入量切片缓冲后重提交，落盘内容与结果字节数一致。"""
    body = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    response = _FakeResponse(
        status=200,
        headers={"content-type": "image/png", "content-length": str(len(body))},
        content_chunks=[body[i : i + 100] for i in range(0, len(body), 100)],
    )
    session = _FakeSession([response])
    monkeypatch.setattr(io_download_module, "_WRITE_BATCH_BYTES", 64)
    real_write = os.write

    def _half_write(fd: int, data: bytearray) -> int:
        view = memoryview(data)
        half = len(data) // 2
        if half == 0:
            return real_write(fd, view)
        return real_write(fd, view[:half])

    monkeypatch.setattr(os, "write", _half_write)

    manager = DownloadManager()
    _patch_download_network(monkeypatch, manager, session)
    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert result["file_size"] == len(body)
    assert save_path.read_bytes() == body


# ==================== 新建临时文件跳过空操作截断定位，重试趟仍执行 ====================


def _install_recording_truncate_align(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """记录 ftruncate 与 lseek 系统调用次序；数据写入线程的 lseek 混入记录，截断相关断言以 ftruncate 锚定。"""
    real_ftruncate = os.ftruncate
    real_lseek = os.lseek

    def _recording_ftruncate(fd: int, size: int) -> None:
        calls.append("ftruncate")
        real_ftruncate(fd, size)

    def _recording_lseek(fd: int, pos: int, whence: int) -> int:
        if whence == os.SEEK_SET:
            calls.append("lseek")
        return real_lseek(fd, pos, whence)

    monkeypatch.setattr(os, "ftruncate", _recording_ftruncate)
    monkeypatch.setattr(os, "lseek", _recording_lseek)


async def test_download_fresh_temp_file_skips_noop_truncate_seek(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首趟新建空临时文件：截断为空操作，不派发执行器往返。"""
    calls: list[str] = []
    _install_recording_truncate_align(monkeypatch, calls)
    manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.read_bytes() == _PNG_BYTES
    assert "ftruncate" not in calls


async def test_download_resume_trip_truncates_once_and_reuses_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """重试趟 fd 已持有：截断撕裂尾部恰一次，临时文件全程仅创建一次。"""
    calls: list[str] = []
    _install_recording_truncate_align(monkeypatch, calls)
    real_ensure_file = _ResumeState.ensure_file
    ensure_calls = {"count": 0}

    def _counting_ensure_file(state: _ResumeState, directory: Path, suffix: str) -> bool:
        ensure_calls["count"] += 1
        return real_ensure_file(state, directory, suffix)

    monkeypatch.setattr(_ResumeState, "ensure_file", _counting_ensure_file)
    manager = DownloadManager()
    session = _FakeSession([_interrupted_first_attempt(), _resumed_response(_RESUME_SPLIT)])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.read_bytes() == _RESUME_BODY
    # 首趟新建空文件跳过空操作，仅续传趟截断一次；两批写入各定位一次，截断回调不再定位。
    assert calls.count("ftruncate") == 1
    assert calls.count("lseek") == 2
    # 续传趟在事件循环内直接取已持有 fd，不再派发 ensure_file 线程往返。
    assert ensure_calls["count"] == 1


# ==================== 取消语义：跳转窗口内取消的 fd 泄漏与 .part 孤儿防护 ====================


async def test_download_cancel_during_temp_open_cleans_fd_and_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消落在临时文件创建跳转：fd 由 dispose 关闭、.part 清理，无泄漏孤儿。"""
    manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, manager, session)

    real_open_temp_fd = io_file.open_temp_fd
    opened = threading.Event()
    release = threading.Event()
    closed = _install_close_counter(monkeypatch)

    def _gated_open_temp_fd(dir_path: Path, *, suffix: str = ".part") -> tuple[int, Path]:
        result = real_open_temp_fd(dir_path, suffix=suffix)
        opened.set()
        assert release.wait(5.0), "放行事件未触发"
        return result

    monkeypatch.setattr(io_download_module, "open_temp_fd", _gated_open_temp_fd)

    save_path = tmp_path / "out.png"
    task = asyncio.ensure_future(manager.download_image("https://example.com/img.png", save_path))
    assert await asyncio.to_thread(opened.wait, 5.0), "临时文件创建线程未进入执行"
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))
    # ensure_file 在工作线程内完成赋值，取消展开后 dispose 关闭 fd 恰一次。
    assert len(closed) == 1


async def test_download_cancel_during_truncate_lands_before_fd_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消落在续传趟截断跳转：在途 ftruncate 落定先于 dispose 关 fd，不误伤复用 fd。"""
    # 退避归零使续传趟立即到达，轮询保留真实 sleep 以让出事件循环等待门控线程。
    monkeypatch.setattr(random, "uniform", lambda low, high: 0.0)
    manager = DownloadManager(retry_delay=0)
    session = _FakeSession([_interrupted_first_attempt(), _resumed_response(_RESUME_SPLIT)])
    _patch_download_network(monkeypatch, manager, session)

    events: list[str] = []
    release = threading.Event()
    real_ftruncate = os.ftruncate

    def _gated_ftruncate(fd: int, size: int) -> None:
        real_ftruncate(fd, size)
        events.append("ftruncate_landed")
        assert release.wait(5.0), "放行事件未触发"
        events.append("op_settled")

    monkeypatch.setattr(os, "ftruncate", _gated_ftruncate)
    # 关闭时点附事件时序快照，锁定 dispose 关 fd 前在途截断已落定。
    closed = _install_close_counter(monkeypatch, record=lambda fd: (fd, list(events)))

    save_path = tmp_path / "out.png"
    task = asyncio.ensure_future(manager.download_image("https://example.com/img.png", save_path))
    # 等待 ftruncate 已执行并挂起在门控，构造取消时 op 在途的排序。
    for _ in range(200):
        if "ftruncate_landed" in events:
            break
        await asyncio.sleep(0.01)
    assert "ftruncate_landed" in events, "截断线程未进入执行"

    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))
    # 首次关闭为中断趟数据写入线程的 fd 自闭；dispose 关续传 fd 时在途截断已
    # 完整落定（含门控放行），二者不竞争。
    assert len(closed) == 2
    assert closed[-1][1] == ["ftruncate_landed", "op_settled"]


async def test_download_repeated_cancel_during_truncate_still_lands_before_fd_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """settle 窗口内的重复取消被吸收：在途 ftruncate 仍先于 dispose 关 fd 落定。"""
    # 退避归零使续传趟立即到达，轮询保留真实 sleep 以让出事件循环等待门控线程。
    monkeypatch.setattr(random, "uniform", lambda low, high: 0.0)
    manager = DownloadManager(retry_delay=0)
    session = _FakeSession([_interrupted_first_attempt(), _resumed_response(_RESUME_SPLIT)])
    _patch_download_network(monkeypatch, manager, session)

    events: list[str] = []
    release = threading.Event()
    real_ftruncate = os.ftruncate

    def _gated_ftruncate(fd: int, size: int) -> None:
        real_ftruncate(fd, size)
        events.append("ftruncate_landed")
        assert release.wait(5.0), "放行事件未触发"
        events.append("op_settled")

    monkeypatch.setattr(os, "ftruncate", _gated_ftruncate)
    closed = _install_close_counter(monkeypatch, record=lambda fd: (fd, list(events)))

    save_path = tmp_path / "out.png"
    task = asyncio.ensure_future(manager.download_image("https://example.com/img.png", save_path))
    for _ in range(200):
        if "ftruncate_landed" in events:
            break
        await asyncio.sleep(0.01)
    assert "ftruncate_landed" in events, "截断线程未进入执行"

    task.cancel()  # 取消一：打断外层 shield 进入落定等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消二：落定等待吸收后继续等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消三：仍被吸收，放行以 op 落定为界。
    await asyncio.sleep(0.05)
    assert not task.done(), "重复取消不提前放行，落定等待保持"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))
    # 重复取消不令 dispose 抢先关 fd：在途截断完整落定后才关闭，无同号 fd 误伤；
    # 首次关闭为中断趟数据写入线程的 fd 自闭。
    assert len(closed) == 2
    assert closed[-1][1] == ["ftruncate_landed", "op_settled"]


async def test_download_repeated_cancel_during_write_releases_and_op_closes_own_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据写入落定等待为有界档：重复取消放行任务退出，写入线程自闭 fd 不误伤。"""
    manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, manager, session)

    events: list[str] = []
    release = threading.Event()
    real_write = os.write

    def _gated_write(fd: int, data: bytearray) -> int:
        events.append("write_entered")
        assert release.wait(5.0), "放行事件未触发"
        count = real_write(fd, data)
        events.append("op_settled")
        return count

    monkeypatch.setattr(os, "write", _gated_write)

    real_open_write_fd = io_file.open_no_follow_write_fd
    write_op_fds: list[int] = []

    def _recording_open_write_fd(path: Path) -> int:
        fd = real_open_write_fd(path)
        write_op_fds.append(fd)
        return fd

    monkeypatch.setattr(io_download_module, "open_no_follow_write_fd", _recording_open_write_fd)
    # 关闭时点附事件时序快照，锁定各 fd 关闭与门控时序的关系。
    closed = _install_close_counter(monkeypatch, record=lambda fd: (fd, list(events)))

    save_path = tmp_path / "out.png"
    task = asyncio.ensure_future(manager.download_image("https://example.com/img.png", save_path))
    for _ in range(200):
        if "write_entered" in events:
            break
        await asyncio.sleep(0.01)
    assert "write_entered" in events, "写入线程未进入执行"

    task.cancel()  # 取消一：打断外层 shield 进入落定等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消二：落定等待吸收首个取消继续等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消三：有界档打断落定等待，放行不等挂起的写入。
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled(), "病态存储挂起不再钉死任务，并发槽得以释放"

    # 取消放行即 dispose 关闭续传 fd，此刻写入仍挂起，二者分属不同 fd。
    assert write_op_fds
    assert len(closed) == 1
    assert closed[0][0] != write_op_fds[0]

    release.set()  # 写入在续传 fd 关闭后照常落地，线程内 fd 自闭完整落定。
    for _ in range(200):
        if len(closed) == 2:
            break
        await asyncio.sleep(0.01)

    assert closed[1] == (write_op_fds[0], ["write_entered", "op_settled"])
    assert not save_path.exists()


async def test_download_cancel_during_reopen_cleans_fd_and_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """完成态重试的重开跳转被取消：已重开 fd 就地关闭，.part 清理无孤儿。"""
    _install_replace_fail_once(monkeypatch)
    manager = DownloadManager()
    session = _FakeSession([_full_retry_response(_RESUME_ETAG)])
    _patch_download_network(monkeypatch, manager, session)

    real_open_write_fd = io_file.open_no_follow_write_fd
    reopened = threading.Event()
    release = threading.Event()
    opened_fds: list[int] = []
    open_calls = {"count": 0}
    closed = _install_close_counter(monkeypatch)

    def _gated_open_write_fd(path: Path) -> int:
        open_calls["count"] += 1
        # 首次打开是首趟数据写入，门控只挂在完成态重开上。
        if open_calls["count"] < 2:
            return real_open_write_fd(path)
        fd = real_open_write_fd(path)
        opened_fds.append(fd)
        reopened.set()
        assert release.wait(5.0), "放行事件未触发"
        return fd

    monkeypatch.setattr(io_download_module, "open_no_follow_write_fd", _gated_open_write_fd)

    save_path = tmp_path / "out.png"
    task = asyncio.ensure_future(manager.download_image("https://example.com/img.png", save_path))
    assert await asyncio.to_thread(reopened.wait, 5.0), "重开线程未进入执行"
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))
    # 首趟数据写入与终局各自闭一次，重开的 fd 经取消善后再关闭恰一次，无泄漏。
    assert len(closed) == 3
    assert opened_fds and closed[-1] == opened_fds[0]
