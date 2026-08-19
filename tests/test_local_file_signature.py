"""_local_file_signature 缓存键签名测试。

守护多 Root 下签名与读取路径锁定同一文件、避免陈旧缓存，越界文件不泄露存在性。
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


def test_url_and_data_uri_return_zero_signature() -> None:
    """URL 与 data URI 内容由字符串决定，签名返回 (0.0, 0)。"""
    assert ImagePreparer._local_file_signature("https://x/a.png", ("/root",)) == (0.0, 0)
    assert ImagePreparer._local_file_signature("data:image/png;base64,", ("/root",)) == (0.0, 0)


def test_nonexistent_relative_path_returns_zero(tmp_path: Path) -> None:
    assert ImagePreparer._local_file_signature("nope.png", (str(tmp_path),)) == (0.0, 0)


def test_absolute_path_outside_roots_returns_zero(tmp_path: Path) -> None:
    """绝对路径不在任一 root 内时返回零，避免越界文件成为存在性 oracle。"""
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG_BYTES)
    roots = (str(tmp_path / "workspace"),)
    assert ImagePreparer._local_file_signature(str(outside), roots) == (0.0, 0)


def test_multi_root_skips_invalid_picks_valid(tmp_path: Path) -> None:
    """Root1 同名条目为目录不可读取、Root2 为有效文件时，签名锁定 Root2 避免陈旧缓存。"""
    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    root1.mkdir()
    root2.mkdir()
    # Root1 的 photo.png 是目录，读取路径会跳过
    (root1 / "photo.png").mkdir()
    valid = root2 / "photo.png"
    valid.write_bytes(_PNG_BYTES)

    sig = ImagePreparer._local_file_signature("photo.png", (str(root1), str(root2)))
    st = valid.stat()
    assert sig == (st.st_mtime, st.st_size)


def test_multi_root_skips_oversized_picks_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root1 同名文件超大小上限、Root2 合法时，签名锁定 Root2。

    大小上限经 monkeypatch 缩到 KB 级触发同一条跳过分支；被测代码读取的是
    image_validation 命名空间的 MAX_IMAGE_FILE_SIZE，patch 目标据此确定。
    """
    monkeypatch.setattr(image_validation, "MAX_IMAGE_FILE_SIZE", 64 * 1024)
    root1 = tmp_path / "big"
    root2 = tmp_path / "ok"
    root1.mkdir()
    root2.mkdir()
    (root1 / "photo.png").write_bytes(b"\x00" * (64 * 1024 + 1))
    valid = root2 / "photo.png"
    valid.write_bytes(_PNG_BYTES)

    sig = ImagePreparer._local_file_signature("photo.png", (str(root1), str(root2)))
    st = valid.stat()
    assert sig == (st.st_mtime, st.st_size)


def test_signature_delegates_to_shared_candidate_resolution(tmp_path: Path) -> None:
    """签名与读取共用 resolve_local_image_candidate 定位候选，不会因规则漂移锁定不同文件。"""
    from seedream_mcp.utils.images.image_validation import resolve_local_image_candidate

    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    root1.mkdir()
    root2.mkdir()
    (root1 / "photo.png").mkdir()
    valid = root2 / "photo.png"
    valid.write_bytes(_PNG_BYTES)
    resolved_roots = [root1.resolve(), root2.resolve()]

    found = resolve_local_image_candidate("photo.png", resolved_roots)
    assert found is not None
    path, st = found

    assert path == valid.resolve()
    assert (st.st_mtime, st.st_size) == ImagePreparer._local_file_signature(
        "photo.png", (str(root1), str(root2))
    )


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="符号链接需 POSIX 与创建权限",
)
def test_final_component_symlink_follows_target_within_root(tmp_path: Path) -> None:
    """界内符号链接按 resolve 跟随语义取目标文件的签名，与读取路径锁定同一文件。

    越界防御由 resolve 后的越界判定承担而非拒绝链接本身；界内链接等价直接引用
    目标，mtime+size 失效保护对链接替换同样生效。
    """
    target = tmp_path / "real.png"
    target.write_bytes(_PNG_BYTES)
    link = tmp_path / "link.png"
    os.symlink(target, link)

    st = target.stat()
    assert ImagePreparer._local_file_signature("link.png", (str(tmp_path),)) == (
        st.st_mtime,
        st.st_size,
    )


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="符号链接需 POSIX 与创建权限",
)
def test_final_component_symlink_escaping_root_returns_zero(tmp_path: Path) -> None:
    """指向根外的符号链接经 resolve 后越界，签名返回零不泄露目标文件信息。"""
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG_BYTES)
    root = tmp_path / "workspace"
    root.mkdir()
    link = root / "link.png"
    os.symlink(outside, link)

    assert ImagePreparer._local_file_signature("link.png", (str(root),)) == (0.0, 0)
