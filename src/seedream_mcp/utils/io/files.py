"""OS 级文件打开工具：O_NOFOLLOW 防符号链接与原子落盘骨架。

提供 open_no_follow_read、open_regular_read、open_packaged_read、
open_no_follow_write_fd、open_temp_fd、finalize_atomic_replace
与同步落盘入口 atomic_replace_from_fd_sync，另有 has_reparse_attribute
判定 NTFS junction 等非符号链接型 reparse point，
供 scan 的目录扫描与 storage 的清理遍历使用。共享函数抛 OSError，
由调用方按各自异常类型包装。

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
from collections.abc import Callable
from pathlib import Path
from typing import IO

from ..core.settle import await_settled_op
from .path import ResolveResultCache, resolve_rejecting_cycle

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


class NonRegularFileError(OSError):
    """打开成功但句柄不是常规文件的拒绝，errno 对齐 EINVAL。

    FIFO 等特殊文件在非阻塞防护下可被打开，阻塞读取会钉死工作线程，形态拒绝
    收敛在打开单点；子类化 OSError 使既有 except OSError 调用方不受影响。
    """


def _cleanup_temp_file(temp_path: Path) -> None:
    """清理临时文件，忽略不存在，清理失败记录警告以暴露残留。

    清理失败多为 Windows 杀软、索引器短暂持锁等瞬时原因，记录 warning 便于运维
    发现残留而非静默吞掉。
    """
    try:
        temp_path.unlink(missing_ok=True)
    except OSError as exc:
        from ..core.logs import get_logger

        get_logger().warning("清理临时文件失败: {} -> {}", temp_path, exc)


def _close_fd_quietly(fd: int) -> None:
    """关闭 fd，失败降 debug 日志不上抛：善后路径的清理不顶替原始错误。"""
    try:
        os.close(fd)
    except OSError as exc:
        from ..core.logs import get_logger

        get_logger().debug("关闭 fd 失败: {}", exc)


def _open_no_follow_fallback(
    path_str: str, flags: int, *, action: str
) -> tuple[int, os.stat_result]:
    """在无 O_NOFOLLOW 的平台兜底打开文件，返回文件描述符与其 fstat 结果。

    lstat 取最终分量指纹，符号链接直接拒绝；open 后用 fstat 复核 fd 仍是同一对象，
    期间被替换则 st_ino/st_dev 不一致，据此拒绝，闭合 TOCTOU 竞态。复核所用的
    fstat 结果随 fd 返回，调用方不再对同一 fd 二次 fstat。

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
    return fd, post_st


def _clear_nonblock_fd(fd: int) -> None:
    """清除 fd 的 O_NONBLOCK，交出前恢复常规文件的阻塞语义。"""
    if sys.platform == "win32":
        return
    nonblock = _NONBLOCKING_OPEN_FLAGS & getattr(os, "O_NONBLOCK", 0)
    if not nonblock:
        return
    import fcntl

    current = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, current & ~nonblock)


