"""OS 级文件打开工具：O_NOFOLLOW 防符号链接与原子落盘骨架。

提供 open_no_follow_read、open_temp_fd、atomic_replace_from_fd（异步）与
atomic_replace_from_fd_sync（同步变体）四个能力，另有 _is_reparse_point 判定
NTFS junction 等非符号链接型 reparse point，供 io_path 的浏览扫描与 io_storage 的
清理遍历共用；已持有 lstat 结果的调用方经 _has_reparse_attribute 判定同一属性位。
open_no_follow_read 读取时拒绝最终路径分量为符号链接；open_temp_fd
以不可预测随机名创建临时文件供调用方写入后原子替换；atomic_replace_from_fd 封装
“随机临时文件→写入→os.replace 原子替换→失败清理”协议供 io_storage 与 io_download
复用，writer 可返回 Path 覆盖最终路径以支持按字节签名修正扩展名等写入后才知的
目标。支持 O_NOFOLLOW 的平台由内核在 open 时原子拒绝符号链接。Windows 等平台
不支持 O_NOFOLLOW 时，退化为打开前 lstat 与打开后 fstat 比对 st_ino/st_dev
同一性：lstat 先取最终分量指纹，open 后用 fstat 复核打开的 fd 仍是同一对象，
若校验与打开之间最终分量被替换为符号链接则 st_ino/st_dev 不一致，据此拒绝，
闭合该 TOCTOU 竞态。共享函数抛 OSError，由调用方按各自异常类型包装。

残余风险：O_NOFOLLOW 仅保护最终路径分量，不阻止内核 open 跟随中间目录的符号链接；
若校验与打开之间某父目录被替换为指向工作区外的符号链接，读取会逃逸出工作区。上述
攻击需本地写权限与精确时序，属下层威胁。
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import IO, Awaitable, Callable

PathLike = str | Path


def _cleanup_temp_file(temp_path: Path) -> None:
    """清理临时文件，忽略不存在，清理失败记录警告以暴露残留临时文件。

    临时文件清理失败多为 Windows 杀毒/索引器短暂持锁等瞬时原因，记录 warning 便于运维
    发现残留而非静默吞掉。
    """
    try:
        temp_path.unlink(missing_ok=True)
    except OSError as exc:
        from ..core.logs import get_logger

        get_logger(__name__).warning("清理临时文件失败: {} -> {}", temp_path, exc)


def _open_no_follow_fallback(path_str: str, flags: int, *, action: str) -> int:
    """在无 O_NOFOLLOW 的平台兜底打开文件，返回文件描述符。

    lstat 先取最终分量指纹，符号链接直接拒绝；open 后用 fstat 复核打开的 fd 仍是
    同一对象，校验与打开之间最终分量被替换为符号链接则 st_ino/st_dev 不一致，据此
    拒绝，闭合该 TOCTOU 竞态。

    Args:
        path_str: 目标路径字符串。
        flags: 传给 os.open 的标志位，不含 O_NOFOLLOW。
        action: 操作描述，用于错误消息。

    Returns:
        打开的文件描述符。

    Raises:
        OSError: 最终分量为符号链接，或校验与打开之间最终分量被替换为符号链接。
    """
    pre_st = os.lstat(path_str)
    if stat.S_ISLNK(pre_st.st_mode):
        raise OSError(f"拒绝{action}符号链接: {path_str}")
    fd = os.open(path_str, flags)
    try:
        post_st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if post_st.st_ino != pre_st.st_ino or post_st.st_dev != pre_st.st_dev:
        os.close(fd)
        raise OSError(f"打开期间最终分量被替换，拒绝{action}: {path_str}")
    return fd


def open_no_follow_read(path: PathLike) -> IO[bytes]:
    """以 O_RDONLY | O_NOFOLLOW 打开文件，返回二进制只读文件对象。

    最终路径分量若为符号链接则拒绝；平台不支持 O_NOFOLLOW 时退化为 lstat 取指纹、
    open 后 fstat 比对 st_ino/st_dev 同一性，闭合最终分量替换竞态。

    Args:
        path: 目标文件路径。

    Returns:
        以只读方式打开的二进制文件对象。

    Raises:
        OSError: 最终路径分量为符号链接、打开期间最终分量被替换，或其他打开失败。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow:
        return os.fdopen(os.open(str(path), os.O_RDONLY | no_follow), "rb")
    fd = _open_no_follow_fallback(str(path), os.O_RDONLY, action="读取")
    return os.fdopen(fd, "rb")


def open_temp_fd(dir_path: PathLike, *, suffix: str = ".part") -> tuple[int, Path]:
    """在指定目录内创建不可预测随机名的临时文件，返回文件描述符与实际路径。

    基于 ``tempfile.mkstemp`` 生成不可预测文件名并以独占创建方式打开，规避可预测
    临时路径被预置符号链接覆盖任意文件的风险。调用方写入完成后用 ``os.replace``
    原子替换到目标路径，失败时负责清理该临时文件。``dir_path`` 必须已存在且与目标
    路径位于同一文件系统，以保证 ``os.replace`` 的原子性。

    Args:
        dir_path: 临时文件所在目录，必须已存在。
        suffix: 临时文件名后缀，用于可读性与调试定位。

    Returns:
        ``(fd, temp_path)``：``fd`` 已以只写独占方式打开，``temp_path`` 为实际创建的
        随机名路径。

    Raises:
        OSError: 目录不存在或临时文件创建失败。
    """
    fd, name = tempfile.mkstemp(dir=str(dir_path), suffix=suffix)
    return fd, Path(name)


