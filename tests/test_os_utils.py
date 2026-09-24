"""io_file 守护测试：open_no_follow_read 的符号链接拒绝、open_regular_read 的常规
文件打开序列与 atomic_replace 原子落盘骨架。

open_no_follow_read 覆盖平台 O_NOFOLLOW、monkeypatch 模拟不支持时的 is_symlink 兜底
与正常读取三种场景。Windows 创建符号链接需特权或开发者模式，相关用例以探测结果 skip。
"""

import asyncio
import os
import stat
import sys
from pathlib import Path

import pytest

from seedream_mcp.utils.io.io_file import (
    SymlinkRejectedError,
    atomic_replace_from_fd,
    atomic_replace_from_fd_sync,
    open_no_follow_read,
    open_regular_read,
)

from _os_fakes import _install_fsync_counter


def _can_create_symlink(tmp_path: Path) -> bool:
    """探测当前进程是否有权创建符号链接，Windows 需特权或开发者模式。"""
    target = tmp_path / "_symlink_probe_target.bin"
    target.write_bytes(b"x")
    link = tmp_path / "_symlink_probe_link.bin"
    try:
        os.symlink(str(target), str(link))
        return True
    except (OSError, AttributeError):
        return False
    finally:
        try:
            if link.exists() or link.is_symlink():
                link.unlink()
        except OSError:
            pass


def _make_symlink(tmp_path: Path, name: str) -> Path:
    """创建指向真实文件的符号链接，返回链接路径；无法创建时 pytest.skip。"""
    if not _can_create_symlink(tmp_path):
        pytest.skip("当前进程无法创建符号链接（Windows 可能需要开发者模式或管理员）")
    target = tmp_path / f"{name}_target.bin"
    target.write_bytes(b"secret")
    link = tmp_path / f"{name}_link.bin"
    os.symlink(str(target), str(link))
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


def test_open_regular_read_non_regular_file_returns_none_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非常规文件（字符设备等）：返回 None 且句柄已关闭，交调用方按缺失口径路由。"""
    import seedream_mcp.utils.io.io_file as io_file_module

    device = os.fdopen(os.open(os.devnull, os.O_RDONLY), "rb")
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: device)

    assert open_regular_read(tmp_path / "any.bin") is None
    assert device.closed


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

    import seedream_mcp.utils.io.io_file as io_file_module

    path = tmp_path / "plain.bin"
    path.write_bytes(b"data")

    def _denied(target: object, **_kwargs: object) -> IO[bytes]:
        raise PermissionError(errno.EACCES, "simulated EACCES", str(target))

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _denied)

    with pytest.raises(PermissionError):
        open_regular_read(path)


def test_open_regular_read_permission_error_stat_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分类用 stat 失败时上抛的仍是原 PermissionError，不泄漏 stat 自身的 OSError。"""
    import errno
    from typing import IO

    import seedream_mcp.utils.io.io_file as io_file_module

    def _denied(target: object, **_kwargs: object) -> IO[bytes]:
        raise PermissionError(errno.EACCES, "simulated EACCES", str(target))

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _denied)

    with pytest.raises(PermissionError):
        open_regular_read(tmp_path / "missing.bin")


