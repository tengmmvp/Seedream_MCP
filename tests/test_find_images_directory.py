"""find_images_in_directory 的字典序遍历与 limit 提前停止测试。

回归保护：分页浏览在大目录下不能退化为全量收集+排序。limit 必须真正限制返回量，
顺序为文件名字典序、与全局 sorted 前 N 等价，保证跨请求分页连续一致。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import pytest

import seedream_mcp.utils.io.io_scan as scan_module
from _log_fakes import capture_loguru_messages
from seedream_mcp.utils.io.io_file import has_reparse_attribute
from seedream_mcp.utils.io.io_scan import cached_find_images_in_directory, find_images_in_directory


def _scan(
    resolved_dir: Path,
    *,
    recursive: bool,
    max_depth: int,
    scan_limit: int,
    unreadable_dirs: list[Path] | None = None,
    truncated_dirs: list[Path] | None = None,
    scanner: Callable[..., Any] | None = None,
) -> list[tuple[Path, Path]]:
    """cached_find_images_in_directory 的统一调用形态。

    收敛各用例逐字重复的固定 kwargs；format_filter 恒为 None，仅测试关心的
    差异项以参数显式表达。
    """
    kwargs: dict[str, Any] = {
        "resolved_dir": resolved_dir,
        "recursive": recursive,
        "max_depth": max_depth,
        "format_filter": None,
        "scan_limit": scan_limit,
    }
    if unreadable_dirs is not None:
        kwargs["unreadable_dirs"] = unreadable_dirs
    if truncated_dirs is not None:
        kwargs["truncated_dirs"] = truncated_dirs
    if scanner is not None:
        kwargs["scanner"] = scanner
    return cached_find_images_in_directory(**kwargs)


def _advance_past_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 io_scan 的 time.monotonic 推进到 TTL 之外，模拟缓存过期。"""
    ttl = scan_module._DIRECTORY_SCAN_CACHE_TTL_SECONDS
    base = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: base + ttl + 1)


def test_limit_returns_sorted_prefix_not_creation_order(tmp_path: Path) -> None:
    """limit 返回字典序前缀而非创建顺序。"""
    # 打乱创建顺序，确保结果不是碰巧按创建序
    for i in [5, 0, 9, 2, 7, 1, 8, 3, 6, 4]:
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False, limit=3)

    assert [p.name for p in result] == ["img_00.png", "img_01.png", "img_02.png"]


