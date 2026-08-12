import os
from pathlib import Path

import pytest

from seedream_mcp.utils.file_manager import FileManager, FileManagerError


def test_file_manager_rejects_non_directory_base_dir(tmp_path: Path) -> None:
    """指向文件的 base_dir 应被拒绝。"""
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("oops", encoding="utf-8")

    with pytest.raises(FileManagerError, match="不是目录"):
        FileManager(base_dir=file_path)


def test_file_manager_rejects_unresolvable_base_dir() -> None:
    with pytest.raises(FileManagerError, match="解析保存路径时出错"):
        FileManager(base_dir=Path("\0invalid"))


def test_file_manager_accepts_valid_base_dir(tmp_path: Path) -> None:
    base_dir = tmp_path / "images"
    manager = FileManager(base_dir=base_dir)

    assert manager.base_dir == base_dir.resolve()
    assert manager.base_dir.exists()


def test_create_save_path_normalizes_non_image_extension(tmp_path: Path) -> None:
    """URL 派生的非图片扩展名应收敛到白名单默认 .jpeg，防止任意后缀落盘。"""
    manager = FileManager(base_dir=tmp_path)
    path = manager.create_save_path(
        prompt="test", url="https://example.com/img.aspx", tool_name="t"
    )
    assert path.suffix.lower() == ".jpeg"


def test_create_save_path_keeps_whitelisted_extension(tmp_path: Path) -> None:
    manager = FileManager(base_dir=tmp_path)
    path = manager.create_save_path(prompt="test", url="https://example.com/img.png", tool_name="t")
    assert path.suffix.lower() == ".png"


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="需支持 O_NOFOLLOW 的平台")
def test_save_bytes_rejects_symlink(tmp_path: Path) -> None:
    """save_bytes 拒绝写向符号链接，O_NOFOLLOW 根除写路径 TOCTOU。"""
    link = tmp_path / "link"
    os.symlink(tmp_path / "nonexistent_target", link)
    manager = FileManager(base_dir=tmp_path)
    with pytest.raises(FileManagerError):
        manager.save_bytes(link, b"data")
