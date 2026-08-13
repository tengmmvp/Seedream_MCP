"""_local_file_signature 缓存键签名测试。

验证候选文件选择与读取路径对齐、最终分量符号链接拒绝、越界守卫不沦为存在性 oracle。
重点守护多 Root 工作区下签名与读取锁定同一文件，避免命中陈旧缓存。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from seedream_mcp.client import SeedreamClient

# 合法 PNG 文件头，供构造可读取的候选常规文件
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def test_url_and_data_uri_return_zero_signature() -> None:
    """URL 与 data URI 内容由字符串决定，签名返回 (0.0, 0)。"""
    assert SeedreamClient._local_file_signature("https://x/a.png", ("/root",)) == (0.0, 0)
    assert SeedreamClient._local_file_signature("data:image/png;base64,", ("/root",)) == (0.0, 0)


def test_nonexistent_relative_path_returns_zero(tmp_path: Path) -> None:
    assert SeedreamClient._local_file_signature("nope.png", (str(tmp_path),)) == (0.0, 0)


def test_absolute_path_outside_roots_returns_zero(tmp_path: Path) -> None:
    """绝对路径不在任一 root 内时返回零，避免越界文件成为存在性 oracle。"""
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG_BYTES)
    roots = (str(tmp_path / "workspace"),)
    assert SeedreamClient._local_file_signature(str(outside), roots) == (0.0, 0)


def test_multi_root_skips_invalid_picks_valid(tmp_path: Path) -> None:
    """Root1 同名条目无效（目录）+ Root2 有效文件 → 签名锁定 Root2，避免陈旧缓存。"""
    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    root1.mkdir()
    root2.mkdir()
    # Root1 的 photo.png 是目录（非 regular），读取路径会跳过
    (root1 / "photo.png").mkdir()
    # Root2 的 photo.png 是可读取的有效文件
    valid = root2 / "photo.png"
    valid.write_bytes(_PNG_BYTES)

    sig = SeedreamClient._local_file_signature("photo.png", (str(root1), str(root2)))
    st = valid.stat()
    assert sig == (st.st_mtime, st.st_size)


def test_multi_root_skips_oversized_picks_valid(tmp_path: Path) -> None:
    """Root1 同名文件超 30MB（校验失败）+ Root2 合法 → 签名锁定 Root2。"""
    root1 = tmp_path / "big"
    root2 = tmp_path / "ok"
    root1.mkdir()
    root2.mkdir()
    (root1 / "photo.png").write_bytes(b"\x00" * (30 * 1024 * 1024 + 1))
    valid = root2 / "photo.png"
    valid.write_bytes(_PNG_BYTES)

    sig = SeedreamClient._local_file_signature("photo.png", (str(root1), str(root2)))
    st = valid.stat()
    assert sig == (st.st_mtime, st.st_size)


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="符号链接需 POSIX 与创建权限",
)
def test_final_component_symlink_rejected(tmp_path: Path) -> None:
    """最终分量为符号链接时签名返回零，与 open_no_follow_read 的拒绝语义一致。"""
    target = tmp_path / "real.png"
    target.write_bytes(_PNG_BYTES)
    link = tmp_path / "link.png"
    os.symlink(target, link)

    assert SeedreamClient._local_file_signature("link.png", (str(tmp_path),)) == (0.0, 0)