def _open_no_follow_fd(path: PathLike, flags: int, *, action: str) -> tuple[int, os.stat_result]:
    """以 no-follow 纪律打开常规文件，返回 fd 与同一 fd 的 fstat 结果。

    读写打开的共享骨架：支持 O_NOFOLLOW 的平台由内核在 open 时原子拒绝符号链接，
    不支持平台退化为 lstat/fstat 同一性比对兜底；
    POSIX 打开阶段携带非阻塞防护标志防 FIFO 等特殊文件路径阻塞 open，
    非常规文件形态在打开单点拒绝，非阻塞位的恢复由调用方执行。

    Args:
        path: 目标文件路径，最终路径分量不得为符号链接。
        flags: 除 O_NOFOLLOW 与非阻塞防护外的打开标志位。
        action: 操作描述，用于错误消息。

    Raises:
        SymlinkRejectedError: 最终路径分量为符号链接或打开期间被换链。
        NonRegularFileError: 打开成功但 fd 不是常规文件。
        OSError: 其他打开或 fstat 失败；失败时 fd 先关闭再抛，不泄漏。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    open_flags = flags | no_follow | _NONBLOCKING_OPEN_FLAGS
    if no_follow:
        try:
            fd = os.open(str(path), open_flags)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise SymlinkRejectedError(errno.ELOOP, f"拒绝{action}符号链接", str(path)) from exc
            raise
        try:
            stat_result = os.fstat(fd)
        except OSError:
            os.close(fd)
            raise
    else:
        fd, stat_result = _open_no_follow_fallback(str(path), open_flags, action=action)
    if not stat.S_ISREG(stat_result.st_mode):
        os.close(fd)
        raise NonRegularFileError(errno.EINVAL, f"拒绝{action}非常规文件", str(path))
    return fd, stat_result


def _open_no_follow_read_with_stat(path: PathLike) -> tuple[IO[bytes], os.stat_result]:
    """以 O_RDONLY | O_NOFOLLOW 打开常规文件，返回二进制只读句柄与其 fstat 结果。

    打开与拒绝序列统一收在 _open_no_follow_fd，交出句柄前清除非阻塞位，
    读取不进入阻塞；返回的 stat 结果取自同一 fd 的单次 fstat。

    Args:
        path: 目标文件路径，最终路径分量不得为符号链接。

    Raises:
        SymlinkRejectedError: 最终路径分量为符号链接或打开期间被换链。
        NonRegularFileError: 打开成功但句柄不是常规文件。
        OSError: 其他打开或 fstat 失败；失败时 fd 先关闭再抛，不泄漏。
    """
    fd, stat_result = _open_no_follow_fd(path, os.O_RDONLY, action="读取")
    handle = os.fdopen(fd, "rb")
    _clear_nonblock_fd(fd)
    return handle, stat_result


def open_no_follow_read(path: PathLike) -> IO[bytes]:
    """以 O_RDONLY | O_NOFOLLOW 打开常规文件，返回二进制只读文件对象。

    符号链接、非常规文件形态与 FIFO 阻塞防护在打开单点拒绝，打开序列与
    拒绝语义统一收在 _open_no_follow_read_with_stat。

    Args:
        path: 目标文件路径，最终路径分量不得为符号链接。

    Raises:
        SymlinkRejectedError: 最终路径分量为符号链接或打开期间被换链。
        NonRegularFileError: 打开成功但句柄不是常规文件。
        OSError: 其他打开失败。
    """
    handle, _stat_result = _open_no_follow_read_with_stat(path)
    return handle


def open_regular_read(path: PathLike) -> tuple[IO[bytes], os.stat_result] | None:
    """以 no-follow 语义打开常规文件，返回只读句柄与其打开时刻的 fstat 结果。

    webapp 页面与原图直出的共享打开序列：stat 结果取自打开序列内的单次
    fstat，响应头与读出内容恒描述同一打开的 inode，消除 stat 与发送期按
    路径重开之间文件被替换的竞态窗口。

    Args:
        path: 目标文件路径，最终路径分量不得为符号链接。

    Returns:
        ``(handle, stat_result)`` 二元组；打开成功但非常规文件时为 None，
        fd 已关闭；打开阶段目录形态异常（Windows PermissionError、POSIX
        IsADirectoryError）经路径 stat 判定为目录或非常规文件时同样归 None。

    Raises:
        SymlinkRejectedError: 最终路径分量为符号链接或打开期间被换链。
        OSError: 其他打开或 fstat 失败；fstat 失败先关 fd 再抛。常规文件的
            PermissionError 原样传播，保留权限类失败的诊断信号。
    """
    try:
        return _open_no_follow_read_with_stat(path)
    except NonRegularFileError:
        # POSIX 打开目录与特殊文件成功，形态拒绝已在打开单点给出。
        return None
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


# 打包资源解析目标的进程级有界缓存：打包树不可变不设 TTL，重装替换经复位协议清缓存。
_PACKAGED_RESOLVE_CACHE_MAX = 128
_packaged_resolve_cache = ResolveResultCache[Path](_PACKAGED_RESOLVE_CACHE_MAX)


def reset_packaged_resolve_cache() -> None:
    """清空打包资源解析目标的进程级缓存，供测试隔离与进程复位使用。

    runtime._reset_lifespan_state 的复位协议经本函数登记此缓存；模块内其余
    名称均为不可变常量或纯函数，无其他需要复位的可变状态。
    """
    _packaged_resolve_cache.clear()


def open_packaged_read(path: PathLike) -> tuple[IO[bytes], os.stat_result] | None:
    """打开随包分发的静态资源，符号链接布局解析到真实目标后按同一序列打开。

    pip/nix 逐文件符号链接到共享存储的安装布局由此正常读取：入口先查已解析
    目标缓存，命中直接按真实目标单次打开，未命中先按原路径打开、符号链接拒绝
    后解析真实目标落缓存重开，符号链接布局仅首次付拒绝重开与 realpath；包内
    路径非用户可控，跟随解析无越权面，链接环经 resolve_rejecting_cycle 拒绝，
    FIFO 与非常规文件防护经 open_regular_read 不退化。
    """
    cache_key = os.fspath(path)
    if cache_key in _packaged_resolve_cache:
        return open_regular_read(_packaged_resolve_cache[cache_key])
    try:
        return open_regular_read(path)
    except SymlinkRejectedError:
        # 拒绝即符号链接布局或打开期换链，解析真实目标落缓存后按同一序列重开。
        target = resolve_rejecting_cycle(Path(cache_key))
        _packaged_resolve_cache[cache_key] = target
        return open_regular_read(target)


def open_no_follow_write_fd(path: PathLike) -> int:
    """以 O_WRONLY | O_NOFOLLOW 打开常规文件返回原始 fd，供落盘终局按路径重开。

    与读取路径同一防换链与 FIFO 防护纪律，打开与拒绝序列统一收在
    _open_no_follow_fd，交出前恢复阻塞语义。

    Raises:
        SymlinkRejectedError: 最终路径分量为符号链接或打开期间被换链。
        NonRegularFileError: 打开成功但 fd 不是常规文件。
        OSError: 其他打开或 fstat 失败。
    """
    fd, _stat_result = _open_no_follow_fd(
        path, os.O_WRONLY | getattr(os, "O_BINARY", 0), action="写入"
    )
    _clear_nonblock_fd(fd)
    return fd


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


async def finalize_atomic_replace(
    fd: int,
    temp_path: Path,
    target: Path,
    *,
    fsync: bool,
) -> None:
    """落盘终局单点：可选 fsync 后静默关闭 fd，再经线程池 ``os.replace`` 原子替换。

    供 download 续传终局使用。fd 在任一退出路径均被关闭，关闭失败仅降
    debug 日志：数据已完整写入临时文件，替换不因善后关闭异常而丢弃；fsync 或
    replace 失败的异常原样上抛，临时文件清理与替换已成后的处置归调用方。取消
    到达时先等待脱离等待层的 fsync/replace 线程落定再放行取消，fd 关闭与调用
    方清理不与在途线程操作竞争。

    Raises:
        OSError: fsync 或原子替换失败。
    """
    try:
        if fsync:
            # fd 关闭前的落定守护点：吸收重复取消，fsync 为有界系统调用满足无条件档前置。
            await await_settled_op(asyncio.to_thread(os.fsync, fd), absorb_all_cancels=True)
    finally:
        _close_fd_quietly(fd)
    await await_settled_op(asyncio.to_thread(temp_path.replace, target))


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
) -> None:
    """经随机名临时文件同步原子落盘。

    供 save_bytes 等同步公共接口复用，避免在事件循环内 asyncio.run 驱动异步骨架；
    调用方须处于可阻塞的同步上下文。writer 同步写入 fd 后由本函数 os.replace 原子替换，
    失败清理临时文件；writer 须以 closefd=False 包装 fd 使本函数独占关闭权，
    抛出的异常原样上抛由调用方分类。

    Args:
        final_path: 最终目标路径，临时文件在其所在目录创建以保证同文件系统原子替换。
        writer: 接收 fd 的同步写入回调，须以 closefd=False 包装 fd。
        suffix: 临时文件名后缀，用于可读性与调试定位。
        fsync: 替换前是否对 fd 执行 os.fsync。

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
