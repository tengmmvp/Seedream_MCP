"""OS 级文件打开工具：O_NOFOLLOW 防符号链接与原子落盘骨架。

提供 open_no_follow_read、open_regular_read、open_temp_fd、atomic_replace_from_fd
与同步变体 atomic_replace_from_fd_sync，另有 has_reparse_attribute 判定 NTFS
junction 等非符号链接型 reparse point，供 io_path 的浏览扫描与 io_storage 的
清理遍历使用。共享函数抛 OSError，由调用方按各自异常类型包装。

残余风险：O_NOFOLLOW 仅保护最终路径分量，不阻止内核 open 跟随中间目录的符号链接；
父目录在校验与打开之间被替换为指向工作区外的符号链接时读取会逃逸出工作区，该攻击
需本地写权限与精确时序，属下层威胁。lstat/fstat 同一性回退依赖 st_ino/st_dev 充当
稳定文件指纹，FAT/FAT32/ReFS 卷的 file index 不保证稳定，替换竞态在该类卷上可能漏判。
"""

from __future__ import annotations

import asyncio
import errno
import os
import stat
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import IO

PathLike = str | Path

# POSIX 打开阶段携带非阻塞与无控制终端标志：FIFO 或终端路径的阻塞 open 会钉死工作线程。
_NONBLOCKING_OPEN_FLAGS = (
    getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0) if sys.platform != "win32" else 0
)


class SymlinkRejectedError(OSError):
    """最终分量为符号链接或打开期间被换链的拒绝，errno 对齐 ELOOP。

    子类化 OSError 使既有 except OSError 调用方不受影响，需要区分拒绝语义的
    调用方按本类型归档。
    """


def _cleanup_temp_file(temp_path: Path) -> None:
    """清理临时文件，忽略不存在，清理失败记录警告以暴露残留。

    清理失败多为 Windows 杀毒、索引器短暂持锁等瞬时原因，记录 warning 便于运维
    发现残留而非静默吞掉。
    """
    try:
        temp_path.unlink(missing_ok=True)
    except OSError as exc:
        from ..core.logs import get_logger

        get_logger().warning("清理临时文件失败: {} -> {}", temp_path, exc)


def _open_no_follow_fallback(path_str: str, flags: int, *, action: str) -> int:
    """在无 O_NOFOLLOW 的平台兜底打开文件，返回文件描述符。

    lstat 取最终分量指纹，符号链接直接拒绝；open 后用 fstat 复核 fd 仍是同一对象，
    期间被替换则 st_ino/st_dev 不一致，据此拒绝，闭合 TOCTOU 竞态。

    Args:
        path_str: 目标路径字符串。
        flags: 传给 os.open 的标志位，不含 O_NOFOLLOW。
        action: 操作描述，用于错误消息。

    Raises:
        SymlinkRejectedError: 最终分量为符号链接，或校验与打开之间最终分量被替换。
        OSError: 其他打开或 stat 失败。
    """
    pre_st = os.lstat(path_str)
    if stat.S_ISLNK(pre_st.st_mode):
        raise SymlinkRejectedError(errno.ELOOP, f"拒绝{action}符号链接", path_str)
    fd = os.open(path_str, flags)
    try:
        post_st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if post_st.st_ino != pre_st.st_ino or post_st.st_dev != pre_st.st_dev:
        os.close(fd)
        raise SymlinkRejectedError(errno.ELOOP, f"打开期间最终分量被替换，拒绝{action}", path_str)
    return fd


