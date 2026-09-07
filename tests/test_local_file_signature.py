"""本地候选定位与缓存签名测试。

守护签名与读取路径锁定同一文件、避免陈旧缓存，越界文件不泄露存在性。相对路径
以存储区为基准，判定面向读权限集合（工作区 ∪ 存储区）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image

from seedream_mcp.utils.images import image_input as image_input_module
from seedream_mcp.utils.images import image_prepare
from seedream_mcp.utils.images import image_validation
from seedream_mcp.utils.images.image_prepare import ImagePreparer

# 合法 PNG 文件头，供构造可读取的候选常规文件
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


@pytest.fixture
def save_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """以 tmp 为工作区根，返回派生的存储区并预建目录。"""
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    root = tmp_path / ".seedream" / "images"
    root.mkdir(parents=True)
    return root


def test_url_and_data_uri_return_no_candidate() -> None:
    """URL 与 data URI 内容由字符串决定，无本地候选。"""
    assert ImagePreparer._local_candidate("https://x/a.png") is None
    assert ImagePreparer._local_candidate("data:image/png;base64,") is None


def test_nonexistent_relative_path_returns_none(save_root: Path) -> None:
    """相对路径在存储区下无候选文件时返回 None。"""
    del save_root
    assert ImagePreparer._local_candidate("nope.png") is None


def test_absolute_path_outside_scope_returns_none(tmp_path: Path, save_root: Path) -> None:
    """绝对路径在读权限（工作区 ∪ 存储区）之外时返回 None，避免越界文件成为存在性 oracle。"""
    del save_root
    outside = tmp_path.parent / "signature-outside.png"
    outside.write_bytes(_PNG_BYTES)
    assert ImagePreparer._local_candidate(str(outside)) is None


def test_directory_named_as_image_returns_none(save_root: Path) -> None:
    """存储区内与图片同名的目录不可作为候选。"""
    (save_root / "photo.png").mkdir()
    assert ImagePreparer._local_candidate("photo.png") is None


def test_oversized_candidate_returns_none(save_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超过大小上限的候选返回 None。

    大小上限经 monkeypatch 缩到 KB 级触发跳过分支；被测代码读取的是
    image_validation 命名空间的 MAX_IMAGE_FILE_SIZE，patch 目标据此确定。
    """
    monkeypatch.setattr(image_validation, "MAX_IMAGE_FILE_SIZE", 64 * 1024)
    (save_root / "photo.png").write_bytes(b"\x00" * (64 * 1024 + 1))
    assert ImagePreparer._local_candidate("photo.png") is None


def test_candidate_delegates_to_shared_candidate_resolution(save_root: Path) -> None:
    """候选定位直接复用 resolve_local_image_candidate，不会因规则漂移锁定不同文件。"""
    from seedream_mcp.utils.images.image_validation import resolve_local_image_candidate

    valid = save_root / "photo.png"
    valid.write_bytes(_PNG_BYTES)

    found = resolve_local_image_candidate("photo.png")
    assert found is not None
    assert ImagePreparer._local_candidate("photo.png") == found
    assert found[0] == valid.resolve()


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="符号链接需 POSIX 与创建权限",
)
def test_final_component_symlink_follows_target_within_scope(save_root: Path) -> None:
    """读权限内符号链接按 resolve 跟随语义取目标文件的候选，与读取路径锁定同一文件。"""
    target = save_root / "real.png"
    target.write_bytes(_PNG_BYTES)
    link = save_root / "link.png"
    os.symlink(target, link)

    candidate = ImagePreparer._local_candidate("link.png")
    st = target.stat()
    assert candidate is not None
    assert candidate[0] == target.resolve()
    assert (candidate[1].st_mtime, candidate[1].st_size) == (st.st_mtime, st.st_size)


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="符号链接需 POSIX 与创建权限",
)
def test_final_component_symlink_escaping_scope_returns_none(
    tmp_path: Path, save_root: Path
) -> None:
    """指向读权限外的符号链接经 resolve 后越界，返回 None 不泄露目标文件信息。"""
    del tmp_path
    outside = save_root.parent.parent.parent / "signature-escape.png"
    outside.write_bytes(_PNG_BYTES)
    link = save_root / "link.png"
    os.symlink(outside, link)

    assert ImagePreparer._local_candidate("link.png") is None


async def test_prepare_local_input_localizes_candidate_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地输入缓存 miss 链只做一次候选定位，读取复用签名定位的同一候选。

    此前签名与读取各定位一次，两次定位之间文件被替换时缓存键记旧 (mtime, size)
    而缓存值存新内容。
    """
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    image_path = tmp_path / "ref.png"
    Image.new("RGB", (32, 32), color="white").save(image_path, format="PNG")

    calls = 0
    real = image_validation.resolve_local_image_candidate

    def counting(image: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real(image, **kwargs)

    monkeypatch.setattr(image_input_module, "resolve_local_image_candidate", counting)
    monkeypatch.setattr(image_prepare, "resolve_local_image_candidate", counting)

    preparer = ImagePreparer(
        prepare_cache_max=4, prepare_cache_max_bytes=16 * 1024 * 1024, prepare_concurrency=1
    )
    result = await preparer.prepare_image_input(str(image_path))

    assert result.startswith("data:image/png;base64,")
    assert calls == 1
