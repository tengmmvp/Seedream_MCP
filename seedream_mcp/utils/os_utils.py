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
逃逸出工作区。Windows 无 O_NOFOLLOW 时，新建文件场景（O_CREAT 打开尚不存在
的路径）无既有分量指纹可比对，其符号链接替换竞态仍存在；上述攻击均需本地写
权限与精确时序，属下层威胁。
"""

import os
import stat
from pathlib import Path
from typing import IO, Union

PathLike = Union[str, Path]


def open_no_follow_read(path: PathLike) -> IO[bytes]:
    """以 O_RDONLY | O_NOFOLLOW 打开文件，返回二进制只读文件对象。

    最终路径分量若为符号链接则拒绝；平台不支持 O_NOFOLLOW 时退化为 lstat 取指纹、
    open 后 fstat 比对 st_ino/st_dev 同一性，闭合最终分量替换竞态。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow:
        fd = os.open(str(path), os.O_RDONLY | no_follow)
        return os.fdopen(fd, "rb")
    # 无 O_NOFOLLOW 平台兜底：lstat 取最终分量同一性指纹，open 后 fstat 复核，
    # 闭合 is_symlink 检查与 open 之间最终分量被替换为符号链接的 TOCTOU 竞态。
    pre_st = os.lstat(str(path))
    if stat.S_ISLNK(pre_st.st_mode):
        raise OSError(f"拒绝跟随符号链接读取: {path}")
    fd = os.open(str(path), os.O_RDONLY)
    try:
        post_st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if post_st.st_ino != pre_st.st_ino or post_st.st_dev != pre_st.st_dev:
        os.close(fd)
        raise OSError(f"打开期间最终分量被替换，拒绝读取: {path}")
    return os.fdopen(fd, "rb")


def open_no_follow_write(path: PathLike) -> IO[bytes]:
    """以 O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW 打开文件，返回二进制只写文件对象。

    最终路径分量若为符号链接则拒绝；平台不支持 O_NOFOLLOW 时退化为 lstat 取指纹、
    open 后 fstat 比对 st_ino/st_dev 同一性，闭合最终分量替换竞态。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | no_follow
    if no_follow:
        fd = os.open(str(path), flags)
        return os.fdopen(fd, "wb")
    # 无 O_NOFOLLOW 平台兜底：同 open_no_follow_read 的 lstat/fstat 同一性复核。
    path_str = str(path)
    try:
        pre_st = os.lstat(path_str)
    except FileNotFoundError:
        # 文件尚不存在，O_CREAT 将新建，无既有分量可被替换，直接打开跳过同一性复核
        return os.fdopen(os.open(path_str, flags), "wb")
    if stat.S_ISLNK(pre_st.st_mode):
        raise OSError(f"拒绝写入符号链接: {path}")
    fd = os.open(path_str, flags)
    try:
        post_st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if post_st.st_ino != pre_st.st_ino or post_st.st_dev != pre_st.st_dev:
        os.close(fd)
        raise OSError(f"打开期间最终分量被替换，拒绝写入: {path}")
    return os.fdopen(fd, "wb")


def open_no_follow_fd(path: PathLike, flags: int) -> int:
    """以调用方 flags 附加 O_NOFOLLOW 打开最终分量，返回文件描述符。

    供需要自定义写入方式的调用方使用，典型场景是经 aiofiles 异步包装 fd。``flags``
    须包含方向与创建位，如 ``os.O_WRONLY | os.O_CREAT | os.O_TRUNC``。最终路径
    分量若为符号链接则拒绝；平台不支持 O_NOFOLLOW 时退化为 lstat 取指纹、open 后
    fstat 比对 st_ino/st_dev 同一性，闭合最终分量替换竞态。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow:
        return os.open(str(path), flags | no_follow)
    # 无 O_NOFOLLOW 平台兜底：同 open_no_follow_write 的 lstat/fstat 同一性复核。
    path_str = str(path)
    try:
        pre_st = os.lstat(path_str)
    except FileNotFoundError:
        # 文件尚不存在，O_CREAT 将新建，无既有分量可被替换，直接打开跳过同一性复核
        return os.open(path_str, flags)
    if stat.S_ISLNK(pre_st.st_mode):
        raise OSError(f"拒绝写入符号链接: {path}")
    fd = os.open(path_str, flags)
    try:
        post_st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if post_st.st_ino != pre_st.st_ino or post_st.st_dev != pre_st.st_dev:
        os.close(fd)
        raise OSError(f"打开期间最终分量被替换，拒绝写入: {path}")
    return fd