def test_limit_one_returns_first_sorted(tmp_path: Path) -> None:
    """limit=1 返回字典序首张。"""
    for name in ("c.png", "a.png", "b.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False, limit=1)

    assert [p.name for p in result] == ["a.png"]


def test_no_limit_returns_all_sorted(tmp_path: Path) -> None:
    """无 limit 返回全量并按字典序排序。"""
    for name in ("c.png", "a.png", "b.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False)

    assert [p.name for p in result] == ["a.png", "b.png", "c.png"]


def test_recursive_limit_one_skips_later_subtrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """递归 limit=1 取到字典序首张即停止，不扫描后续子树，证明 limit 真正限制扫描量。"""
    sub_a = tmp_path / "a"
    sub_b = tmp_path / "b"
    sub_a.mkdir()
    sub_b.mkdir()
    (sub_a / "a1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (sub_b / "b1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    scanned: list[str] = []
    original_scandir = os.scandir

    def _spy(path: Any) -> Any:
        scanned.append(str(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", _spy)

    result = find_images_in_directory(str(tmp_path), recursive=True, max_depth=3, limit=1)

    assert [p.name for p in result] == ["a1.png"]
    assert str(sub_b) not in scanned, "limit=1 应在取到首张后停止，不应扫描 b/ 子树"


def test_limit_respects_extension_filter(tmp_path: Path) -> None:
    """limit 只统计图片文件，非图片扩展名不占限额。"""
    # 非图片文件参与字典序占位但不进入结果
    (tmp_path / "0_readme.txt").write_bytes(b"x")
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False, limit=1)

    assert [p.name for p in result] == ["a.png"]


def test_sort_matches_path_semantics(tmp_path: Path) -> None:
    """遍历序与 sorted(Path) 逐位一致。"""
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
    """limit<=0 返回数量上限为 0，直接返回空列表且不触发目录扫描。"""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    scanned: list[str] = []
    original_scandir = os.scandir

    def _spy(path: Any) -> Any:
        scanned.append(str(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", _spy)

    assert find_images_in_directory(str(tmp_path), recursive=False, limit=0) == []
    assert find_images_in_directory(str(tmp_path), recursive=False, limit=-3) == []
    assert scanned == [], "limit<=0 应直接返回空，不触发扫描"


def test_recursive_order_matches_global_sorted_path(tmp_path: Path) -> None:
    """递归深度优先加同级 normcase 排序与全局 sorted(Path) 等价，是分页跨请求
    顺序连续一致的前提。"""
    # 前缀目录名 a/a1/a10 与多层级 a/b 混排，空目录仅作前缀占位不产出条目。
    # Windows 大小写不敏感，不能同时建 b/ 与 B/ 目录，大小写排序由单层用例覆盖。
    (tmp_path / "a" / "b").mkdir(parents=True)
    for name in ("a1", "a10", "sub1", "sub10"):
        (tmp_path / name).mkdir()
    image_rels = (
        "a/m.png",
        "a/z.jpg",
        "a/b/deep.png",
        "a1/a.png",
        "a10/b.png",
        "a10/c.png",
        "sub1/i.png",
        "sub10/j.png",
    )
    for rel in image_rels:
        (tmp_path / rel).write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=True, max_depth=6)

    expected = sorted(Path(tmp_path, rel) for rel in image_rels)
    assert result == expected, "递归遍历序须与全局 sorted(Path) 完全一致"
    assert len(result) == len(image_rels)


def test_find_images_does_not_descend_into_symlink_dir(tmp_path: Path) -> None:
    """符号链接目录指向 base 之外时，递归扫描不得下降进入该目录遍历外部图片。

    entry.is_dir(follow_symlinks=False) 拒绝下降符号链接目录；误跟随会把 base 外
    图片纳入结果，构成边界逃逸，与 browse_images 的工作区边界保证冲突。
    """
    # 越界目标置于共享 basetemp 之外的独占临时目录，内放一张图片
    outside_dir = Path(tempfile.mkdtemp(prefix="seedream-find-outside-"))
    try:
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
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)

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
    """os.scandir 抛 PermissionError 时该目录被跳过，整体不抛异常、返回空列表。"""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _raise_permission(path: Any) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "scandir", _raise_permission)

    assert find_images_in_directory(str(tmp_path), recursive=False) == []


def test_find_images_collects_unreadable_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """传入收集列表时，无法读取的目录追加至列表，供调用方区分目录不可读与无图片。"""
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def _raise_permission(path: Any) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "scandir", _raise_permission)

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

    monkeypatch.setattr(os, "scandir", _raise_permission)
    scan_module.reset_directory_scan_cache()

    first_collected: list[Path] = []
    first = _scan(
        tmp_path, recursive=False, max_depth=1, scan_limit=10, unreadable_dirs=first_collected
    )
    assert first == []
    assert first_collected == [tmp_path.resolve()]

    # 非递归按 mtime 失效，目录未变更即命中缓存：不可读目录从缓存条目回放
    hit_collected: list[Path] = []
    hit = _scan(
        tmp_path, recursive=False, max_depth=1, scan_limit=10, unreadable_dirs=hit_collected
    )
    assert hit == []
    assert hit_collected == [tmp_path.resolve()]


def test_cached_find_images_recursive_uses_ttl_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """递归扫描用 TTL 缓存：TTL 内命中缓存返回陈旧结果，过期后重扫。

    子目录新增文件不改变顶层目录 mtime，递归无法按 mtime 失效，改用 TTL 换取
    翻页性能，过期后重扫反映新增。
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    # scan_limit 大于目录图数，确保扫到末尾从而缓存全量
    first = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=100)
    assert [raw.name for raw, _resolved in first] == ["a.png"]
    assert len(scan_module._DIRECTORY_SCAN_CACHE) == 1

    # 向已存在子目录新增第 2 张图；顶层目录 mtime 不变
    (sub / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # TTL 内再次调用命中缓存，返回陈旧的 1 张
    within_ttl = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=100)
    assert [raw.name for raw, _resolved in within_ttl] == ["a.png"]

    _advance_past_ttl(monkeypatch)

    expired = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=100)
    assert sorted(raw.name for raw, _resolved in expired) == ["a.png", "b.png"]


def test_cached_find_images_unsettled_entry_ttl_bounds_stale_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未沉淀条目的 mtime 失效叠加 TTL 上界：目录 mtime 未变但超 TTL 后重新扫描。

    捕获时目录 mtime 距墙钟不足 FAT 粒度窗口的条目未沉淀，粗粒度时间戳感知不到
    同名增删，须由 TTL 上界兜底最终重扫。
    """
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    # 构造未沉淀条目：目录 mtime 距捕获时刻不足 2 秒粒度窗口
    mtime_ns = int((time.time() - 1) * 1e9)
    os.utime(tmp_path, ns=(mtime_ns, mtime_ns))
    dir_stat = tmp_path.stat()
    scan_module.reset_directory_scan_cache()

    first = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10)
    assert [raw.name for raw, _resolved in first] == ["a.png"]
    entry = next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))
    assert entry.settled is False

    # 新增图片后把目录时间戳恢复为扫描前的值，模拟粗粒度 mtime 未感知该变更
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    os.utime(tmp_path, ns=(dir_stat.st_atime_ns, dir_stat.st_mtime_ns))

    # mtime 指纹一致且在 TTL 内：命中缓存返回陈旧的 1 张
    within_ttl = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=100)
    assert [raw.name for raw, _resolved in within_ttl] == ["a.png"]

    # 超 TTL 后上界兜底失效，重扫反映新增
    _advance_past_ttl(monkeypatch)

    expired = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=100)
    assert sorted(raw.name for raw, _resolved in expired) == ["a.png", "b.png"]