def test_open_regular_read_fstat_failure_closes_handle_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fstat 抛错：句柄先关闭再原样上抛 OSError，不依赖 GC 释放 fd。"""
    import seedream_mcp.utils.io.io_file as io_file_module

    path = tmp_path / "plain.bin"
    path.write_bytes(b"data")
    handle = open(path, "rb")
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: handle)

    def _failing_fstat(fd: int) -> os.stat_result:
        del fd
        raise OSError("simulated ESTALE")

    monkeypatch.setattr(os, "fstat", _failing_fstat)

    with pytest.raises(OSError, match="simulated ESTALE"):
        open_regular_read(path)
    assert handle.closed


def test_open_regular_read_symlink_rejection_propagates(tmp_path: Path) -> None:
    """符号链接拒绝原样上抛，由调用方按各自口径路由。"""
    link = _make_symlink(tmp_path, "regular_read")

    with pytest.raises(SymlinkRejectedError):
        open_regular_read(link)


def test_open_regular_read_missing_file_propagates_oserror(tmp_path: Path) -> None:
    """缺失路径的 OSError 原样上抛，不吞不换。"""
    with pytest.raises(FileNotFoundError):
        open_regular_read(tmp_path / "missing.bin")


def test_open_regular_read_open_flags_carry_nonblocking_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX 打开阶段携带 O_NONBLOCK|O_NOCTTY 防特殊文件路径阻塞 open 钉死工作线程，验明常规文件后清除非阻塞位。"""
    from typing import Any

    path = tmp_path / "page.html"
    path.write_bytes(b"<html></html>")
    captured: list[int] = []
    real_open = os.open

    def _capturing_open(name: str, flags: int, *args: Any, **kwargs: Any) -> int:
        captured.append(flags)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", _capturing_open)
    opened = open_regular_read(path)

    assert opened is not None
    handle, _stat_result = opened
    try:
        if sys.platform != "win32":
            assert captured == [os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY]
            import fcntl

            served_flags = fcntl.fcntl(handle.fileno(), fcntl.F_GETFL)
            assert not served_flags & os.O_NONBLOCK
        else:
            # Windows 无两类标志，兜底路径按原 O_RDONLY 形态打开。
            assert captured == [os.O_RDONLY]
        assert handle.read() == b"<html></html>"
    finally:
        handle.close()


@pytest.mark.skipif(sys.platform == "win32", reason="os.mkfifo 仅 POSIX 提供")
def test_open_regular_read_fifo_returns_none_without_blocking(tmp_path: Path) -> None:
    """FIFO 路径经非阻塞打开进 fstat 判定后归 None，不无限阻塞工作线程。"""
    if sys.platform == "win32":
        pytest.skip("os.mkfifo 仅 POSIX 提供")
    fifo = tmp_path / "fifo-page.html"
    os.mkfifo(fifo)

    assert open_regular_read(fifo) is None


# ==================== atomic_replace_from_fd 原子落盘骨架 ====================


async def test_atomic_replace_from_fd_success_replaces_and_leaves_no_temp(
    tmp_path: Path,
) -> None:
    """成功路径：writer 写入后 os.replace 原子替换，目录内仅最终文件无随机临时残留。"""
    final = tmp_path / "out.bin"

    async def writer(fd: int) -> None:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(b"payload")

    await atomic_replace_from_fd(final, writer, suffix=".part")

    assert final.read_bytes() == b"payload"
    assert list(tmp_path.iterdir()) == [final]


async def test_atomic_replace_from_fd_writer_failure_cleans_temp_and_closes_fd_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """writer 抛出：随机临时文件被清理、fd 由骨架唯一关闭一次、原始异常上抛。"""
    final = tmp_path / "out.bin"
    closed: list[int] = []
    real_close = os.close

    def _tracking_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "close", _tracking_close)

    async def bad_writer(fd: int) -> None:
        raise OSError("write boom")

    with pytest.raises(OSError, match="write boom"):
        await atomic_replace_from_fd(final, bad_writer, suffix=".part")

    assert not final.exists()
    # 失败路径清理随机临时文件，目录内无残留
    assert list(tmp_path.iterdir()) == []
    # 骨架为 fd 唯一关闭点，writer 未接管时关闭一次避免泄漏
    assert len(closed) == 1


async def test_atomic_replace_from_fd_replace_failure_cleans_temp(tmp_path: Path) -> None:
    """异步骨架替换失败：目标被同名目录占用时异常上抛，随机临时文件被清理。

    与同步版 atomic_replace_from_fd_sync 的用例互为镜像。
    """
    final = tmp_path / "out.bin"
    final.mkdir()

    async def writer(fd: int) -> None:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(b"payload")

    with pytest.raises(OSError):
        await atomic_replace_from_fd(final, writer, suffix=".part")

    # 目录占用保留，临时文件经失败路径清理无残留
    assert final.is_dir()
    assert list(tmp_path.iterdir()) == [final]


