"""OS 级文件打开工具：O_NOFOLLOW 防符号链接。

统一 file_manager.save_bytes 与 image_input._prepare_local_image 的打开逻辑，
消除两处重复实现。最终路径分量若为符号链接则拒绝；支持 O_NOFOLLOW 的平台由
内核在 open 时原子拒绝符号链接。Windows 等平台不支持 O_NOFOLLOW 时，退化为
打开前 lstat 与打开后 fstat 比对 st_ino/st_dev 同一性：lstat 先取最终分量指纹，
open 后用 fstat 复核打开的 fd 仍是同一对象，若校验与打开之间最终分量被替换为
符号链接则 st_ino/st_dev 不一致，据此拒绝，闭合该 TOCTOU 竞态。共享函数抛
OSError，由调用方按各自异常类型包装。

残余风险：O_NOFOLLOW 仅保护最终路径分量，不阻止内核 open 跟随中间目录的
符号链接；若校验与打开之间某父目录被替换为指向工作区外的符号链接，读取会
逃逸出工作区。Windows 无 O_NOFOLLOW 时，O_CREAT 新建尚不存在的路径无既有
分量指纹可比对，其符号链接替换竞态仍存在；上述攻击均需本地写权限与精确时序，
属下层威胁。
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import IO

PathLike = str | Path


def _open_no_follow_fallback(path_str: str, flags: int, *, creating: bool, action: str) -> int:
    """无 O_NOFOLLOW 平台的兜底打开，返回文件描述符。

    lstat 先取最终分量指纹，符号链接直接拒绝；open 后用 fstat 复核打开的 fd 仍是
    同一对象，校验与打开之间最终分量被替换为符号链接则 st_ino/st_dev 不一致，据此
    拒绝，闭合该 TOCTOU 竞态。

    Args:
        path_str: 目标路径字符串。
        flags: 传给 os.open 的标志位，不含 O_NOFOLLOW。
        creating: 是否为创建语义。True 时文件不存在直接 open，O_CREAT 将新建，
            无既有分量可被替换，跳过同一性复核。
        action: 操作描述，用于错误消息。

    Returns:
        打开的文件描述符。
    """
    if creating:
        try:
            pre_st = os.lstat(path_str)
        except FileNotFoundError:
            return os.open(path_str, flags)
    else:
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
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow:
        return os.fdopen(os.open(str(path), os.O_RDONLY | no_follow), "rb")
    fd = _open_no_follow_fallback(str(path), os.O_RDONLY, creating=False, action="读取")
    return os.fdopen(fd, "rb")


def open_no_follow_write(path: PathLike) -> IO[bytes]:
    """以 O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW 打开文件，返回二进制只写文件对象。

    最终路径分量若为符号链接则拒绝；平台不支持 O_NOFOLLOW 时退化为 lstat 取指纹、
    open 后 fstat 比对 st_ino/st_dev 同一性，闭合最终分量替换竞态。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | no_follow
    if no_follow:
        return os.fdopen(os.open(str(path), flags), "wb")
    fd = _open_no_follow_fallback(str(path), flags, creating=True, action="写入")
    return os.fdopen(fd, "wb")


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
    """
    fd, name = tempfile.mkstemp(dir=str(dir_path), suffix=suffix)
    return fd, Path(name)