def test_cached_find_images_settled_entry_survives_ttl_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """沉淀条目 mtime 未变即新鲜：超 TTL 后仍命中缓存，不触发重扫。

    捕获时目录已静止超 FAT 粒度窗口，此后任何变更必然使粗粒度 mtime 前移，mtime
    指纹未变即结果仍准确，常态文件系统不再每 TTL 全量重扫大目录。
    """
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    # 构造沉淀条目：目录 mtime 早于捕获时刻 2 秒粒度窗口以上
    mtime_ns = int((time.time() - 3) * 1e9)
    os.utime(tmp_path, ns=(mtime_ns, mtime_ns))
    scan_module.reset_directory_scan_cache()

    scanned_dirs: list[str] = []
    original_scan = find_images_in_directory

    def counting_scan(**kwargs: Any) -> list[Path]:
        scanned_dirs.append(kwargs["directory"])
        return original_scan(**kwargs)

    first = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10, scanner=counting_scan)
    assert [raw.name for raw, _resolved in first] == ["a.png"]
    entry = next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))
    assert entry.settled is True

    # mtime 未变的沉淀条目超 TTL 后仍命中，不触发重扫
    _advance_past_ttl(monkeypatch)

    hit = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=100, scanner=counting_scan)
    assert [raw.name for raw, _resolved in hit] == ["a.png"]
    assert scanned_dirs == [str(tmp_path)], "沉淀条目 mtime 未变时超 TTL 不得重扫"


def test_cached_find_images_cache_hit_returns_full_list(tmp_path: Path) -> None:
    """缓存命中返回全量列表浅拷贝，不同 scan_limit 的翻页共享同一缓存条目。"""
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    # scan_limit 大于图数，扫到末尾缓存全量 3 张
    first = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10)
    assert len(first) == 3
    assert all(raw.resolve() == resolved for raw, resolved in first)
    assert len(scan_module._DIRECTORY_SCAN_CACHE) == 1

    # 不同 scan_limit 模拟翻页，命中同一缓存，返回全量 3 而非 scan_limit=2 的前缀
    paged = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=2)
    assert len(paged) == 3

    # 返回浅拷贝：调用方修改不影响内部缓存
    paged.append((Path("/fake.png"), Path("/fake.png")))
    again = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10)
    assert len(again) == 3


