"""FileManager 快速失败与保存路径扩展名收敛测试。"""

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


def test_save_bytes_atomic_write_leaves_no_temp(tmp_path: Path) -> None:
    """save_bytes 经随机名临时文件原子 replace 落盘，成功后不留临时文件残留。"""
    manager = FileManager(base_dir=tmp_path)
    path = tmp_path / "out.png"

    result = manager.save_bytes(path, b"payload")

    assert path.read_bytes() == b"payload"
    # 成功落盘后目录内仅最终文件，无随机名临时文件残留
    assert list(tmp_path.iterdir()) == [path]
    assert result["file_size"] == len(b"payload")
    assert result["file_path"] == str(path)


def test_save_bytes_overwrite_replaces_existing_file(tmp_path: Path) -> None:
    """overwrite=True 时原子 replace 覆盖已有文件。"""
    manager = FileManager(base_dir=tmp_path)
    path = tmp_path / "out.png"
    path.write_bytes(b"old-content")

    manager.save_bytes(path, b"new", overwrite=True)

    assert path.read_bytes() == b"new"


def test_save_bytes_no_overwrite_renames_on_conflict(tmp_path: Path) -> None:
    """overwrite=False 且文件已存在时，追加内容短哈希生成不冲突的新文件名。"""
    manager = FileManager(base_dir=tmp_path)
    path = tmp_path / "out.png"
    path.write_bytes(b"old-content")

    manager.save_bytes(path, b"new-content", overwrite=False)

    # 原文件保留旧内容
    assert path.read_bytes() == b"old-content"
    # 新文件以内容哈希后缀生成
    png_files = [f for f in tmp_path.iterdir() if f.suffix == ".png"]
    assert len(png_files) == 2


def test_save_bytes_cleans_random_temp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """replace 失败时随机名临时文件被 finally 清理，目录内不留残留。"""
    from seedream_mcp.utils import file_manager as fm_module

    manager = FileManager(base_dir=tmp_path)
    path = tmp_path / "out.png"

    def _raise_on_replace(_src: object, _dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(fm_module.os, "replace", _raise_on_replace)

    with pytest.raises(FileManagerError, match="写入文件失败"):
        manager.save_bytes(path, b"data")

    # 失败路径清理随机名临时文件，目录内无残留
    assert list(tmp_path.iterdir()) == []
