"""OS 级文件打开工具：O_NOFOLLOW 防符号链接。

统一 file_manager.save_bytes 与 image_input._prepare_local_image 的打开逻辑，
消除两处重复实现。最终路径分量若为符号链接则拒绝；平台不支持 O_NOFOLLOW 时
（如 Windows）退化为打开前 is_symlink 拒绝，保留同等安全语义。共享函数抛
OSError，由调用方按各自异常类型包装。
"""

import os
from pathlib import Path
from typing import IO, Union

PathLike = Union[str, Path]


def open_no_follow_read(path: PathLike) -> IO[bytes]:
    """以 O_RDONLY | O_NOFOLLOW 打开文件，返回二进制只读文件对象。

    最终路径分量若为符号链接则拒绝；平台不支持 O_NOFOLLOW 时前置 is_symlink 兜底。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow and Path(path).is_symlink():
        raise OSError(f"拒绝跟随符号链接读取: {path}")
    fd = os.open(str(path), os.O_RDONLY | no_follow)
    return os.fdopen(fd, "rb")


def open_no_follow_write(path: PathLike) -> IO[bytes]:
    """以 O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW 打开文件，返回二进制只写文件对象。

    最终路径分量若为符号链接则拒绝；平台不支持 O_NOFOLLOW 时前置 is_symlink 兜底。
    """
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow and Path(path).is_symlink():
        raise OSError(f"拒绝写入符号链接: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | no_follow
    fd = os.open(str(path), flags)
    return os.fdopen(fd, "wb")