def test_cached_find_images_prefix_expands_on_deeper_page(tmp_path: Path) -> None:
    """大目录深翻页：小 scan_limit 缓存不完整前缀，更大 scan_limit 重扫扩展前缀。

    前缀随 scan_limit 增长扩展，回看与同范围重复请求直接命中缓存不重扫。
    """
    for i in range(5):
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    def _entry() -> Any:
        return next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))

    # 首页 scan_limit=2：目录有 5 图，返回 2 条，结果数等于 limit 故 complete=False，缓存前缀 2
    _scan(tmp_path, recursive=False, max_depth=1, scan_limit=2)
    entry = _entry()
    assert not entry.complete
    assert len(entry.images) == 2

    # 深页 scan_limit=4：缓存前缀 2 小于 4，重扫并扩展前缀至 4
    _scan(tmp_path, recursive=False, max_depth=1, scan_limit=4)
    entry = _entry()
    assert not entry.complete
    assert len(entry.images) == 4

    # 回看 scan_limit=2：缓存前缀 4 不小于 2，命中返回前缀，不重扫，images 保持 4
    back = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=2)
    assert len(back) == 4
    assert len(_entry().images) == 4


def test_cached_find_images_complete_skips_rescan(tmp_path: Path) -> None:
    """扫到目录末尾即 complete=True 后，任意 scan_limit 均命中全量，不再扫描。"""
    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    def _entry() -> Any:
        return next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))

    # scan_limit=10 远大于目录 2 图，返回 2 条且扫到末尾 complete=True
    _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10)
    entry = _entry()
    assert entry.complete
    assert len(entry.images) == 2

    # 更小 scan_limit 命中 complete 缓存，返回全量 2 而非前缀
    hit = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=1)
    assert len(hit) == 2


def test_cached_find_images_hot_directory_survives_cache_pressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存命中刷新条目热度，轮询目录数超过上限时热目录不被逐出。

    FIFO 驱逐下热目录先插入即位于链首，轮询超上限后被逐出退化为重扫；LRU 命中
    刷新 move_to_end，被逐出的是最久未命中的目录。
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
    original_scan = find_images_in_directory

    def counting_scan(**kwargs: Any) -> list[Path]:
        scanned_dirs.append(kwargs["directory"])
        return original_scan(**kwargs)

    def scan(dir_key: Path) -> None:
        _scan(dir_key, recursive=False, max_depth=1, scan_limit=10, scanner=counting_scan)

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


def test_cached_find_images_ttl_rescan_overwrite_refreshes_lru_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTL 过期重扫覆写既有键时刷新 LRU 位，重扫后的热条目不得滞留旧位置被逐出。

    OrderedDict 对既有键赋值不移动位置：递归扫描靠 TTL 失效，过期重扫走覆写
    路径且不经过命中刷新，覆写不刷新热度时最常访问的目录被优先逐出。
    """
    monkeypatch.setattr(scan_module, "_DIRECTORY_SCAN_CACHE_MAX_ENTRIES", 2)
    scan_module.reset_directory_scan_cache()

    hot = tmp_path / "hot"
    hot.mkdir()
    (hot / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    other_dirs: list[Path] = []
    for i in range(2):
        other = tmp_path / f"d{i}"
        other.mkdir()
        (other / f"{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        other_dirs.append(other)

    scanned_dirs: list[str] = []
    original_scan = find_images_in_directory

    def counting_scan(**kwargs: Any) -> list[Path]:
        scanned_dirs.append(kwargs["directory"])
        return original_scan(**kwargs)

    def scan(dir_key: Path) -> None:
        _scan(dir_key, recursive=True, max_depth=1, scan_limit=10, scanner=counting_scan)

    scan(hot)
    scan(other_dirs[0])

    # 递归条目按 TTL 失效，对 hot 的重扫走覆写路径
    _advance_past_ttl(monkeypatch)

    # 过期重扫覆写 hot：正确行为下覆写刷新 LRU 位，hot 成为最近使用
    scan(hot)
    # 缓存已满 2 条，插入 d1 触发驱逐，被逐出的应是最久未用的 d0 而非刚重扫的 hot
    scan(other_dirs[1])
    # TTL 内命中缓存，不再重扫
    scan(hot)

    assert (
        scanned_dirs.count(str(hot)) == 2
    ), "TTL 过期重扫覆写后热条目须刷新 LRU 位，不得被优先逐出"
    assert scanned_dirs == [str(hot), str(other_dirs[0]), str(hot), str(other_dirs[1])]


def test_find_images_stops_at_scan_entry_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单次调用遍历条目总数（含非图片）超预算即停止遍历并记录告警。

    统计口径覆盖非图片条目：8 个图片条目对 5 的预算即触发，本批丢弃返回空列表。
    """
    for i in range(8):
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 5)

    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        result = find_images_in_directory(str(tmp_path), recursive=False)

    assert result == []
    assert any("目录扫描条目数超过预算" in message for message in warnings)


