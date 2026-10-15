"""files 守护测试：open_no_follow_read 与 open_no_follow_write_fd 的防换链打开、
open_regular_read 的常规文件打开序列与 open_packaged_read 的符号链接布局解析、
finalize_atomic_replace 落盘终局、atomic_replace_from_fd_sync 同步原子落盘。

open_no_follow_read 覆盖平台 O_NOFOLLOW、monkeypatch 模拟不支持时的 lstat/S_ISLNK 兜底
与正常读取三种场景。Windows 创建符号链接需特权或开发者模式，相关用例经共享建链原语
在创建能力缺失时 skip。
"""

import asyncio
import os
import stat
import sys
import threading
from pathlib import Path
from typing import IO, Any

import pytest

from seedream_mcp.utils.io import files as io_file
from seedream_mcp.utils.io.files import (
    NonRegularFileError,
    SymlinkRejectedError,
    atomic_replace_from_fd_sync,
    finalize_atomic_replace,
    open_no_follow_read,
    open_no_follow_write_fd,
    open_packaged_read,
    open_regular_read,
    open_temp_fd,
)
from seedream_mcp.utils.io.path import ResolveResultCache, resolve_rejecting_cycle

from _lib.os_fakes import (
    _install_close_counter,
    _install_fsync_counter,
    _install_gated_path_replace,
    _make_file_link,
)


def _make_symlink(tmp_path: Path, name: str) -> Path:
    """创建指向含固定内容真实文件的符号链接，返回链接路径；建链能力缺失经共享原语跳过。"""
    target = tmp_path / f"{name}_target.bin"
    target.write_bytes(b"secret")
    link = tmp_path / f"{name}_link.bin"
    _make_file_link(target, link)
    return link


def test_open_no_follow_read_returns_file_content(tmp_path: Path) -> None:
    """正常文件：open_no_follow_read 返回二进制只读对象，内容正确。"""
    path = tmp_path / "input.bin"
    path.write_bytes(b"hello")

    with open_no_follow_read(path) as handle:
        assert handle.read() == b"hello"


def test_open_no_follow_read_rejects_symlink_when_supported(tmp_path: Path) -> None:
    """平台支持 O_NOFOLLOW 时，符号链接读取以 SymlinkRejectedError 拒绝。"""
    if not getattr(os, "O_NOFOLLOW", 0):
        pytest.skip("当前平台不支持 O_NOFOLLOW")
    link = _make_symlink(tmp_path, "read_native")

    with pytest.raises(SymlinkRejectedError):
        open_no_follow_read(link)


def test_open_no_follow_read_fallback_rejects_symlink_without_no_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟平台不支持 O_NOFOLLOW：lstat/S_ISLNK 兜底分支拒绝符号链接读取。"""
    link = _make_symlink(tmp_path, "read_fallback")

    # 强制 no_follow 取值为 0，触发 lstat/S_ISLNK 兜底分支
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
    with pytest.raises(OSError, match="拒绝读取符号链接") as exc_info:
        open_no_follow_read(link)
    assert isinstance(exc_info.value, SymlinkRejectedError)


def test_open_no_follow_fallback_allows_normal_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟不支持 O_NOFOLLOW 时，正常文件读取仍成功，兜底分支不误伤。"""
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)

    path = tmp_path / "plain.bin"
    path.write_bytes(b"ok")

    with open_no_follow_read(path) as handle:
        assert handle.read() == b"ok"


