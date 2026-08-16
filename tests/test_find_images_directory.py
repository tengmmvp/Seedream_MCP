"""find_images_in_directory 的字典序遍历与 limit 提前停止测试。

回归保护：分页浏览在大目录下不能退化为全量收集+排序。limit 必须真正限制返回量，
顺序为文件名字典序（与全局 sorted 前 N 等价），保证跨请求分页连续一致。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, Callable

import pytest

import seedream_mcp.utils.io.io_scan as scan_module
import seedream_mcp.utils.io.io_path as path_utils_module
from seedream_mcp.utils.io.io_scan import cached_find_images_in_directory
from seedream_mcp.utils.io.io_path import find_images_in_directory


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

    防御与 io_storage.FileManager.run_cleanup_policies 同类的符号链接越界风险：find_images_in_directory
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


def test_find_images_collects_unreadable_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """传入收集列表时，无法读取的目录追加至列表，供调用方区分目录不可读与无图片。"""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _raise_permission(path: Any) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(path_utils_module.os, "scandir", _raise_permission)

    collected: list[Path] = []
    result = find_images_in_directory(str(tmp_path), recursive=False, unreadable_dirs=collected)

    assert result == []
    assert collected == [tmp_path.resolve()]


def test_cached_find_images_replays_unreadable_dirs_on_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不可读目录信号随扫描缓存条目存储，缓存命中时回放给收集列表。"""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _raise_permission(path: Any) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(path_utils_module.os, "scandir", _raise_permission)
    scan_module.reset_directory_scan_cache()

    first_collected: list[Path] = []
    first = cached_find_images_in_directory(
        resolved_dir=tmp_path,
        recursive=False,
        max_depth=1,
        format_filter=None,
        scan_limit=10,
        unreadable_dirs=first_collected,
    )
    assert first == []
    assert first_collected == [tmp_path.resolve()]

    # 非递归按 mtime 失效，目录未变更即命中缓存：不可读目录从缓存条目回放
    hit_collected: list[Path] = []
    hit = cached_find_images_in_directory(
        resolved_dir=tmp_path,
        recursive=False,
        max_depth=1,
        format_filter=None,
        scan_limit=10,
        unreadable_dirs=hit_collected,
    )
    assert hit == []
    assert hit_collected == [tmp_path.resolve()]