def test_find_images_budget_counts_non_image_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非图片条目同样计入预算，仅图片条目不触发封顶。"""
    for i in range(8):
        (tmp_path / f"note_{i:02d}.txt").write_bytes(b"x")
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 5)

    assert find_images_in_directory(str(tmp_path), recursive=False) == []


def test_find_images_prefix_rescan_budget_does_not_double_count(tmp_path: Path) -> None:
    """前缀倍增重扫的条目预算只计每趟新增，9999 非图片 + 2 图片不被预算误杀。

    旧口径按趟累计全目录条目：第一趟计 10001、重扫趟再计 10001，累计 20002 超过
    20000 预算，整批被丢弃返回 0 张图；按新增条目摊销后 10001 条目录不超预算，
    limit=21 经倍增前缀扫到目录末尾取回全部 2 张图片。
    """
    for i in range(9999):
        (tmp_path / f"a_{i:04d}.txt").write_bytes(b"x")
    (tmp_path / "z_0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "z_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = find_images_in_directory(str(tmp_path), recursive=False, limit=21)

    assert [p.name for p in result] == ["z_0.png", "z_1.png"]


def test_find_images_records_truncated_dir_on_budget_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预算截断时截断目录追加至收集列表，供调用方区分「扫完全量」与「部分结果」。"""
    for i in range(8):
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 5)

    truncated: list[Path] = []
    result = find_images_in_directory(
        str(tmp_path), recursive=False, unreadable_dirs=[], truncated_dirs=truncated
    )

    assert result == []
    assert truncated == [tmp_path.resolve()]


def test_cached_find_images_truncated_scan_not_cached_as_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """条目预算截断的扫描不按 complete 缓存，预算恢复后后续扫描可重新尝试。

    截断结果被误标 complete 后，更大 scan_limit 的后续扫描会命中缓存短路返回
    空列表，目录内真实图片在缓存有效期内不可见。
    """
    for i in range(8):
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 5)
    scan_module.reset_directory_scan_cache()

    truncated: list[Path] = []
    first = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10, truncated_dirs=truncated)
    assert first == []
    assert truncated == [tmp_path.resolve()]
    entry = next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))
    assert entry.complete is False
    assert entry.truncated_dirs == [tmp_path.resolve()]

    # 预算恢复后同目录扫描不命中 complete 短路，重扫取回全量
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 100)
    recovered = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10)
    assert len(recovered) == 8


def test_cached_find_images_replays_truncation_signal_on_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """截断条目的缓存命中回放截断目录，调用方对部分前缀仍感知结果不完整。

    子目录 a 先于截断点完成产出 1 张稳定前缀图，子目录 b 触发预算截断；缓存
    前缀不少于更小 scan_limit 时直接命中，截断信号须随命中回放。
    """
    sub_a = tmp_path / "a"
    sub_a.mkdir()
    (sub_a / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    sub_b = tmp_path / "b"
    sub_b.mkdir()
    for i in range(20):
        (sub_b / f"note_{i:02d}.txt").write_bytes(b"x")
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 15)
    scan_module.reset_directory_scan_cache()

    first_truncated: list[Path] = []
    first = _scan(
        tmp_path, recursive=True, max_depth=3, scan_limit=10, truncated_dirs=first_truncated
    )
    assert [raw.name for raw, _resolved in first] == ["x.png"]
    assert first_truncated == [sub_b.resolve()]

    hit_truncated: list[Path] = []
    hit = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=1, truncated_dirs=hit_truncated)
    assert [raw.name for raw, _resolved in hit] == ["x.png"]
    assert hit_truncated == [sub_b.resolve()]