def test_open_no_follow_fallback_rejects_fstat_toctou_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fstat 与 lstat 的 st_ino 不一致时拒绝打开，闭合 TOCTOU 竞态。

    monkeypatch os.fstat 返回不同 inode，模拟校验与打开之间最终分量被替换的场景。
    """
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
    path = tmp_path / "plain.bin"
    path.write_bytes(b"data")

    real_fstat = os.fstat

    def _fake_fstat(fd: int) -> os.stat_result:
        st = real_fstat(fd)
        # 构造不同 inode 的 stat_result，触发同一性复核拒绝
        return os.stat_result(
            (
                st.st_mode,
                st.st_ino + 1,
                st.st_dev,
                st.st_nlink,
                st.st_uid,
                st.st_gid,
                st.st_size,
                st.st_atime,
                st.st_mtime,
                st.st_ctime,
            )
        )

    monkeypatch.setattr(os, "fstat", _fake_fstat)

    with pytest.raises(SymlinkRejectedError, match="被替换"):
        open_no_follow_read(path)


def _install_open_flags_capture(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """monkeypatch os.open 为标志位记录透传，返回捕获列表。"""
    captured: list[int] = []
    real_open = os.open

    def _capturing_open(name: str, flags: int, *args: Any, **kwargs: Any) -> int:
        captured.append(flags)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", _capturing_open)
    return captured


def _assert_nonblocking_guard_open_flags(handle: IO[bytes], captured: list[int]) -> None:
    """断言 POSIX 打开携带 O_NONBLOCK|O_NOCTTY 防护位且交出前已清除，Windows 按裸 O_RDONLY 打开。"""
    if sys.platform != "win32":
        assert captured == [os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY]
        import fcntl

        served_flags = fcntl.fcntl(handle.fileno(), fcntl.F_GETFL)
        assert not served_flags & os.O_NONBLOCK
    else:
        # Windows 无两类标志，兜底路径按原 O_RDONLY 形态打开。
        assert captured == [os.O_RDONLY]


def test_open_no_follow_read_open_flags_carry_nonblocking_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """通用读取入口同样在 POSIX 打开阶段携带 O_NONBLOCK|O_NOCTTY，防 FIFO 等特殊文件路径阻塞 open。"""
    path = tmp_path / "input.bin"
    path.write_bytes(b"data")
    captured = _install_open_flags_capture(monkeypatch)

    with open_no_follow_read(path) as handle:
        _assert_nonblocking_guard_open_flags(handle, captured)
        assert handle.read() == b"data"


def test_open_no_follow_read_rejects_non_regular_file() -> None:
    """字符设备等非常规文件在打开单点拒绝，不交出会阻塞读取的句柄。"""
    with pytest.raises(NonRegularFileError, match="拒绝读取非常规文件"):
        open_no_follow_read(Path(os.devnull))


@pytest.mark.skipif(sys.platform == "win32", reason="os.mkfifo 仅 POSIX 提供")
def test_open_no_follow_read_fifo_rejected_without_blocking(tmp_path: Path) -> None:
    """FIFO 经非阻塞打开后在打开单点按形态拒绝，不进入后续阻塞读取。"""
    mkfifo = getattr(os, "mkfifo", None)
    assert mkfifo is not None
    fifo = tmp_path / "fifo-input.bin"
    mkfifo(fifo)

    with pytest.raises(NonRegularFileError):
        open_no_follow_read(fifo)


# ==================== open_no_follow_write_fd 写路径打开 ====================


def test_open_no_follow_write_fd_returns_writable_fd(tmp_path: Path) -> None:
    """正常文件：返回可写 fd，经 fd 写入的内容落盘。"""
    path = tmp_path / "out.bin"
    path.write_bytes(b"old")

    fd = open_no_follow_write_fd(path)
    os.write(fd, b"new")
    os.close(fd)

    assert path.read_bytes() == b"new"


def test_open_no_follow_write_fd_rejects_symlink_when_supported(tmp_path: Path) -> None:
    """平台支持 O_NOFOLLOW 时，符号链接写入以 SymlinkRejectedError 拒绝。"""
    if not getattr(os, "O_NOFOLLOW", 0):
        pytest.skip("当前平台不支持 O_NOFOLLOW")
    link = _make_symlink(tmp_path, "write_native")

    with pytest.raises(SymlinkRejectedError):
        open_no_follow_write_fd(link)


def test_open_no_follow_write_fd_fallback_rejects_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟平台不支持 O_NOFOLLOW：lstat/S_ISLNK 兜底分支拒绝符号链接写入。"""
    link = _make_symlink(tmp_path, "write_fallback")

    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
    with pytest.raises(OSError, match="拒绝写入符号链接") as exc_info:
        open_no_follow_write_fd(link)
    assert isinstance(exc_info.value, SymlinkRejectedError)


