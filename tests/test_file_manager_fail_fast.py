from pathlib import Path

import pytest

from seedream_mcp.utils.file_manager import FileManager, FileManagerError


def test_file_manager_rejects_unsafe_base_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FileManager, "_is_unsafe_path", lambda self, path: True)

    with pytest.raises(FileManagerError, match="不安全"):
        FileManager(base_dir=Path("safe-looking"))


def test_file_manager_rejects_unresolvable_base_dir() -> None:
    with pytest.raises(FileManagerError, match="解析保存路径时出错"):
        FileManager(base_dir=Path("\0invalid"))


def test_file_manager_accepts_valid_base_dir(tmp_path: Path) -> None:
    base_dir = tmp_path / "images"
    manager = FileManager(base_dir=base_dir)

    assert manager.base_dir == base_dir.resolve()
    assert manager.base_dir.exists()
