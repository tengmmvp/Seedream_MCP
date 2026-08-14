"""find_images_in_directory 的字典序遍历与 limit 提前停止测试。

回归保护：分页浏览在大目录下不能退化为全量收集+排序。limit 必须真正限制返回量，
顺序为文件名字典序（与全局 sorted 前 N 等价），保证跨请求分页连续一致。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import seedream_mcp.tools.impl.browse_images as browse_module
import seedream_mcp.utils.path_utils as path_utils_module
from seedream_mcp.tools.impl.browse_images import _cached_find_images_in_directory
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


def test_find_images_does_not_descend_into_symlink_dir(tmp_path: Path) -> None:
    """符号链接目录指向 base 之外时，递归扫描不得下降进入该目录遍历外部图片。

    防御与 file_manager.cleanup_old_files 同类的符号链接越界风险：find_images_in_directory
    使用 os.scandir 配合 entry.is_dir(follow_symlinks=False) 拒绝下降符号链接目录。若错误地
    跟随符号链接目录下降，会把 base 之外的外部图片纳入结果，构成路径边界逃逸，与 browse_images
    的工作区边界保证冲突。构造 base 内的符号链接目录指向 base 外的目录（含一张图片），并另放
    一张 base 内真实图片，断言返回结果含真实图片但不含经由符号链接目录下降到的外部图片。
    """
    # base 之外的外部目录放置一张图片
    outside_dir = tmp_path.parent / "outside_find_symlink_target"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # base 内放一张真实图片，证明扫描确实执行而非整体被跳过
    (tmp_path / "inside.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # base 内创建指向外部目录的符号链接目录
    link_dir = tmp_path / "link_dir"
    try:
        os.symlink(str(outside_dir), str(link_dir), target_is_directory=True)
    except (OSError, AttributeError):
        pytest.skip("当前进程无法创建符号链接（Windows 可能需要开发者模式或管理员）")

    result = find_images_in_directory(str(tmp_path), recursive=True)

    result_names = {p.name for p in result}
    # 真实图片正常返回，证明扫描确实执行
    assert "inside.png" in result_names
    # 经由符号链接目录下降到的外部图片不得纳入结果
    assert "outside.png" not in result_names
    # 所有结果必须落在 base 目录内，不得经由符号链接逃逸到外部
    base_resolved = tmp_path.resolve()
    for image_path in result:
        assert base_resolved in image_path.resolve().parents


def test_extensions_parameter_filters_to_given_set(tmp_path: Path) -> None:
    """显式 extensions 仅返回匹配扩展名的文件，其余图片扩展名被排除。"""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "b.jpg").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "c.txt").write_bytes(b"x")

    result = find_images_in_directory(str(tmp_path), recursive=False, extensions=[".jpg"])

    assert [p.name for p in result] == ["b.jpg"]


def test_find_images_returns_empty_when_directory_missing(tmp_path: Path) -> None:
    """目录不存在时返回空列表而非抛出异常。"""
    missing = tmp_path / "does_not_exist"
    assert find_images_in_directory(str(missing), recursive=False) == []


def test_find_images_swallows_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.scandir 抛 PermissionError 时扫描该目录返回 False，整体不抛异常、返回空。"""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _raise_permission(path: Any) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(path_utils_module.os, "scandir", _raise_permission)

    assert find_images_in_directory(str(tmp_path), recursive=False) == []


