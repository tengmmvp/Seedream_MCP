"""FileManager 的 validate_path 越界守卫与 cleanup_old_files 测试（A5 安全覆盖）。"""

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
    """符号链接指向 base_dir 之外时，cleanup 不得删除其目标（L1 越权防护）。"""
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