def test_cached_find_images_isolates_injected_scanners(tmp_path: Path) -> None:
    """注入不同 scanner 的扫描互不命中对方缓存条目。

    键并入 scanner 身份前，后到的 scanner 会命中先到者的缓存而不再被调用，替身
    扫描结果串染调用方。
    """
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    scan_module.reset_directory_scan_cache()

    scanned_by: list[str] = []
    original_scan = find_images_in_directory

    def scanner_one(**kwargs: Any) -> list[Path]:
        scanned_by.append("one")
        return original_scan(**kwargs)

    def scanner_two(**kwargs: Any) -> list[Path]:
        scanned_by.append("two")
        return original_scan(**kwargs)

    _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10, scanner=scanner_one)
    _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10, scanner=scanner_two)

    assert scanned_by == ["one", "two"]


def test_cached_find_images_unreadable_signal_not_duplicated_across_rounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一收集列表跨扫描与缓存回放多轮传递时不可读目录去重，不重复累计。

    scan_limit=1 使首条目未扫完，第二轮更大 scan_limit 触发重扫、第三轮命中
    回放，两条路径各自会把不可读目录再度交给收集列表。
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    original_scandir = os.scandir

    def _raise_for_sub(path: Any) -> Any:
        if Path(path).name == "sub":
            raise PermissionError("denied")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", _raise_for_sub)
    scan_module.reset_directory_scan_cache()

    unreadable: list[Path] = []
    first = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=1, unreadable_dirs=unreadable)
    rescan = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=5, unreadable_dirs=unreadable)
    replay = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=1, unreadable_dirs=unreadable)

    assert [raw.name for raw, _resolved in first] == ["a.png"]
    assert rescan == first
    assert replay == first
    assert unreadable == [sub.resolve()]