async def atomic_replace_from_fd(
    final_path: Path,
    writer: Callable[[int], Awaitable[Path | None]],
    suffix: str = ".part",
) -> Path:
    """经随机名临时文件原子落盘，返回实际创建的随机临时路径。

    统一 io_storage.save_bytes 与 io_download._download_response_to_temp 的落盘
    协议：``open_temp_fd`` 在 ``final_path`` 同目录创建不可预测随机名临时文件规避符号
    链接 TOCTOU；``writer`` 接收 fd 异步写入，须以 ``closefd=False`` 包装 fd 使本函数
    独占 fd 关闭权，避免双重关闭与 fd 复用误关他者；写入完成后经线程池执行 ``os.replace``
    原子替换，失败路径清理随机临时文件。mkstemp 与 replace 经 ``asyncio.to_thread``
    卸载，避免在事件循环线程内执行可能阻塞网络文件系统的同步系统调用。

    writer 返回 None 时替换到 ``final_path``；返回 Path 时以该路径为最终目标，供
    写入完成后才能确定最终路径的场景（如按字节签名修正扩展名）使用，返回路径须与
    ``final_path`` 同目录以保证原子性。writer 抛出的异常原样上抛，由调用方按各自
    异常类型分类处理；fd 在 writer 正常返回或抛出后由本函数统一关闭，writer 无需
    也无法安全关闭 fd。

    Args:
        final_path: 最终目标路径，临时文件在其所在目录创建以保证同文件系统原子替换。
        writer: 接收 fd 的异步写入回调，须以 closefd=False 包装 fd，可返回覆盖用的
            最终路径或 None。
        suffix: 临时文件名后缀，用于可读性与调试定位。

    Returns:
        实际创建的随机名临时路径（替换成功后已重命名为最终路径）。

    Raises:
        OSError: 临时文件创建或原子替换失败；writer 抛出的异常经本函数原样上抛。
    """
    fd, temp_path = await asyncio.to_thread(open_temp_fd, final_path.parent, suffix=suffix)
    replaced = False
    try:
        try:
            override_path = await writer(fd)
        finally:
            os.close(fd)
        target = final_path if override_path is None else override_path
        await asyncio.to_thread(temp_path.replace, target)
        replaced = True
    finally:
        if not replaced:
            _cleanup_temp_file(temp_path)
    return temp_path


_FILE_ATTRIBUTE_REPARSE_POINT = stat.FILE_ATTRIBUTE_REPARSE_POINT


def _has_reparse_attribute(st: os.stat_result) -> bool:
    """判断 lstat 结果是否携带 NTFS reparse point 属性位，非 Windows 恒为 False。

    调用方已持有 lstat 结果时经本函数判定，避免 _is_reparse_point 再次 lstat；
    st_file_attributes 仅 Windows 的 stat 结果存在，平台判定先行。
    """
    if sys.platform != "win32":
        return False
    return bool(st.st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _is_reparse_point(path: Path) -> bool:
    """判断路径是否为 NTFS junction 等非符号链接型 reparse point。

    os.scandir(follow_symlinks=False) 与 os.walk(followlinks=False) 均不拦截 junction：
    junction 属 reparse point，is_symlink 对其返回 False，会被下降进入目标执行 OS 级
    listdir。本函数用 reparse point 属性位检测，供目录遍历下降前剔除。仅 Windows 存在
    junction，其他平台直接返回 False。

    供 io_storage 的清理遍历与 io_path 的浏览扫描共用，保持 reparse point 检测单一实现。
    """
    try:
        st = path.lstat()
    except OSError:
        return False
    return _has_reparse_attribute(st)


def atomic_replace_from_fd_sync(
    final_path: Path,
    writer: Callable[[int], None],
    suffix: str = ".part",
) -> Path:
    """经随机名临时文件同步原子落盘，返回实际创建的随机临时路径。

    与 atomic_replace_from_fd 对应的同步版本，供 save_bytes 等同步公共接口直接复用，
    避免在事件循环内 asyncio.run 驱动异步骨架的分层倒置与 RuntimeError 风险。writer
    同步写入 fd 后由本函数 os.replace 原子替换，失败路径清理随机临时文件。mkstemp 与
    replace 为同步系统调用，调用方须处于可阻塞的工作线程或同步上下文。

    writer 抛出的异常原样上抛，由调用方按各自异常类型分类处理；fd 在 writer 正常返回
    或抛出后由本函数统一关闭，writer 须以 closefd=False 包装 fd 使本函数独占 fd 关闭权，
    避免双重关闭与 fd 复用误关他者。

    Args:
        final_path: 最终目标路径，临时文件在其所在目录创建以保证同文件系统原子替换。
        writer: 接收 fd 的同步写入回调，须以 closefd=False 包装 fd。
        suffix: 临时文件名后缀，用于可读性与调试定位。

    Returns:
        实际创建的随机名临时路径（替换成功后已重命名为 final_path）。

    Raises:
        OSError: 临时文件创建或原子替换失败；writer 抛出的异常经本函数原样上抛。
    """
    fd, temp_path = open_temp_fd(final_path.parent, suffix=suffix)
    replaced = False
    try:
        try:
            writer(fd)
        finally:
            os.close(fd)
        temp_path.replace(final_path)
        replaced = True
    finally:
        if not replaced:
            _cleanup_temp_file(temp_path)
    return temp_path