def test_cached_find_images_recursive_uses_ttl_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """递归扫描用 TTL 缓存：TTL 内命中缓存返回陈旧结果，TTL 过期后重扫看到子目录新增图。

    子目录新增文件不改变顶层目录 mtime，递归无法用 mtime 失效，改用 TTL：TTL 内复用缓存
    换取翻页性能（接受短时陈旧），过期后重新扫描反映新增。
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    browse_module._DIRECTORY_SCAN_CACHE.clear()

    # scan_limit 大于目录图数，确保扫到末尾从而缓存全量
    first = _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=True, max_depth=3, format_filter=None, scan_limit=100
    )
    assert [p.name for p in first] == ["a.png"]
    assert len(browse_module._DIRECTORY_SCAN_CACHE) == 1

    # 向已存在子目录新增第 2 张图；顶层目录 mtime 不变
    (sub / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # TTL 内再次调用命中缓存，返回陈旧的 1 张
    within_ttl = _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=True, max_depth=3, format_filter=None, scan_limit=100
    )
    assert [p.name for p in within_ttl] == ["a.png"]

    # 模拟 TTL 过期：推进 browse_images.time.monotonic 返回值越过 TTL
    ttl = browse_module._DIRECTORY_SCAN_CACHE_TTL_SECONDS
    real_monotonic = browse_module.time.monotonic
    base = real_monotonic()
    monkeypatch.setattr(browse_module.time, "monotonic", lambda: base + ttl + 1)

    expired = _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=True, max_depth=3, format_filter=None, scan_limit=100
    )
    assert sorted(p.name for p in expired) == ["a.png", "b.png"]


def test_cached_find_images_cache_hit_returns_full_list(tmp_path: Path) -> None:
    """缓存命中返回全量列表浅拷贝，不同 scan_limit 的翻页共享同一缓存条目。"""
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    browse_module._DIRECTORY_SCAN_CACHE.clear()

    # scan_limit 大于图数，扫到末尾缓存全量 3 张
    first = _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=10
    )
    assert len(first) == 3
    assert len(browse_module._DIRECTORY_SCAN_CACHE) == 1

    # 不同 scan_limit（模拟翻页）命中同一缓存，返回全量 3 而非 scan_limit=2 的前缀
    paged = _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=2
    )
    assert len(paged) == 3

    # 返回浅拷贝：调用方修改不影响内部缓存
    paged.append(Path("/fake.png"))
    again = _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=10
    )
    assert len(again) == 3


def test_cached_find_images_prefix_expands_on_deeper_page(tmp_path: Path) -> None:
    """大目录深翻页：小 scan_limit 缓存不完整前缀，更大 scan_limit 重扫扩展前缀，回看命中不重扫。

    覆盖 complete=False 前缀增量扩展这一新逻辑：旧实现从不缓存不完整列表，深翻页每页重扫；
    新实现缓存前缀并随 scan_limit 增长扩展，回看与同范围重复请求直接命中。
    """
    for i in range(5):
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    browse_module._DIRECTORY_SCAN_CACHE.clear()

    def _entry() -> Any:
        return next(iter(browse_module._DIRECTORY_SCAN_CACHE.values()))

    # 首页 scan_limit=2：目录有 5 图，返回 2 条，结果数等于 limit 故 complete=False，缓存前缀 2
    _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=2
    )
    entry = _entry()
    assert not entry.complete
    assert len(entry.images) == 2

    # 深页 scan_limit=4：缓存前缀 2 小于 4，重扫并扩展前缀至 4
    _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=4
    )
    entry = _entry()
    assert not entry.complete
    assert len(entry.images) == 4

    # 回看 scan_limit=2：缓存前缀 4 不小于 2，命中返回前缀，不重扫（images 仍为 4）
    back = _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=2
    )
    assert len(back) == 4
    assert len(_entry().images) == 4


def test_cached_find_images_complete_skips_rescan(tmp_path: Path) -> None:
    """扫到目录末尾(complete=True)后，任意 scan_limit 均命中全量，不再扫描。"""
    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    browse_module._DIRECTORY_SCAN_CACHE.clear()

    def _entry() -> Any:
        return next(iter(browse_module._DIRECTORY_SCAN_CACHE.values()))

    # scan_limit=10 远大于目录 2 图，返回 2 条且 complete=True（扫到末尾）
    _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=10
    )
    entry = _entry()
    assert entry.complete
    assert len(entry.images) == 2

    # 更小 scan_limit 命中 complete 缓存，返回全量 2 而非前缀
    hit = _cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=1
    )
    assert len(hit) == 2