def test_open_no_follow_write_fd_fallback_allows_normal_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟不支持 O_NOFOLLOW 时，正常文件写入仍成功，兜底分支不误伤。"""
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)

    path = tmp_path / "plain.bin"
    path.write_bytes(b"old")

    fd = open_no_follow_write_fd(path)
    os.write(fd, b"new")
    os.close(fd)

    assert path.read_bytes() == b"new"


def test_open_no_follow_write_fd_open_flags_carry_nonblocking_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写路径 POSIX 打开携带 O_NONBLOCK|O_NOCTTY 防 FIFO 路径阻塞 open，交出前恢复阻塞语义。"""
    path = tmp_path / "w.bin"
    path.write_bytes(b"data")
    captured = _install_open_flags_capture(monkeypatch)

    fd = open_no_follow_write_fd(path)
    try:
        if sys.platform != "win32":
            import fcntl

            assert captured == [os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY]
            assert not fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK
        else:
            # Windows 无两类标志，兜底路径按 O_WRONLY|O_BINARY 形态打开。
            assert captured == [os.O_WRONLY | os.O_BINARY]
    finally:
        os.close(fd)


@pytest.mark.skipif(sys.platform == "win32", reason="os.mkfifo 仅 POSIX 提供")
def test_open_no_follow_write_fd_fifo_rejected_without_blocking(tmp_path: Path) -> None:
    """FIFO 写路径经非阻塞打开后在打开单点按形态拒绝，不阻塞工作线程。"""
    mkfifo = getattr(os, "mkfifo", None)
    assert mkfifo is not None
    fifo = tmp_path / "fifo-out.bin"
    mkfifo(fifo)
    # 预置非阻塞读端使 O_WRONLY 打开即时返回，到达 fstat 形态拒绝分支。
    read_fd = os.open(fifo, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        with pytest.raises(NonRegularFileError, match="拒绝写入非常规文件"):
            open_no_follow_write_fd(fifo)
    finally:
        os.close(read_fd)


# ==================== open_regular_read 常规文件打开序列 ====================


def test_open_regular_read_returns_handle_and_stat(tmp_path: Path) -> None:
    """常规文件：返回可读句柄与其 fstat 结果，stat 与句柄描述同一 inode。"""
    path = tmp_path / "page.html"
    path.write_bytes(b"<html></html>")

    opened = open_regular_read(path)

    assert opened is not None
    handle, stat_result = opened
    try:
        assert handle.read() == b"<html></html>"
        assert stat.S_ISREG(stat_result.st_mode)
        assert stat_result.st_size == len(b"<html></html>")
    finally:
        handle.close()


def test_open_regular_read_non_regular_file_returns_none() -> None:
    """字符设备等非常规文件在打开单点拒绝，open_regular_read 归 None 按缺失路由。"""
    assert open_regular_read(Path(os.devnull)) is None


def test_open_regular_read_directory_named_like_image_returns_none(tmp_path: Path) -> None:
    """目录路径归 None：POSIX 由句柄 fstat 判定，Windows 的 EACCES 经路径 stat 分类。"""
    directory = tmp_path / "图片(evil.png)"
    directory.mkdir()

    assert open_regular_read(directory) is None


def test_open_regular_read_permission_error_on_regular_file_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """常规文件的 PermissionError 原样上抛，不被目录分类误吞为缺失。"""
    import errno
    from typing import IO

    import seedream_mcp.utils.io.files as io_file_module

    path = tmp_path / "plain.bin"
    path.write_bytes(b"data")

    def _denied(target: object, **_kwargs: object) -> tuple[IO[bytes], os.stat_result]:
        raise PermissionError(errno.EACCES, "simulated EACCES", str(target))

    monkeypatch.setattr(io_file_module, "_open_no_follow_read_with_stat", _denied)

    with pytest.raises(PermissionError):
        open_regular_read(path)


def test_open_regular_read_permission_error_stat_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分类用 stat 失败时上抛的仍是原 PermissionError，不泄漏 stat 自身的 OSError。"""
    import errno
    from typing import IO

    import seedream_mcp.utils.io.files as io_file_module

    def _denied(target: object, **_kwargs: object) -> tuple[IO[bytes], os.stat_result]:
        raise PermissionError(errno.EACCES, "simulated EACCES", str(target))

    monkeypatch.setattr(io_file_module, "_open_no_follow_read_with_stat", _denied)

    with pytest.raises(PermissionError):
        open_regular_read(tmp_path / "missing.bin")


def test_open_regular_read_fstat_failure_closes_fd_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fstat 抛错：fd 先关闭再原样上抛 OSError，不依赖 GC 释放。"""
    path = tmp_path / "plain.bin"
    path.write_bytes(b"data")
    opened: list[int] = []
    closed: list[int] = []
    real_open = os.open
    real_close = os.close

    def _recording_open(name: str, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = real_open(name, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    def _recording_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    def _failing_fstat(fd: int) -> os.stat_result:
        del fd
        raise OSError("simulated ESTALE")

    monkeypatch.setattr(os, "open", _recording_open)
    monkeypatch.setattr(os, "close", _recording_close)
    monkeypatch.setattr(os, "fstat", _failing_fstat)

    with pytest.raises(OSError, match="simulated ESTALE"):
        open_regular_read(path)
    assert opened
    assert set(opened) <= set(closed)


def test_open_regular_read_symlink_rejection_propagates(tmp_path: Path) -> None:
    """符号链接拒绝原样上抛，由调用方按各自口径路由。"""
    link = _make_symlink(tmp_path, "regular_read")

    with pytest.raises(SymlinkRejectedError):
        open_regular_read(link)


def test_open_packaged_read_resolves_symlinked_layout(tmp_path: Path) -> None:
    """逐文件符号链接布局解析到真实目标后按同一序列打开，内容与 stat 同源。"""
    link = _make_symlink(tmp_path, "packaged_read")

    opened = open_packaged_read(link)

    assert opened is not None
    handle, stat_result = opened
    try:
        assert handle.read() == b"secret"
        assert stat_result.st_size == len(b"secret")
    finally:
        handle.close()


def test_open_packaged_read_rejects_link_cycle(tmp_path: Path) -> None:
    """链接环布局经严格解析拒绝，OSError 原样上抛交调用方归档。"""
    cycle = tmp_path / "cycle_link.bin"
    _make_file_link(cycle, cycle)

    with pytest.raises(OSError):
        open_packaged_read(cycle)


def _install_open_path_counter(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """monkeypatch os.open 为路径记录透传，返回按序的打开尝试路径列表。"""
    opened: list[str] = []
    real_open = os.open

    def _counting_open(name: str, flags: int, *args: Any, **kwargs: Any) -> int:
        opened.append(str(name))
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", _counting_open)
    return opened


def _install_resolve_counter(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """monkeypatch io_file 的 resolve_rejecting_cycle 为路径记录透传，返回按序的解析路径列表。"""
    resolved: list[str] = []

    def _counting_resolve(path: Path) -> Path:
        resolved.append(str(path))
        return resolve_rejecting_cycle(path)

    monkeypatch.setattr(io_file, "resolve_rejecting_cycle", _counting_resolve)
    return resolved


def test_open_packaged_read_plain_file_opens_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非链接路径直接按 no-follow 打开一次，不付符号链接布局的拒绝重开轮次。"""
    path = tmp_path / "plain.bin"
    path.write_bytes(b"data")
    opened = _install_open_path_counter(monkeypatch)

    result = open_packaged_read(path)

    assert result is not None
    handle, _stat_result = result
    try:
        assert handle.read() == b"data"
    finally:
        handle.close()
    assert opened == [str(path)]


def test_open_packaged_read_symlink_layout_falls_back_to_resolved_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """符号链接布局经拒绝异常回退解析，最终打开落在解析后的真实目标上。"""
    link = _make_symlink(tmp_path, "packaged_fallback")
    target = tmp_path / "packaged_fallback_target.bin"
    opened = _install_open_path_counter(monkeypatch)

    result = open_packaged_read(link)

    assert result is not None
    handle, _stat_result = result
    try:
        assert handle.read() == b"secret"
    finally:
        handle.close()
    # 有 O_NOFOLLOW 的平台先付一次注定失败的打开，至多两轮后落在真实目标。
    assert opened[-1] == str(target.resolve())
    assert len(opened) <= 2


def test_open_packaged_read_resolved_target_cached_for_repeat_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """符号链接布局二次读取命中已解析目标缓存，不再付链接路径打开与 realpath。"""
    link = _make_symlink(tmp_path, "packaged_cached")
    target = (tmp_path / "packaged_cached_target.bin").resolve()
    opened = _install_open_path_counter(monkeypatch)
    resolves = _install_resolve_counter(monkeypatch)

    for _ in range(2):
        result = open_packaged_read(link)
        assert result is not None
        handle, _stat_result = result
        try:
            assert handle.read() == b"secret"
        finally:
            handle.close()

    # 解析仅首轮付一次，二轮起不触链接路径、单开真实目标。
    assert resolves == [str(link)]
    assert opened.count(str(target)) == 2
    assert opened.count(str(link)) <= 1


def test_open_packaged_read_resolve_cache_evicts_beyond_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存超上限按最久未用序逐出，被逐出的链接重读重新付解析轮次。"""
    monkeypatch.setattr(io_file, "_packaged_resolve_cache", ResolveResultCache[Path](1))
    first = _make_symlink(tmp_path, "packaged_evict_first")
    second = _make_symlink(tmp_path, "packaged_evict_second")
    resolves = _install_resolve_counter(monkeypatch)

    open_packaged_read(first)
    open_packaged_read(second)
    result = open_packaged_read(first)

    assert result is not None
    handle, _stat_result = result
    try:
        assert handle.read() == b"secret"
    finally:
        handle.close()
    # 上限 1 时 second 落缓存逐出 first，重读 first 重新付解析。
    assert resolves == [str(first), str(second), str(first)]


def test_open_packaged_read_resolve_cache_cleared_by_lifespan_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨用例隔离回归：复位协议清空解析缓存，原路径重新付解析轮次。

    布局拒绝经 open_regular_read 边界模拟，符号链接创建能力缺失的平台同样可跑。
    """
    import errno

    from seedream_mcp.server import runtime

    link = tmp_path / "packaged_reset_link.bin"
    target = tmp_path / "packaged_reset_target.bin"
    target.write_bytes(b"secret")
    real_open_regular_read = io_file.open_regular_read
    resolves: list[str] = []

    def _layout_rejecting_open(
        path: io_file.PathLike,
    ) -> tuple[IO[bytes], os.stat_result] | None:
        if str(path) == str(link):
            raise SymlinkRejectedError(errno.ELOOP, "模拟符号链接布局", str(path))
        return real_open_regular_read(path)

    def _counting_resolve(path: Path) -> Path:
        resolves.append(str(path))
        return target

    monkeypatch.setattr(io_file, "open_regular_read", _layout_rejecting_open)
    monkeypatch.setattr(io_file, "resolve_rejecting_cycle", _counting_resolve)

    first = open_packaged_read(link)
    assert first is not None
    first[0].close()
    assert io_file._packaged_resolve_cache
    assert resolves == [str(link)]

    runtime._reset_lifespan_state()

    assert not io_file._packaged_resolve_cache
    second = open_packaged_read(link)
    assert second is not None
    handle, _stat_result = second
    try:
        assert handle.read() == b"secret"
    finally:
        handle.close()
    assert resolves == [str(link), str(link)]


def test_open_regular_read_missing_file_propagates_oserror(tmp_path: Path) -> None:
    """缺失路径的 OSError 原样上抛，不吞不换。"""
    with pytest.raises(FileNotFoundError):
        open_regular_read(tmp_path / "missing.bin")


def test_open_regular_read_open_flags_carry_nonblocking_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX 打开阶段携带 O_NONBLOCK|O_NOCTTY 防特殊文件路径阻塞 open 钉死工作线程，验明常规文件后清除非阻塞位。"""
    path = tmp_path / "page.html"
    path.write_bytes(b"<html></html>")
    captured = _install_open_flags_capture(monkeypatch)
    opened = open_regular_read(path)

    assert opened is not None
    handle, _stat_result = opened
    try:
        _assert_nonblocking_guard_open_flags(handle, captured)
        assert handle.read() == b"<html></html>"
    finally:
        handle.close()


@pytest.mark.skipif(sys.platform == "win32", reason="os.mkfifo 仅 POSIX 提供")
def test_open_regular_read_fifo_returns_none_without_blocking(tmp_path: Path) -> None:
    """FIFO 路径经非阻塞打开进 fstat 判定后归 None，不无限阻塞工作线程。"""
    fifo = tmp_path / "fifo-page.html"
    mkfifo = getattr(os, "mkfifo", None)
    assert mkfifo is not None
    mkfifo(fifo)

    assert open_regular_read(fifo) is None


# ==================== finalize_atomic_replace 落盘终局 ====================


async def test_finalize_atomic_replace_success_replaces_and_leaves_no_temp(
    tmp_path: Path,
) -> None:
    """成功路径：可选 fsync 后关闭 fd，os.replace 原子替换，目录内仅最终文件。"""
    final = tmp_path / "out.bin"
    fd, temp_path = open_temp_fd(tmp_path, suffix=".part")
    with os.fdopen(fd, "wb", closefd=False) as f:
        f.write(b"payload")

    await finalize_atomic_replace(fd, temp_path, final, fsync=False)

    assert final.read_bytes() == b"payload"
    assert list(tmp_path.iterdir()) == [final]


async def test_finalize_atomic_replace_fsync_enabled_calls_os_fsync_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fsync=True 时替换前对 fd 执行 os.fsync 恰好一次。"""
    fsync_calls = _install_fsync_counter(monkeypatch)
    final = tmp_path / "out.bin"
    fd, temp_path = open_temp_fd(tmp_path, suffix=".part")
    with os.fdopen(fd, "wb", closefd=False) as f:
        f.write(b"payload")

    await finalize_atomic_replace(fd, temp_path, final, fsync=True)

    assert final.read_bytes() == b"payload"
    assert len(fsync_calls) == 1


async def test_finalize_atomic_replace_fsync_disabled_skips_os_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fsync=False 时不调用 os.fsync。"""
    fsync_calls = _install_fsync_counter(monkeypatch)
    final = tmp_path / "out.bin"
    fd, temp_path = open_temp_fd(tmp_path, suffix=".part")
    with os.fdopen(fd, "wb", closefd=False) as f:
        f.write(b"payload")

    await finalize_atomic_replace(fd, temp_path, final, fsync=False)

    assert final.read_bytes() == b"payload"
    assert fsync_calls == []


async def test_finalize_atomic_replace_close_failure_does_not_block_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fsync 成功后 os.close 抛 OSError：静默降级不阻断，os.replace 照常执行。"""
    final = tmp_path / "out.bin"
    fd, temp_path = open_temp_fd(tmp_path, suffix=".part")
    with os.fdopen(fd, "wb", closefd=False) as f:
        f.write(b"payload")

    real_close = os.close

    def _close_then_raise(passed_fd: int) -> None:
        # 先真实关闭再上报失败：模拟关闭已生效而调用方仍收到错误的形态。
        real_close(passed_fd)
        raise OSError("simulated close failure")

    monkeypatch.setattr(os, "close", _close_then_raise)

    await finalize_atomic_replace(fd, temp_path, final, fsync=True)

    assert final.read_bytes() == b"payload"
    assert not temp_path.exists()


async def test_finalize_atomic_replace_cancel_during_replace_keeps_replaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消落在 replace 等待层：脱离的替换线程被等待落定，最终文件保留。"""
    replace_running, replace_release = _install_gated_path_replace(monkeypatch)
    final = tmp_path / "out.bin"
    fd, temp_path = open_temp_fd(tmp_path, suffix=".part")
    with os.fdopen(fd, "wb", closefd=False) as f:
        f.write(b"payload")

    task = asyncio.ensure_future(finalize_atomic_replace(fd, temp_path, final, fsync=False))
    assert await asyncio.to_thread(replace_running.wait, 5.0), "替换线程未进入执行"
    task.cancel()
    replace_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert final.read_bytes() == b"payload"


async def test_finalize_atomic_replace_repeated_cancel_during_fsync_lands_before_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fsync 落定等待吸收重复取消：在途 fsync 先于 finally 关 fd 落定，不竞争同号 fd。"""
    events: list[str] = []
    release = threading.Event()
    real_fsync = os.fsync

    def _gated_fsync(fd: int) -> None:
        real_fsync(fd)
        events.append("fsync_landed")
        assert release.wait(5.0), "放行事件未触发"
        events.append("op_settled")

    monkeypatch.setattr(os, "fsync", _gated_fsync)
    closed = _install_close_counter(monkeypatch, record=lambda fd: (fd, list(events)))

    final = tmp_path / "out.bin"
    fd, temp_path = open_temp_fd(tmp_path, suffix=".part")
    with os.fdopen(fd, "wb", closefd=False) as f:
        f.write(b"payload")

    task = asyncio.ensure_future(finalize_atomic_replace(fd, temp_path, final, fsync=True))
    for _ in range(200):
        if "fsync_landed" in events:
            break
        await asyncio.sleep(0.01)
    assert "fsync_landed" in events, "fsync 线程未进入执行"

    task.cancel()  # 取消一：打断外层 shield 进入落定等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消二：落定等待吸收后继续等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消三：仍被吸收，放行以 fsync 落定为界。
    await asyncio.sleep(0.05)
    assert not task.done(), "重复取消不提前放行，落定等待保持"
    assert not closed, "取消放行前不得关闭 fd"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(closed) == 1
    assert closed[0][1] == ["fsync_landed", "op_settled"]


async def test_finalize_atomic_replace_cancel_during_failing_replace_retrieves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """脱离的 replace 线程失败：异常被取回不产生未取回告警，fd 已由终局在 replace 前关闭。"""
    import gc

    closed = _install_close_counter(monkeypatch)
    replace_running, replace_release = _install_gated_path_replace(monkeypatch, fail=True)
    final = tmp_path / "out.bin"
    fd, temp_path = open_temp_fd(tmp_path, suffix=".part")
    with os.fdopen(fd, "wb", closefd=False) as f:
        f.write(b"payload")

    task = asyncio.ensure_future(finalize_atomic_replace(fd, temp_path, final, fsync=False))
    assert await asyncio.to_thread(replace_running.wait, 5.0), "替换线程未进入执行"
    task.cancel()
    replace_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    del task
    gc.collect()

    assert not final.exists()
    # fd 在 replace 提交前已由终局关闭，且全程恰一次。
    assert closed == [fd]
    assert "exception was never retrieved" not in caplog.text


# ==================== atomic_replace_from_fd_sync 同步原子落盘 ====================


def test_atomic_replace_from_fd_sync_success_replaces_and_leaves_no_temp(
    tmp_path: Path,
) -> None:
    """成功路径：同步 writer 写入后 os.replace 原子替换，目录内仅最终文件无残留。"""
    final = tmp_path / "out.bin"

    def writer(fd: int) -> None:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(b"payload")

    atomic_replace_from_fd_sync(final, writer, suffix=".part")

    assert final.read_bytes() == b"payload"
    assert list(tmp_path.iterdir()) == [final]


def test_atomic_replace_from_fd_sync_writer_failure_cleans_temp_and_closes_fd_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同步 writer 抛出：随机临时文件被清理、fd 由骨架唯一关闭一次、原始异常上抛。"""
    final = tmp_path / "out.bin"
    closed: list[int] = []
    real_close = os.close

    def _tracking_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "close", _tracking_close)

    def bad_writer(fd: int) -> None:
        raise OSError("write boom")

    with pytest.raises(OSError, match="write boom"):
        atomic_replace_from_fd_sync(final, bad_writer, suffix=".part")

    assert not final.exists()
    # 失败路径清理随机临时文件，目录内无残留
    assert list(tmp_path.iterdir()) == []
    # 骨架为 fd 唯一关闭点，writer 未接管时关闭一次避免泄漏
    assert len(closed) == 1


def test_atomic_replace_from_fd_sync_replace_failure_cleans_temp(
    tmp_path: Path,
) -> None:
    """替换失败：目标被同名目录占用时 os.replace 抛错，随机临时文件被清理。"""
    final = tmp_path / "out.bin"
    final.mkdir()

    def writer(fd: int) -> None:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(b"payload")

    with pytest.raises(OSError):
        atomic_replace_from_fd_sync(final, writer, suffix=".part")

    # 目录占用保留，临时文件经失败路径清理无残留
    assert final.is_dir()
    assert list(tmp_path.iterdir()) == [final]


def test_atomic_replace_from_fd_sync_fsync_enabled_calls_os_fsync_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fsync=True 时同步骨架在 writer 写入后、替换前对 fd 执行 os.fsync 恰好一次。"""
    final = tmp_path / "out.bin"
    fsync_calls = _install_fsync_counter(monkeypatch)

    def writer(fd: int) -> None:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(b"payload")

    atomic_replace_from_fd_sync(final, writer, suffix=".part", fsync=True)

    assert final.read_bytes() == b"payload"
    assert list(tmp_path.iterdir()) == [final]
    assert len(fsync_calls) == 1


def test_atomic_replace_from_fd_sync_fsync_default_off_skips_os_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认 fsync 关闭：同步落盘成功但不调用 os.fsync。"""
    final = tmp_path / "out.bin"
    fsync_calls = _install_fsync_counter(monkeypatch)

    def writer(fd: int) -> None:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(b"payload")

    atomic_replace_from_fd_sync(final, writer, suffix=".part")

    assert final.read_bytes() == b"payload"
    assert fsync_calls == []
