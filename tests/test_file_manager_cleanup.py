"""FileManager 的 validate_path 越界守卫与 cleanup_old_files 测试。"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from seedream_mcp.utils.file_manager import FileManager


def test_validate_path_accepts_inside_base(tmp_path: Path) -> None:
    manager = FileManager(base_dir=tmp_path)
    assert manager.validate_path(tmp_path / "nested" / "image.png") is True


def test_validate_path_rejects_outside_base(tmp_path: Path) -> None:
    manager = FileManager(base_dir=tmp_path)
    outside = tmp_path.parent / "sibling.png"
    assert manager.validate_path(outside) is False


def test_cleanup_old_files_removes_expired_and_keeps_recent(tmp_path: Path) -> None:
    manager = FileManager(base_dir=tmp_path)

    old_file = tmp_path / "old.png"
    old_file.write_bytes(b"old")
    old_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(old_file, (old_time, old_time))

    new_file = tmp_path / "new.png"
    new_file.write_bytes(b"new")

    result = manager.cleanup_old_files(days=30)

    assert not old_file.exists()
    assert new_file.exists()
    assert result["deleted_files"] >= 1


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 创建符号链接需管理员权限")
def test_cleanup_old_files_skips_symlink_pointing_outside(tmp_path: Path) -> None:
    """符号链接指向 base_dir 之外时，cleanup 不得删除其目标，防止越权删除。"""
    manager = FileManager(base_dir=tmp_path)

    outside_dir = tmp_path.parent / "outside_target"
    outside_dir.mkdir(exist_ok=True)
    target = outside_dir / "target.png"
    target.write_bytes(b"target")
    old_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(target, (old_time, old_time))

    link = tmp_path / "link.png"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("当前环境不支持创建符号链接")

    manager.cleanup_old_files(days=30)

    # 符号链接自身可能被跳过；但其指向的外部目标必须不被删除
    assert target.exists()


def test_cleanup_old_files_does_not_descend_into_symlink_dir(tmp_path: Path) -> None:
    """符号链接目录指向 base_dir 之外时，cleanup 不得下降进入该目录遍历外部条目。

    构造 base_dir 内的符号链接目录，指向 base_dir 之外的临时目录；该外部目录含一个
    mtime 已过期的 marker 文件。若 cleanup 错误地跟随符号链接目录下降，会 stat 到该
    marker 并因过期将其删除（Windows 下还可能触发指向外部资源的 SMB 出站认证）。
    断言 cleanup 后外部 marker 仍存在、内容未被触碰、且未计入删除数量。
    """
    manager = FileManager(base_dir=tmp_path)

    # 在 base_dir 之外的外部目录放置一个过期 marker 文件
    outside_dir = tmp_path.parent / "outside_symlink_dir_target"
    outside_dir.mkdir(exist_ok=True)
    marker = outside_dir / "marker.png"
    marker.write_bytes(b"marker-content")
    old_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(marker, (old_time, old_time))

    # base_dir 内创建指向外部目录的符号链接目录
    link_dir = tmp_path / "link_dir"
    try:
        os.symlink(str(outside_dir), str(link_dir), target_is_directory=True)
    except (OSError, AttributeError):
        pytest.skip("当前进程无法创建符号链接（Windows 可能需要开发者模式或管理员）")

    result = manager.cleanup_old_files(days=30)

    # marker 已过期，若 cleanup 下降进入符号链接目录则会被删除；
    # 其仍存在即证明 cleanup 未对外部条目下降遍历
    assert marker.exists()
    assert marker.read_bytes() == b"marker-content"
    assert result["deleted_files"] == 0