def test_cached_find_images_recursive_uses_ttl_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """递归扫描用 TTL 缓存：TTL 内命中缓存返回陈旧结果，过期后重扫。

    重扫看到子目录新增图。子目录新增文件不改变顶层目录 mtime，递归无法用 mtime
    失效，改用 TTL：TTL 内复用缓存换取翻页性能（接受短时陈旧），过期后重新
    扫描反映新增。
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    # scan_limit 大于目录图数，确保扫到末尾从而缓存全量
    first = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=True, max_depth=3, format_filter=None, scan_limit=100
    )
    assert [raw.name for raw, _resolved in first] == ["a.png"]
    assert len(scan_module._DIRECTORY_SCAN_CACHE) == 1

    # 向已存在子目录新增第 2 张图；顶层目录 mtime 不变
    (sub / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # TTL 内再次调用命中缓存，返回陈旧的 1 张
    within_ttl = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=True, max_depth=3, format_filter=None, scan_limit=100
    )
    assert [raw.name for raw, _resolved in within_ttl] == ["a.png"]

    # 模拟 TTL 过期：推进 browse_images.time.monotonic 返回值越过 TTL
    ttl = scan_module._DIRECTORY_SCAN_CACHE_TTL_SECONDS
    real_monotonic = scan_module.time.monotonic
    base = real_monotonic()
    monkeypatch.setattr(scan_module.time, "monotonic", lambda: base + ttl + 1)

    expired = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=True, max_depth=3, format_filter=None, scan_limit=100
    )
    assert sorted(raw.name for raw, _resolved in expired) == ["a.png", "b.png"]


def test_cached_find_images_cache_hit_returns_full_list(tmp_path: Path) -> None:
    """缓存命中返回全量列表浅拷贝，不同 scan_limit 的翻页共享同一缓存条目。"""
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    # scan_limit 大于图数，扫到末尾缓存全量 3 张
    first = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=10
    )
    assert len(first) == 3
    assert all(raw.resolve() == resolved for raw, resolved in first)
    assert len(scan_module._DIRECTORY_SCAN_CACHE) == 1

    # 不同 scan_limit（模拟翻页）命中同一缓存，返回全量 3 而非 scan_limit=2 的前缀
    paged = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=2
    )
    assert len(paged) == 3

    # 返回浅拷贝：调用方修改不影响内部缓存
    paged.append((Path("/fake.png"), Path("/fake.png")))
    again = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=10
    )
    assert len(again) == 3


def test_cached_find_images_prefix_expands_on_deeper_page(tmp_path: Path) -> None:
    """大目录深翻页：小 scan_limit 缓存不完整前缀，更大 scan_limit 重扫扩展前缀。

    回看命中不重扫。覆盖 complete=False 前缀增量扩展这一新逻辑：旧实现从不缓存
    不完整列表，深翻页每页重扫；新实现缓存前缀并随 scan_limit 增长扩展，回看与
    同范围重复请求直接命中。
    """
    for i in range(5):
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    def _entry() -> Any:
        return next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))

    # 首页 scan_limit=2：目录有 5 图，返回 2 条，结果数等于 limit 故 complete=False，缓存前缀 2
    cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=2
    )
    entry = _entry()
    assert not entry.complete
    assert len(entry.images) == 2

    # 深页 scan_limit=4：缓存前缀 2 小于 4，重扫并扩展前缀至 4
    cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=4
    )
    entry = _entry()
    assert not entry.complete
    assert len(entry.images) == 4

    # 回看 scan_limit=2：缓存前缀 4 不小于 2，命中返回前缀，不重扫（images 仍为 4）
    back = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=2
    )
    assert len(back) == 4
    assert len(_entry().images) == 4


def test_cached_find_images_complete_skips_rescan(tmp_path: Path) -> None:
    """扫到目录末尾(complete=True)后，任意 scan_limit 均命中全量，不再扫描。"""
    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    def _entry() -> Any:
        return next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))

    # scan_limit=10 远大于目录 2 图，返回 2 条且 complete=True（扫到末尾）
    cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=10
    )
    entry = _entry()
    assert entry.complete
    assert len(entry.images) == 2

    # 更小 scan_limit 命中 complete 缓存，返回全量 2 而非前缀
    hit = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=1
    )
    assert len(hit) == 2


def test_cached_find_images_hot_directory_survives_cache_pressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存命中刷新条目热度，轮询目录数超过上限时热目录不被逐出。

    旧实现为 FIFO 驱逐：热目录先插入即位于链首，轮询超过上限数量的其他目录后热
    目录被逐出，下一次访问退化为全量重扫。LRU 下命中刷新 move_to_end，被逐出的
    是最久未命中的目录。以注入的扫描计数器断言热目录全程只扫一次。
    """
    monkeypatch.setattr(scan_module, "_DIRECTORY_SCAN_CACHE_MAX_ENTRIES", 2)
    scan_module.reset_directory_scan_cache()

    hot = tmp_path / "hot"
    hot.mkdir()
    (hot / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    other_dirs: list[Path] = []
    for i in range(3):
        other = tmp_path / f"d{i}"
        other.mkdir()
        (other / f"{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        other_dirs.append(other)

    scanned_dirs: list[str] = []
    original_scan = scan_module.find_images_in_directory

    def counting_scan(**kwargs: Any) -> list[Path]:
        scanned_dirs.append(kwargs["directory"])
        return original_scan(**kwargs)

    def scan(dir_key: Path) -> None:
        cached_find_images_in_directory(
            resolved_dir=dir_key,
            recursive=False,
            max_depth=1,
            format_filter=None,
            scan_limit=10,
            scanner=counting_scan,
        )

    scan(hot)
    scan(other_dirs[0])
    # 缓存已满 2 条：热目录再次命中须刷新热度，不触发重扫
    scan(hot)
    # 插入第三目录触发驱逐，被逐出的应是最久未命中的 d0 而非热目录
    scan(other_dirs[1])
    # 热目录仍命中缓存，不重扫
    scan(hot)

    assert scanned_dirs.count(str(hot)) == 1, "热目录命中缓存刷新热度后不得被 LRU 逐出"
    assert scanned_dirs == [str(hot), str(other_dirs[0]), str(other_dirs[1])]


def test_find_images_does_not_descend_into_reparse_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NTFS junction 等 reparse point 目录不下降，与 io_storage 清理路径防护对齐。

    junction 的 is_symlink 返回 False，entry.is_dir(follow_symlinks=False) 对其返回 True
    仍会下降，从而进入 junction 目标执行 OS 级 listdir，涉及 SMB 出站认证暴露。find_images
    下降前须经 io_file._is_reparse_point 剔除。用 monkeypatch 让该函数对指定子目录返回
    True，断言该子树不被扫描而真实图片仍正常返回，回归保护此防护不退化。
    """
    junction_dir = tmp_path / "junction_dir"
    junction_dir.mkdir()
    (junction_dir / "inside_junction.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "real.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    real_is_reparse = path_utils_module._is_reparse_point
    monkeypatch.setattr(
        path_utils_module,
        "_is_reparse_point",
        lambda p: real_is_reparse(p) or p.resolve() == junction_dir.resolve(),
    )

    result = find_images_in_directory(str(tmp_path), recursive=True)

    result_names = {p.name for p in result}
    assert "real.png" in result_names
    assert "inside_junction.png" not in result_names


def test_find_images_excludes_reparse_point_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reparse point 文件不列入结果，与目录分支及 io_storage 清理遍历防护口径对称。

    OneDrive 占位 .png、投影 FS 条目等 reparse 文件不是 symlink，entry.is_file(
    follow_symlinks=False) 对其返回 True，仅靠后缀过滤会把它列为参考图，后续读取路径的
    open_no_follow_read 兜底只拦 S_ISLNK 而跟随 reparse 目标。用 monkeypatch 让
    _is_reparse_point 对指定文件返回 True，断言该文件被剔除而真实图片正常返回。
    """
    placeholder = tmp_path / "onedrive_placeholder.png"
    placeholder.write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "real.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    placeholder_resolved = placeholder.resolve()
    real_is_reparse = path_utils_module._is_reparse_point
    monkeypatch.setattr(
        path_utils_module,
        "_is_reparse_point",
        lambda p: real_is_reparse(p) or p.resolve() == placeholder_resolved,
    )

    result_names = {p.name for p in find_images_in_directory(str(tmp_path), recursive=False)}

    assert result_names == {"real.png"}


def test_cached_find_images_prefix_extension_not_clamped_by_cache_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存前缀扩展不得把缓存条目上限施加到实际扫描量。

    回归保护：扩展 scan_limit 曾被 min(单条目列表上限) 截断，超过上限的大目录深翻页
    只扫到上限条数即返回短页，调用方把短页误判为扫完全量并谎报 total_count。缓存
    上限只应决定结果是否写缓存，不应截断扫描本身。以 monkeypatch 缩小条目上限常数
    模拟万张目录，避免真实建万级文件。
    """
    for i in range(7):
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(scan_module, "_DIRECTORY_SCAN_CACHE_MAX_LIST_LEN", 5)
    scan_module.reset_directory_scan_cache()

    def _entry() -> Any:
        return next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))

    # 首页 scan_limit=3：目录有 7 图，缓存不完整前缀 3
    cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=3
    )
    assert not _entry().complete
    assert len(_entry().images) == 3

    # 深页 scan_limit=7 超过条目上限 5：扩展扫描不得被截断到 5，必须返回全部 7 张
    deep = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=7
    )
    assert len(deep) == 7

    # 扫描结果超过条目上限 5，不写缓存；缓存内仍为旧前缀条目且不标记 complete
    entry = _entry()
    assert len(entry.images) == 3
    assert not entry.complete

    # 同一深页请求再次到达：缓存前缀不足，按原始 scan_limit 重扫并返回全量
    deep_again = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=7
    )
    assert len(deep_again) == 7


class _ExplodingEntry:
    """is_file 抛 OSError 的目录条目，驱动扫描循环体内的中途异常路径。"""

    def __init__(self, path: str) -> None:
        self.path = path

    def is_file(self, follow_symlinks: bool = True) -> bool:
        raise OSError("transient io error")

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return False


def _patch_scandir_with_exploding_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Any]:
    """把 os.scandir 替换为返回单个 is_file 抛 OSError 条目的扫描器。

    返回被替换前的原 scandir，调用方需要中途恢复时用它做 setattr 定点还原，
    不得调用 monkeypatch.undo，否则会连 autouse fixture 的补丁一并回退。
    """

    original_scandir: Callable[..., Any] = path_utils_module.os.scandir

    def _fake_scandir(path: Any) -> Any:
        del path
        return contextlib.nullcontext(iter([_ExplodingEntry(str(tmp_path / "boom.png"))]))

    monkeypatch.setattr(path_utils_module.os, "scandir", _fake_scandir)
    return original_scandir


def test_find_images_propagates_mid_scan_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描循环体内的 OSError 向上传播，不再吞掉后返回部分结果。

    旧实现的外层 catch 收窄前，中途 IO 错误被记日志后返回已收集的部分列表，调用方
    无法区分「扫完」与「中途出错」。
    """
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _patch_scandir_with_exploding_entry(tmp_path, monkeypatch)

    with pytest.raises(OSError, match="transient io error"):
        find_images_in_directory(str(tmp_path), recursive=False)


def test_cached_find_images_mid_scan_error_not_cached_as_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描中途异常向上传播且不写缓存条目，部分列表不得被冻结为 complete。

    调用链：cached_find_images_in_directory 的 complete 按「返回量小于 scan_limit」
    判定，若扫描器吞掉中途异常返回部分列表，缓存层会把短列表整条缓存为 complete，
    目录后半部分在缓存有效期内不可见。异常传播使缓存写入不可达，杜绝该冻结。
    """
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    original_scandir = _patch_scandir_with_exploding_entry(tmp_path, monkeypatch)
    scan_module.reset_directory_scan_cache()

    with pytest.raises(OSError, match="transient io error"):
        cached_find_images_in_directory(
            resolved_dir=tmp_path,
            recursive=False,
            max_depth=1,
            format_filter=None,
            scan_limit=10,
        )

    assert scan_module._DIRECTORY_SCAN_CACHE == {}

    monkeypatch.setattr(path_utils_module.os, "scandir", original_scandir)
    # 瞬时错误恢复后重扫可得完整结果，证明错误未被固化为缓存
    recovered = cached_find_images_in_directory(
        resolved_dir=tmp_path, recursive=False, max_depth=1, format_filter=None, scan_limit=10
    )
    assert [raw.name for raw, _resolved in recovered] == ["a.png"]