def open_no_follow_read(path: PathLike, *, extra_open_flags: int = 0) -> IO[bytes]:
    """以 O_RDONLY | O_NOFOLLOW 打开文件，返回二进制只读文件对象。

    最终路径分量若为符号链接则拒绝：支持 O_NOFOLLOW 的平台由内核在 open 时原子
    拒绝，不支持平台退化为 lstat/fstat 同一性比对兜底。

    Args:
        path: 目标文件路径，最终路径分量不得为符号链接。
        extra_open_flags: 追加到 os.open 的标志位，默认 0 不改变行为。

    Raises:
        SymlinkRejectedError: 最终路径分量为符号链接或打开期间被换链。
        OSError: 其他打开失败。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow:
        try:
            fd = os.open(str(path), os.O_RDONLY | no_follow | extra_open_flags)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise SymlinkRejectedError(errno.ELOOP, "拒绝读取符号链接", str(path)) from exc
            raise
        return os.fdopen(fd, "rb")
    fd = _open_no_follow_fallback(str(path), os.O_RDONLY | extra_open_flags, action="读取")
    return os.fdopen(fd, "rb")


def _clear_nonblock_flag(handle: IO[bytes]) -> None:
    """清除句柄的 O_NONBLOCK，交出前恢复常规文件的阻塞读语义。"""
    if sys.platform == "win32":
        return
    nonblock = _NONBLOCKING_OPEN_FLAGS & getattr(os, "O_NONBLOCK", 0)
    if not nonblock:
        return
    import fcntl

    fd = handle.fileno()
    current = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, current & ~nonblock)


def open_regular_read(path: PathLike) -> tuple[IO[bytes], os.stat_result] | None:
    """以 no-follow 语义打开常规文件，返回只读句柄与其 fstat 结果。

    webapp 页面与原图直出的共享打开序列：打开后立即取句柄 fstat，响应头与
    读出内容恒描述同一打开的 inode，消除 stat 与发送期按路径重开之间文件
    被替换的竞态窗口。POSIX 打开阶段携带非阻塞防护标志防特殊文件路径阻塞
    打开，fstat 验明常规文件后清除 O_NONBLOCK 再交出句柄。

    Args:
        path: 目标文件路径，最终路径分量不得为符号链接。

    Returns:
        ``(handle, stat_result)`` 二元组；打开成功但非常规文件时为 None，
        句柄已关闭；打开阶段目录形态异常（Windows PermissionError、POSIX
        IsADirectoryError）经路径 stat 判定为目录或非常规文件时同样归 None。

    Raises:
        SymlinkRejectedError: 最终路径分量为符号链接或打开期间被换链。
        OSError: 其他打开或 fstat 失败；fstat 失败先关句柄再抛。常规文件的
            PermissionError 原样传播，保留权限类失败的诊断信号。
    """
    try:
        handle = open_no_follow_read(path, extra_open_flags=_NONBLOCKING_OPEN_FLAGS)
    except (PermissionError, IsADirectoryError):
        # Windows 对目录的 open 报 EACCES、POSIX 报 EISDIR；路径 stat 仅作形态
        # 分类，stat 失败或仍为常规文件时原异常传播。
        try:
            mode: int | None = os.stat(path).st_mode
        except OSError:
            mode = None
        if mode is not None and not stat.S_ISREG(mode):
            return None
        raise
    try:
        stat_result = os.fstat(handle.fileno())
    except OSError:
        handle.close()
        raise
    if not stat.S_ISREG(stat_result.st_mode):
        handle.close()
        return None
    _clear_nonblock_flag(handle)
    return handle, stat_result


def open_temp_fd(dir_path: PathLike, *, suffix: str = ".part") -> tuple[int, Path]:
    """在指定目录内创建不可预测随机名的临时文件，返回文件描述符与实际路径。

    基于 ``tempfile.mkstemp`` 以独占创建方式打开，规避可预测临时路径被预置符号链接
    覆盖任意文件的风险。``dir_path`` 须已存在且与目标路径同文件系统，以保证
    ``os.replace`` 的原子性；调用方写入完成后负责替换与失败清理。

    Args:
        dir_path: 临时文件所在目录，须已存在且与目标路径同文件系统。
        suffix: 临时文件名后缀，默认 ``.part``。

    Returns:
        ``(fd, temp_path)`` 二元组，``fd`` 已以读写独占方式打开。
    """
    fd, name = tempfile.mkstemp(dir=str(dir_path), suffix=suffix)
    return fd, Path(name)


async def atomic_replace_from_fd(
    final_path: Path,
    writer: Callable[[int], Awaitable[Path | None]],
    suffix: str = ".part",
    fsync: bool = False,
) -> Path:
    """经随机名临时文件原子落盘，返回实际创建的随机临时路径。

    统一 io_storage 与 io_download 的落盘协议：``open_temp_fd`` 在 ``final_path``
    同目录创建随机名临时文件规避符号链接 TOCTOU，``writer`` 接收 fd 异步写入，完成
    后经线程池执行 ``os.replace`` 原子替换，失败路径清理临时文件；mkstemp、
    replace 与失败清理经 ``asyncio.to_thread`` 卸载，避免阻塞事件循环。writer 须以
    ``closefd=False`` 包装 fd 使本函数独占关闭权，避免双重关闭与 fd 复用误关他者，
    抛出的异常原样上抛由调用方分类。writer 返回 None 时替换到 ``final_path``；
    返回 Path 时以该路径为最终目标，供写入后才能确定路径的场景使用，须与
    ``final_path`` 同目录以保证原子性。

    Args:
        final_path: 最终目标路径，临时文件在其所在目录创建以保证同文件系统原子替换。
        writer: 接收 fd 的异步写入回调，可返回覆盖用的最终路径或 None。
        suffix: 临时文件名后缀，用于可读性与调试定位。
        fsync: 替换前是否对 fd 执行 os.fsync。默认关闭；开启后大幅缩小但未消除
            rename 的持久化窗口，POSIX 上未 fsync 父目录时崩溃可能丢失 rename 本身。

    Returns:
        实际创建的随机名临时路径（替换成功后已重命名为最终路径）。

    Raises:
        OSError: 临时文件创建或原子替换失败；writer 抛出的异常原样上抛。
    """
    fd, temp_path = await asyncio.to_thread(open_temp_fd, final_path.parent, suffix=suffix)
    replaced = False
    try:
        try:
            override_path = await writer(fd)
            if fsync:
                await asyncio.to_thread(os.fsync, fd)
        finally:
            os.close(fd)
        target = final_path if override_path is None else override_path
        await asyncio.to_thread(temp_path.replace, target)
        replaced = True
    finally:
        if not replaced:
            # shield：二级取消只打断外层等待，排队中的清理仍执行，避免 .part 残留
            await asyncio.shield(asyncio.to_thread(_cleanup_temp_file, temp_path))
    return temp_path


_FILE_ATTRIBUTE_REPARSE_POINT = stat.FILE_ATTRIBUTE_REPARSE_POINT


def has_reparse_attribute(st: os.stat_result) -> bool:
    """判断 no-follow stat 结果是否携带 NTFS reparse point 属性位，非 Windows 恒为 False。

    调用方已持有 stat 结果时经本函数判定，不再单独 lstat；st_file_attributes 仅
    Windows 的 stat 结果存在，平台判定先行。
    """
    if sys.platform != "win32":
        return False
    return bool(st.st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def atomic_replace_from_fd_sync(
    final_path: Path,
    writer: Callable[[int], None],
    suffix: str = ".part",
    fsync: bool = False,
) -> Path:
    """经随机名临时文件同步原子落盘，返回实际创建的随机临时路径。

    atomic_replace_from_fd 的同步版本，供 save_bytes 等同步公共接口复用，避免在
    事件循环内 asyncio.run 驱动异步骨架；调用方须处于可阻塞的同步上下文。writer
    同步写入 fd 后由本函数 os.replace 原子替换，失败清理临时文件；writer 须以
    closefd=False 包装 fd 使本函数独占关闭权，抛出的异常原样上抛由调用方分类。

    Args:
        final_path: 最终目标路径，临时文件在其所在目录创建以保证同文件系统原子替换。
        writer: 接收 fd 的同步写入回调，须以 closefd=False 包装 fd。
        suffix: 临时文件名后缀，用于可读性与调试定位。
        fsync: 替换前是否对 fd 执行 os.fsync，语义与 atomic_replace_from_fd 一致。

    Returns:
        实际创建的随机名临时路径（替换成功后已重命名为 final_path）。

    Raises:
        OSError: 临时文件创建或原子替换失败；writer 抛出的异常原样上抛。
    """
    fd, temp_path = open_temp_fd(final_path.parent, suffix=suffix)
    replaced = False
    try:
        try:
            writer(fd)
            if fsync:
                os.fsync(fd)
        finally:
            os.close(fd)
        temp_path.replace(final_path)
        replaced = True
    finally:
        if not replaced:
            _cleanup_temp_file(temp_path)
    return temp_path
