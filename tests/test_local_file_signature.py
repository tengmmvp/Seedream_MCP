"""_local_file_signature 缓存键签名测试。

守护签名与读取路径锁定同一文件、避免陈旧缓存，越界文件不泄露存在性。相对路径
以存储根为基准，判定面向读权限集合（工作区 ∪ 存储根）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from seedream_mcp.utils.images import image_validation
from seedream_mcp.utils.images.image_prepare import ImagePreparer

# 合法 PNG 文件头，供构造可读取的候选常规文件
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


@pytest.fixture
def save_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """以 tmp 为工作区根，返回派生的存储根并预建目录。"""
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    root = tmp_path / ".seedream" / "images"
    root.mkdir(parents=True)
    return root


def test_url_and_data_uri_return_zero_signature() -> None:
    """URL 与 data URI 内容由字符串决定，签名返回 (0.0, 0)。"""
    assert ImagePreparer._local_file_signature("https://x/a.png") == (0.0, 0)
    assert ImagePreparer._local_file_signature("data:image/png;base64,") == (0.0, 0)


def test_nonexistent_relative_path_returns_zero(save_root: Path) -> None:
    """相对路径在存储根下无候选文件时返回零。"""
    del save_root
    assert ImagePreparer._local_file_signature("nope.png") == (0.0, 0)


def test_absolute_path_outside_scope_returns_zero(tmp_path: Path, save_root: Path) -> None:
    """绝对路径在读权限（工作区 ∪ 存储根）之外时返回零，避免越界文件成为存在性 oracle。"""
    del save_root
    outside = tmp_path.parent / "signature-outside.png"
    outside.write_bytes(_PNG_BYTES)
    assert ImagePreparer._local_file_signature(str(outside)) == (0.0, 0)


def test_directory_named_as_image_returns_zero(save_root: Path) -> None:
    """存储根内与图片同名的目录不可作为候选，签名返回零。"""
    (save_root / "photo.png").mkdir()
    assert ImagePreparer._local_file_signature("photo.png") == (0.0, 0)


def test_oversized_candidate_returns_zero(save_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超过大小上限的候选返回零。

    大小上限经 monkeypatch 缩到 KB 级触发跳过分支；被测代码读取的是
    image_validation 命名空间的 MAX_IMAGE_FILE_SIZE，patch 目标据此确定。
    """
    monkeypatch.setattr(image_validation, "MAX_IMAGE_FILE_SIZE", 64 * 1024)
    (save_root / "photo.png").write_bytes(b"\x00" * (64 * 1024 + 1))
    assert ImagePreparer._local_file_signature("photo.png") == (0.0, 0)


def test_signature_delegates_to_shared_candidate_resolution(save_root: Path) -> None:
    """签名与读取共用 resolve_local_image_candidate 定位候选，不会因规则漂移锁定不同文件。"""
    from seedream_mcp.utils.images.image_validation import resolve_local_image_candidate

    valid = save_root / "photo.png"
    valid.write_bytes(_PNG_BYTES)

    found = resolve_local_image_candidate("photo.png")
    assert found is not None
    path, st = found

    assert path == valid.resolve()
    assert (st.st_mtime, st.st_size) == ImagePreparer._local_file_signature("photo.png")


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="符号链接需 POSIX 与创建权限",
)
def test_final_component_symlink_follows_target_within_scope(save_root: Path) -> None:
    """读权限内符号链接按 resolve 跟随语义取目标文件的签名，与读取路径锁定同一文件。"""
    target = save_root / "real.png"
    target.write_bytes(_PNG_BYTES)
    link = save_root / "link.png"
    os.symlink(target, link)

    st = target.stat()
    assert ImagePreparer._local_file_signature("link.png") == (st.st_mtime, st.st_size)


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="符号链接需 POSIX 与创建权限",
)
def test_final_component_symlink_escaping_scope_returns_zero(
    tmp_path: Path, save_root: Path
) -> None:
    """指向读权限外的符号链接经 resolve 后越界，签名返回零不泄露目标文件信息。"""
    del tmp_path
    outside = save_root.parent.parent.parent / "signature-escape.png"
    outside.write_bytes(_PNG_BYTES)
    link = save_root / "link.png"
    os.symlink(outside, link)

    assert ImagePreparer._local_file_signature("link.png") == (0.0, 0)