async def test_atomic_replace_from_fd_failure_cleanup_runs_off_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败路径的临时文件清理经线程池执行，unlink 不阻塞事件循环。"""
    import threading

    import seedream_mcp.utils.io.io_file as io_file_module

    main_thread = threading.get_ident()
    cleanup_thread_ids: list[int] = []
    real_cleanup = io_file_module._cleanup_temp_file

    def _tracking_cleanup(temp_path: Path) -> None:
        cleanup_thread_ids.append(threading.get_ident())
        real_cleanup(temp_path)

    monkeypatch.setattr(io_file_module, "_cleanup_temp_file", _tracking_cleanup)

    final = tmp_path / "out.bin"

    async def bad_writer(fd: int) -> None:
        raise OSError("write boom")

    with pytest.raises(OSError, match="write boom"):
        await atomic_replace_from_fd(final, bad_writer, suffix=".part")

    assert list(tmp_path.iterdir()) == []
    assert cleanup_thread_ids, "失败清理须被执行"
    assert all(tid != main_thread for tid in cleanup_thread_ids), "清理 unlink 不得留在事件循环线程"


async def test_atomic_replace_from_fd_cleanup_survives_second_level_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """二级取消不打断 finally 清理：排队中的 unlink 仍执行，.part 无残留。

    writer 被取消进入失败清理后再收到外层取消，模拟下载任务取消后外层再取消；
    清理 await 以挂起的 to_thread 替身建模已提交未开始（线程池排队）的窗口，
    无 shield 时该窗口内的取消会丢弃清理工作。
    """
    import threading
    from typing import Any

    import seedream_mcp.utils.io.io_file as io_file_module

    real_to_thread = asyncio.to_thread
    real_cleanup = io_file_module._cleanup_temp_file
    cleanup_pending = asyncio.Event()
    cleanup_gate = asyncio.Event()
    cleanup_done = threading.Event()

    def gated_cleanup(temp_path: Path) -> None:
        real_cleanup(temp_path)
        cleanup_done.set()

    async def gated_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        if func is not gated_cleanup:
            return await real_to_thread(func, *args, **kwargs)
        cleanup_pending.set()
        await cleanup_gate.wait()
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(io_file_module, "_cleanup_temp_file", gated_cleanup)
    monkeypatch.setattr(asyncio, "to_thread", gated_to_thread)

    final = tmp_path / "out.bin"
    started = asyncio.Event()

    async def hanging_writer(fd: int) -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.ensure_future(atomic_replace_from_fd(final, hanging_writer, suffix=".part"))
    await started.wait()
    task.cancel()
    await cleanup_pending.wait()
    # 清理 await 已提交未开始时注入二级取消，shield 使其只命中外层等待
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    cleanup_gate.set()
    assert await real_to_thread(cleanup_done.wait, 5.0), "二级取消后清理仍须执行"
    assert list(tmp_path.iterdir()) == []


async def test_atomic_replace_from_fd_fsync_enabled_calls_os_fsync_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fsync=True 时异步骨架在 writer 写入后、替换前对 fd 执行 os.fsync 恰好一次。"""
    final = tmp_path / "out.bin"
    fsync_calls = _install_fsync_counter(monkeypatch)

    async def writer(fd: int) -> None:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(b"payload")

    await atomic_replace_from_fd(final, writer, suffix=".part", fsync=True)

    assert final.read_bytes() == b"payload"
    assert list(tmp_path.iterdir()) == [final]
    assert len(fsync_calls) == 1


async def test_atomic_replace_from_fd_fsync_default_off_skips_os_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认 fsync 关闭：落盘成功但不调用 os.fsync。"""
    final = tmp_path / "out.bin"
    fsync_calls = _install_fsync_counter(monkeypatch)

    async def writer(fd: int) -> None:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(b"payload")

    await atomic_replace_from_fd(final, writer, suffix=".part")

    assert final.read_bytes() == b"payload"
    assert fsync_calls == []


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
