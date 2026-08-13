"""find_images_in_directory 的字典序遍历与 limit 提前停止测试。

回归保护：分页浏览在大目录下不能退化为全量收集+排序。limit 必须真正限制返回量，
顺序为文件名字典序（与全局 sorted 前 N 等价），保证跨请求分页连续一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import seedream_mcp.utils.path_utils as path_utils_module
from seedream_mcp.utils.path_utils import find_images_in_directory


def test_limit_returns_sorted_prefix_not_creation_order(tmp_path: Path) -> None:
    # 打乱创建顺序，确保结果不是"碰巧按创建序"
    for i in [5, 0, 9, 2, 7, 1, 8, 3, 6, 4]:
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False, limit=3)

    assert [p.name for p in result] == ["img_00.png", "img_01.png", "img_02.png"]


def test_limit_one_returns_first_sorted(tmp_path: Path) -> None:
    for name in ("c.png", "a.png", "b.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False, limit=1)

    assert [p.name for p in result] == ["a.png"]


def test_no_limit_returns_all_sorted(tmp_path: Path) -> None:
    for name in ("c.png", "a.png", "b.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False)

    assert [p.name for p in result] == ["a.png", "b.png", "c.png"]


def test_recursive_limit_one_skips_later_subtrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 递归结构：root 下 a/ 与 b/ 各含一张图。limit=1 时字典序遍历进 a/ 取到首张即停，
    # 不应再扫描 b/，直接证明 limit 提前停止真正限制了扫描量。
    sub_a = tmp_path / "a"
    sub_b = tmp_path / "b"
    sub_a.mkdir()
    sub_b.mkdir()
    (sub_a / "a1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (sub_b / "b1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    scanned: list[str] = []
    original_scandir = path_utils_module.os.scandir

    def _spy(path: Any) -> Any:
        scanned.append(str(path))
        return original_scandir(path)

    monkeypatch.setattr(path_utils_module.os, "scandir", _spy)

    result = find_images_in_directory(str(tmp_path), recursive=True, max_depth=3, limit=1)

    assert [p.name for p in result] == ["a1.png"]
    assert str(sub_b) not in scanned, "limit=1 应在取到首张后停止，不应扫描 b/ 子树"


def test_limit_respects_extension_filter(tmp_path: Path) -> None:
    # 非图片文件参与字典序占位但不进入结果
    (tmp_path / "0_readme.txt").write_bytes(b"x")
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False, limit=1)

    assert [p.name for p in result] == ["a.png"]


def test_sort_matches_path_semantics(tmp_path: Path) -> None:
    # 大小写混排：遍历序必须与 sorted(Path) 完全一致。
    # Path 比较走 os.path.normcase：Windows 大小写不敏感、POSIX 敏感——两者须逐位等价。
    names = ("Z.png", "a.png", "B.png")
    for name in names:
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False)
    expected = sorted(Path(tmp_path, name) for name in names)

    assert result == expected


def test_non_positive_limit_returns_empty_without_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # limit<=0：返回数量上限为 0，直接返回空列表，且不应触发任何目录扫描
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    scanned: list[str] = []
    original_scandir = path_utils_module.os.scandir

    def _spy(path: Any) -> Any:
        scanned.append(str(path))
        return original_scandir(path)

    monkeypatch.setattr(path_utils_module.os, "scandir", _spy)

    assert find_images_in_directory(str(tmp_path), recursive=False, limit=0) == []
    assert find_images_in_directory(str(tmp_path), recursive=False, limit=-3) == []
    assert scanned == [], "limit<=0 应直接返回空，不触发扫描"


def test_recursive_order_matches_global_sorted_path(tmp_path: Path) -> None:
    # 递归且包含前缀目录名 a/a1/a10 与多层级 a/b：
    # 深度优先加同级按 normcase 排序，须与对全部结果做 sorted(Path) 全局等价；
    # 同父兄弟前缀相同，整串比较退化为子段比较，等价于 Path 的 parts 全局序，
    # 这是分页跨请求顺序连续一致的前提，须作为回归锁定。
    # 注意 Windows 文件系统大小写不敏感，不能同时建 b/ 与 B/ 目录以免冲突，
    # 大小写排序改由单层 test_sort_matches_path_semantics 覆盖。
    (tmp_path / "a" / "b").mkdir(parents=True)
    for name in ("a1", "a10", "sub1", "sub10"):
        (tmp_path / name).mkdir()
    for rel in (
        "a/m.png",
        "a/z.jpg",
        "a/b/deep.png",
        "a1/a.png",
        "a10/b.png",
        "a10/c.png",
        "sub1/i.png",
        "sub10/j.png",
    ):
        (tmp_path / rel).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=True, max_depth=6)

    assert result == sorted(result), "递归遍历序须与全局 sorted(Path) 完全一致"