def test_cached_find_images_truncation_signal_not_duplicated_across_rounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """截断信号在缓存命中的回放轮间去重合并，同列表恒无重复条目。"""
    sub_a = tmp_path / "a"
    sub_a.mkdir()
    (sub_a / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    sub_b = tmp_path / "b"
    sub_b.mkdir()
    for i in range(20):
        (sub_b / f"note_{i:02d}.txt").write_bytes(b"x")
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 15)
    scan_module.reset_directory_scan_cache()

    truncated: list[Path] = []
    first = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=10, truncated_dirs=truncated)
    hit = _scan(tmp_path, recursive=True, max_depth=3, scan_limit=1, truncated_dirs=truncated)

    assert [raw.name for raw, _resolved in first] == ["x.png"]
    assert [raw.name for raw, _resolved in hit] == ["x.png"]
    assert truncated == [sub_b.resolve()]


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="目录分支的 reparse 剔除带 win32 短路，POSIX 上不经过替身判定",
)
def test_find_images_does_not_descend_into_reparse_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NTFS junction 等 reparse point 目录不下降，与 io_storage 清理路径防护对齐。

    junction 的 is_symlink 返回 False，is_dir(follow_symlinks=False) 对其仍返回 True
    而下降进入目标执行 OS 级 listdir，涉及 SMB 出站认证暴露；目录分支复用遍历条目
    的 no-follow stat 经 has_reparse_attribute 剔除，替身以唯一 mtime_ns 标记目标。
    """
    junction_dir = tmp_path / "junction_dir"
    junction_dir.mkdir()
    (junction_dir / "inside_junction.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "real.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    marker_ns = 1_600_000_000_000_000_000
    os.utime(junction_dir, ns=(marker_ns, marker_ns))

    real_has_reparse = has_reparse_attribute
    monkeypatch.setattr(
        scan_module,
        "has_reparse_attribute",
        lambda st: real_has_reparse(st) or st.st_mtime_ns == marker_ns,
    )

    result = find_images_in_directory(str(tmp_path), recursive=True)

    result_names = {p.name for p in result}
    assert "real.png" in result_names
    assert "inside_junction.png" not in result_names


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="文件分支的 reparse 剔除带 win32 短路，POSIX 上不经过替身判定",
)
def test_find_images_excludes_reparse_point_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reparse point 文件不列入结果，与目录分支及 io_storage 清理遍历防护口径对称。

    OneDrive 占位 .png 等 reparse 文件不是 symlink，is_file(follow_symlinks=False)
    对其仍返回 True，仅靠后缀过滤会列为参考图而读取时跟随 reparse 目标。文件与
    目录两分支均先判 win32 平台以省 POSIX 上的无效 stat，替身仅在 Windows 生效，
    非 Windows 平台跳过本用例。
    """
    placeholder = tmp_path / "onedrive_placeholder.png"
    # 占位文件取独有长度：Windows 的 DirEntry.stat 不携带 st_ino，替身按 st_size
    # 标记占位文件为 reparse。
    placeholder_bytes = b"\x89PNG\r\n\x1a\n" + b"placeholder" * 8
    placeholder.write_bytes(placeholder_bytes)
    (tmp_path / "real.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    real_has_reparse = has_reparse_attribute
    monkeypatch.setattr(
        scan_module,
        "has_reparse_attribute",
        lambda st: real_has_reparse(st) or st.st_size == len(placeholder_bytes),
    )

    result_names = {p.name for p in find_images_in_directory(str(tmp_path), recursive=False)}

    assert result_names == {"real.png"}


def test_cached_find_images_prefix_extension_not_clamped_by_cache_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存前缀扩展不得把缓存条目上限施加到实际扫描量。

    扩展 scan_limit 曾被单条目列表上限截断而返回短页，调用方误判扫完全量。上限只
    决定是否写缓存，不截断扫描本身。以 monkeypatch 缩小上限常数模拟万张目录。
    """
    for i in range(7):
        (tmp_path / f"img_{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(scan_module, "_DIRECTORY_SCAN_CACHE_MAX_LIST_LEN", 5)
    scan_module.reset_directory_scan_cache()

    def _entry() -> Any:
        return next(iter(scan_module._DIRECTORY_SCAN_CACHE.values()))

    # 首页 scan_limit=3：目录有 7 图，缓存不完整前缀 3
    _scan(tmp_path, recursive=False, max_depth=1, scan_limit=3)
    assert not _entry().complete
    assert len(_entry().images) == 3

    # 深页 scan_limit=7 超过条目上限 5：扩展扫描不得被截断到 5，必须返回全部 7 张
    deep = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=7)
    assert len(deep) == 7

    # 扫描结果超过条目上限 5，不写缓存；缓存内仍为旧前缀条目且不标记 complete
    entry = _entry()
    assert len(entry.images) == 3
    assert not entry.complete

    # 同一深页请求再次到达：缓存前缀不足，按原始 scan_limit 重扫并返回全量
    deep_again = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=7)
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

    original_scandir: Callable[..., Any] = os.scandir

    def _fake_scandir(path: Any) -> Any:
        del path
        return contextlib.nullcontext(iter([_ExplodingEntry(str(tmp_path / "boom.png"))]))

    monkeypatch.setattr(os, "scandir", _fake_scandir)
    return original_scandir


def test_find_images_propagates_mid_scan_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描循环体内的 OSError 向上传播，不再吞掉后返回部分结果。

    旧实现吞掉中途 IO 错误返回部分列表，调用方无法区分「扫完」与「中途出错」。
    """
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _patch_scandir_with_exploding_entry(tmp_path, monkeypatch)

    with pytest.raises(OSError, match="transient io error"):
        find_images_in_directory(str(tmp_path), recursive=False)


def test_cached_find_images_mid_scan_error_not_cached_as_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描中途异常向上传播且不写缓存条目，部分列表不得被冻结为 complete。

    complete 按「返回量小于 scan_limit」判定，扫描器吞错返回短列表会被整条缓存
    为 complete，目录后半部分在缓存有效期内不可见。
    """
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    original_scandir = _patch_scandir_with_exploding_entry(tmp_path, monkeypatch)
    scan_module.reset_directory_scan_cache()

    with pytest.raises(OSError, match="transient io error"):
        _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10)

    assert scan_module._DIRECTORY_SCAN_CACHE == {}

    monkeypatch.setattr(os, "scandir", original_scandir)
    # 瞬时错误恢复后重扫可得完整结果，证明错误未被固化为缓存
    recovered = _scan(tmp_path, recursive=False, max_depth=1, scan_limit=10)
    assert [raw.name for raw, _resolved in recovered] == ["a.png"]
