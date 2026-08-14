"""os_utils.open_no_follow_read 守护测试。

覆盖三种场景：
(a) 平台支持 O_NOFOLLOW 时，最终分量为符号链接的路径被拒绝；
(b) 模拟平台不支持 O_NOFOLLOW，monkeypatch 置 O_NOFOLLOW=0，is_symlink 兜底分支拒绝符号链接；
(c) 正常文件读取成功。

Windows 创建符号链接需特权或开发者模式，相关用例以 try/skip 跳过，避免 WinError 1314。
"""

import os
from pathlib import Path

import pytest

from seedream_mcp.utils.os_utils import open_no_follow_read


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
    """平台支持 O_NOFOLLOW 时，最终分量为符号链接的路径读取被拒绝。"""
    if not getattr(os, "O_NOFOLLOW", 0):
        pytest.skip("当前平台不支持 O_NOFOLLOW")
    link = _make_symlink(tmp_path, "read_native")

    with pytest.raises(OSError):
        open_no_follow_read(link)


def test_open_no_follow_read_fallback_rejects_symlink_without_no_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟平台不支持 O_NOFOLLOW：is_symlink 兜底分支拒绝符号链接读取。"""
    link = _make_symlink(tmp_path, "read_fallback")

    # 强制 no_follow 取值为 0，触发 is_symlink 兜底分支
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
    with pytest.raises(OSError, match="拒绝跟随符号链接"):
        open_no_follow_read(link)


def test_open_no_follow_fallback_allows_normal_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟不支持 O_NOFOLLOW 时，正常文件即非符号链接的读取仍成功。

    确保兜底分支不会误伤普通文件。
    """
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)

    path = tmp_path / "plain.bin"
    path.write_bytes(b"ok")

    with open_no_follow_read(path) as handle:
        assert handle.read() == b"ok"


def test_open_no_follow_fallback_rejects_fstat_toctou_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fstat 与 lstat 的 st_ino 不一致时，_open_no_follow_fallback 拒绝以闭合 TOCTOU 竞态。

    模拟平台不支持 O_NOFOLLOW，强制走 lstat+fstat 同一性复核分支；monkeypatch os.fstat
    返回不同 inode，模拟校验与打开之间最终分量被替换为符号链接的场景。
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

    with pytest.raises(OSError, match="被替换"):
        open_no_follow_read(path)
