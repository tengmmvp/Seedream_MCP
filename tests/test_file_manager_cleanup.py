"""FileManager 的 validate_path 越界守卫、run_cleanup_policies 清理与 Markdown 引用测试。

清理覆盖按天过期、总量配额驱逐、超龄 .part 清扫与空目录修剪，并锁定符号链接不
跟随、仅删图片扩展名等边界。全部用例在 tmp_path 临时目录构造文件。
"""

import inspect
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from seedream_mcp.utils.io.io_storage import FileManager

# 深树层数上限与 Windows MAX_PATH 预算：每层目录消耗 3 字符以内，预算收缩仍保底 30 层。
_DEEP_TREE_MAX_DEPTH = 100
_DEEP_TREE_MIN_DEPTH = 30
_WINDOWS_PATH_BUDGET = 259


def _deep_tree_depth(base: Path) -> int:
    """树深按 Windows MAX_PATH 剩余预算收缩，POSIX 取固定深度。"""
    if sys.platform != "win32":
        return _DEEP_TREE_MAX_DEPTH
    return max(
        _DEEP_TREE_MIN_DEPTH,
        min(_DEEP_TREE_MAX_DEPTH, (_WINDOWS_PATH_BUDGET - len(str(base))) // 3),
    )


def test_validate_path_accepts_inside_base(tmp_path: Path) -> None:
    """base 内嵌套路径通过校验。"""
    manager = FileManager(base_dir=tmp_path)
    assert manager.validate_path(tmp_path / "nested" / "image.png") is True


def test_validate_path_rejects_outside_base(tmp_path: Path) -> None:
    """base 之外的兄弟路径被拒绝。"""
    manager = FileManager(base_dir=tmp_path)
    outside = tmp_path.parent / "sibling.png"
    assert manager.validate_path(outside) is False


def test_run_cleanup_age_removes_expired_and_keeps_recent(tmp_path: Path) -> None:
    """按天清理删除过期文件，保留未过期文件。"""
    manager = FileManager(base_dir=tmp_path)

    old_file = tmp_path / "old.png"
    old_file.write_bytes(b"old")
    old_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(old_file, (old_time, old_time))

    new_file = tmp_path / "new.png"
    new_file.write_bytes(b"new")

    result = manager.run_cleanup_policies(days=30, max_total_bytes=None)

    assert not old_file.exists()
    assert new_file.exists()
    assert result["deleted_files"] == 1
    assert result["deleted_size"] == 3


def test_run_cleanup_age_accumulates_deleted_size_and_prunes_empty_dirs(
    tmp_path: Path,
) -> None:
    """deleted_size 按字节累计；过期文件清空后变空的子目录须被修剪。"""
    manager = FileManager(base_dir=tmp_path)

    sub = tmp_path / "2024-01-01" / "tool"
    sub.mkdir(parents=True)
    old_file = sub / "old.png"
    old_file.write_bytes(b"x" * 100)
    old_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(old_file, (old_time, old_time))

    result = manager.run_cleanup_policies(days=30, max_total_bytes=None)

    assert result["deleted_files"] == 1
    assert result["deleted_size"] == 100
    assert result["errors"] == []
    # 文件删除后子目录变空，按深度逆序修剪：tool 与 2024-01-01 均被清空删除
    assert not sub.exists()
    assert not sub.parent.exists()
    # base_dir 自身不修剪
    assert tmp_path.exists()


def test_run_cleanup_age_keeps_non_empty_subdir(tmp_path: Path) -> None:
    """子目录仍含未过期文件时不得被修剪。"""
    manager = FileManager(base_dir=tmp_path)

    sub = tmp_path / "keep_dir"
    sub.mkdir()
    new_file = sub / "new.png"
    new_file.write_bytes(b"new")  # 未过期

    manager.run_cleanup_policies(days=30, max_total_bytes=None)

    assert new_file.exists()
    assert sub.exists()


def test_run_cleanup_age_skips_symlink_pointing_outside(tmp_path: Path) -> None:
    """符号链接指向 base_dir 之外时，清理不得删除其目标，防止越权删除。

    创建能力经函数内 os.symlink 的 OSError 运行时探测跳过，开发者模式下的
    Windows 也可执行。
    """
    manager = FileManager(base_dir=tmp_path)

    # 越界目标置于共享 basetemp 之外的独占临时目录
    outside_dir = Path(tempfile.mkdtemp(prefix="seedream-cleanup-outside-"))
    try:
        target = outside_dir / "target.png"
        target.write_bytes(b"target")
        old_time = (datetime.now() - timedelta(days=40)).timestamp()
        os.utime(target, (old_time, old_time))

        link = tmp_path / "link.png"
        try:
            os.symlink(target, link)
        except OSError:
            pytest.skip("当前环境不支持创建符号链接")
        manager.run_cleanup_policies(days=30, max_total_bytes=None)

        # 符号链接自身可能被跳过；但其指向的外部目标必须不被删除
        assert target.exists()
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


def test_run_cleanup_age_does_not_descend_into_symlink_dir(tmp_path: Path) -> None:
    """符号链接目录指向 base_dir 之外时，清理不得下降进入该目录遍历外部条目。

    误跟随会把外部过期文件删除，Windows 下还可能触发 SMB 出站认证。
    """
    manager = FileManager(base_dir=tmp_path)

    outside_dir = Path(tempfile.mkdtemp(prefix="seedream-cleanup-outside-"))
    try:
        marker = outside_dir / "marker.png"
        marker.write_bytes(b"marker-content")
        old_time = (datetime.now() - timedelta(days=40)).timestamp()
        os.utime(marker, (old_time, old_time))

        # base_dir 内创建指向外部目录的符号链接目录
        link_dir = tmp_path / "link_dir"
        try:
            os.symlink(str(outside_dir), str(link_dir), target_is_directory=True)
        except (OSError, AttributeError):
            pytest.skip("当前进程无法创建符号链接（Windows 可能需要开发者模式或管理员）")

        result = manager.run_cleanup_policies(days=30, max_total_bytes=None)

        # marker 已过期，若清理下降进入符号链接目录则会被删除；
        # 其仍存在即证明清理未对外部条目下降遍历
        assert marker.exists()
        assert marker.read_bytes() == b"marker-content"
        assert result["deleted_files"] == 0
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


def test_run_cleanup_quota_evicts_oldest_until_under_limit(tmp_path: Path) -> None:
    """总量超限时按 mtime 升序驱逐最旧文件直至总量达标，保留较新文件。"""
    manager = FileManager(base_dir=tmp_path)

    now = datetime.now()
    # 三个文件各 100 字节，总量 300；上限 150 须驱逐最旧两个共 200 字节方达标
    files: list[Path] = []
    for i, age_days in enumerate([10, 5, 1]):
        f = tmp_path / f"img_{i}.png"
        f.write_bytes(b"x" * 100)
        t = (now - timedelta(days=age_days)).timestamp()
        os.utime(f, (t, t))
        files.append(f)

    # days=0 跳过按天清理，仅执行配额驱逐
    result = manager.run_cleanup_policies(days=0, max_total_bytes=150)

    # 最旧两个被驱逐，最新一个保留
    assert result["deleted_files"] == 2
    assert result["deleted_size"] == 200
    assert not files[0].exists()
    assert not files[1].exists()
    assert files[2].exists()


def test_run_cleanup_quota_noop_when_under_limit(tmp_path: Path) -> None:
    """总量未超上限时不删除任何文件。"""
    manager = FileManager(base_dir=tmp_path)
    f = tmp_path / "img.png"
    f.write_bytes(b"x" * 100)

    result = manager.run_cleanup_policies(days=0, max_total_bytes=200)

    assert result["deleted_files"] == 0
    assert result["deleted_size"] == 0
    assert f.exists()


def test_run_cleanup_quota_counts_zero_byte_files_in_candidate_window(
    tmp_path: Path,
) -> None:
    """最旧候选为 0 字节文件时窗口计入其数量：与次旧非零文件一并驱逐方达配额。"""
    manager = FileManager(base_dir=tmp_path)

    now = datetime.now()
    zero = tmp_path / "zero.png"
    zero.write_bytes(b"")
    zero_t = (now - timedelta(days=10)).timestamp()
    os.utime(zero, (zero_t, zero_t))
    middle = tmp_path / "middle.png"
    middle.write_bytes(b"x" * 100)
    middle_t = (now - timedelta(days=5)).timestamp()
    os.utime(middle, (middle_t, middle_t))
    newest = tmp_path / "newest.png"
    newest.write_bytes(b"x" * 100)

    # 总量 200 超上限 199：0 字节项不减少总量，窗口不含其数量时会提前耗尽而配额未达
    result = manager.run_cleanup_policies(days=0, max_total_bytes=199)

    assert result["deleted_files"] == 2
    assert result["deleted_size"] == 100
    assert result["errors"] == []
    assert not zero.exists()
    assert not middle.exists()
    assert newest.exists()


def test_run_cleanup_policies_runs_age_then_quota_in_single_scan(
    tmp_path: Path,
) -> None:
    """单次扫描依次执行按天清理与总量配额：过期文件先删，剩余超配额再驱逐最旧。"""
    manager = FileManager(base_dir=tmp_path)

    now = datetime.now()
    # 过期文件：40 天前，100 字节；按天清理会删除
    expired = tmp_path / "expired.png"
    expired.write_bytes(b"x" * 100)
    expired_t = (now - timedelta(days=40)).timestamp()
    os.utime(expired, (expired_t, expired_t))
    # 较新文件：1 天前，各 100 字节；按天保留，但总量超配额时最旧的会被驱逐
    recent_old = tmp_path / "recent_old.png"
    recent_old.write_bytes(b"x" * 100)
    recent_old_t = (now - timedelta(days=1, seconds=2)).timestamp()
    os.utime(recent_old, (recent_old_t, recent_old_t))
    recent_new = tmp_path / "recent_new.png"
    recent_new.write_bytes(b"x" * 100)
    recent_new_t = (now - timedelta(days=1, seconds=1)).timestamp()
    os.utime(recent_new, (recent_new_t, recent_new_t))

    # 按天 30 天删除 expired 的 100B；剩余 200B，配额 150 须再驱逐最旧的 recent_old
    result = manager.run_cleanup_policies(days=30, max_total_bytes=150)

    assert result["deleted_files"] == 2
    assert result["deleted_size"] == 200
    assert result["errors"] == []
    assert not expired.exists()
    assert not recent_old.exists()
    assert recent_new.exists()


def test_run_cleanup_policies_quota_excludes_age_deleted_files(tmp_path: Path) -> None:
    """配额驱逐对按天清理已删除的文件不重复 unlink，errors 不含已删路径的删除失败。"""
    manager = FileManager(base_dir=tmp_path)

    now = datetime.now()
    expired = tmp_path / "expired.png"
    expired.write_bytes(b"x" * 100)
    expired_t = (now - timedelta(days=40)).timestamp()
    os.utime(expired, (expired_t, expired_t))
    keep = tmp_path / "keep.png"
    keep.write_bytes(b"x" * 100)
    keep_t = (now - timedelta(days=1)).timestamp()
    os.utime(keep, (keep_t, keep_t))

    # 按天删除 expired；剩余 keep 100B，配额 200 不超，不再驱逐
    result = manager.run_cleanup_policies(days=30, max_total_bytes=200)

    assert result["deleted_files"] == 1
    assert result["deleted_size"] == 100
    assert result["errors"] == []
    assert not expired.exists()
    assert keep.exists()


def test_run_cleanup_policies_skips_age_when_days_below_one(tmp_path: Path) -> None:
    """days<1 时跳过按天清理，仅执行配额驱逐。"""
    manager = FileManager(base_dir=tmp_path)

    now = datetime.now()
    old_file = tmp_path / "old.png"
    old_file.write_bytes(b"x" * 100)
    old_t = (now - timedelta(days=40)).timestamp()
    os.utime(old_file, (old_t, old_t))

    # days=0 跳过按天清理：old_file 虽过期仍保留；配额 50 须驱逐它
    result = manager.run_cleanup_policies(days=0, max_total_bytes=50)

    assert result["deleted_files"] == 1
    assert result["deleted_size"] == 100
    assert not old_file.exists()


def test_run_cleanup_policies_skips_quota_when_none(tmp_path: Path) -> None:
    """max_total_bytes=None 时跳过配额驱逐，仅执行按天清理。"""
    manager = FileManager(base_dir=tmp_path)

    now = datetime.now()
    expired = tmp_path / "expired.png"
    expired.write_bytes(b"x" * 100)
    expired_t = (now - timedelta(days=40)).timestamp()
    os.utime(expired, (expired_t, expired_t))

    result = manager.run_cleanup_policies(days=30, max_total_bytes=None)

    assert result["deleted_files"] == 1
    assert result["deleted_size"] == 100
    assert not expired.exists()


def test_run_cleanup_only_deletes_image_files(tmp_path: Path) -> None:
    """清理仅删除图片扩展名文件，base_dir 内其他类型文件保留，防误删用户数据。"""
    manager = FileManager(base_dir=tmp_path)

    old_time = (datetime.now() - timedelta(days=40)).timestamp()
    old_image = tmp_path / "old.png"
    old_image.write_bytes(b"img")
    os.utime(old_image, (old_time, old_time))
    old_doc = tmp_path / "notes.txt"
    old_doc.write_bytes(b"notes")
    os.utime(old_doc, (old_time, old_time))
    old_data = tmp_path / "data.json"
    old_data.write_bytes(b"{}")
    os.utime(old_data, (old_time, old_time))

    manager.run_cleanup_policies(days=30, max_total_bytes=None)

    assert not old_image.exists()
    assert old_doc.exists()
    assert old_data.exists()


def test_run_cleanup_sweeps_stale_part_files_only(tmp_path: Path) -> None:
    """超龄 .part 遗留临时文件被清扫并计入统计；宽限期内的在途临时文件保留。"""
    manager = FileManager(base_dir=tmp_path)

    stale_time = (datetime.now() - timedelta(days=2)).timestamp()
    stale_part = tmp_path / "tmpabc123.png.part"
    stale_part.write_bytes(b"x" * 100)
    os.utime(stale_part, (stale_time, stale_time))
    fresh_part = tmp_path / "tmpdef456.png.part"
    fresh_part.write_bytes(b"y" * 50)

    result = manager.run_cleanup_policies(days=30, max_total_bytes=None)

    assert not stale_part.exists()
    assert fresh_part.exists()
    assert result["deleted_files"] == 1
    assert result["deleted_size"] == 100


def test_run_cleanup_quota_only_config_prunes_empty_dirs(tmp_path: Path) -> None:
    """CLEANUP_DAYS=0 且仅配置总量配额时空目录同样回收，不随 days 门控累积。"""
    manager = FileManager(base_dir=tmp_path)

    empty_date_dir = tmp_path / "2026-08-16"
    empty_date_dir.mkdir()

    manager.run_cleanup_policies(days=0, max_total_bytes=1024)

    assert not empty_date_dir.exists()


def test_generate_markdown_reference_encodes_spaces_and_parens(tmp_path: Path) -> None:
    """文件名含空格与圆括号时 Markdown 引用目标百分号编码，符合 CommonMark 语法。"""
    manager = FileManager(base_dir=tmp_path)

    target = tmp_path / "2026-08-16" / "seedream"
    target.mkdir(parents=True)
    image = target / "my pic (1)_a1b2c3d4.png"
    image.write_bytes(b"img")

    markdown_ref = manager.generate_markdown_reference(image, alt_text="pic")

    assert "my%20pic%20%281%29_a1b2c3d4.png" in markdown_ref
    assert " " not in markdown_ref.split("(", 1)[1]


def test_run_cleanup_cleans_files_at_arbitrary_depth(
    tmp_path: Path,
) -> None:
    """迭代扫描无递归深度限制，超深目录树内的过期文件同样进入清理视野。

    树深超过钳低后的递归余量，递归式扫描实现在此必爆 RecursionError，守护
    扫描保持迭代形态不随深度回退。
    """
    depth = _deep_tree_depth(tmp_path)
    deep = tmp_path
    for _ in range(depth):
        deep = deep / "d"
    deep.mkdir(parents=True)
    old_deep_image = deep / "deep.png"
    old_deep_image.write_bytes(b"x" * 10)
    deep_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(old_deep_image, (deep_time, deep_time))

    manager = FileManager(base_dir=tmp_path)
    recursion_limit = sys.getrecursionlimit()
    try:
        # 递归余量压到树深一半以下，迭代实现不新增递归深度不受影响。
        sys.setrecursionlimit(len(inspect.stack()) + depth // 2)
        result = manager.run_cleanup_policies(days=30, max_total_bytes=None)
    finally:
        sys.setrecursionlimit(recursion_limit)

    assert result["errors"] == []
    assert not old_deep_image.exists()


def test_generate_markdown_reference_encodes_hash_and_percent(tmp_path: Path) -> None:
    """文件名含 # 与 % 时同样百分号编码，引用目标不被截断或误解码。

    # 是 Markdown fragment 起点，% 会被按百分号编码误解码；百分号先编码，产物
    不被二次编码为 %2520。
    """
    manager = FileManager(base_dir=tmp_path)

    target = tmp_path / "2026-08-16" / "seedream"
    target.mkdir(parents=True)
    image = target / "pic #1 50%_a1b2c3d4.png"
    image.write_bytes(b"img")

    markdown_ref = manager.generate_markdown_reference(image, alt_text="pic")

    assert "pic%20%231%2050%25_a1b2c3d4.png" in markdown_ref
    assert "%2520" not in markdown_ref
    target_part = markdown_ref.split("(", 1)[1]
    assert "#" not in target_part
    assert target_part.count("%") == target_part.count("%2")


def test_collect_all_files_warns_when_entries_exceed_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清理扫描的图片条目数超过告警阈值时记录含条数与阈值的 warning，收集行为不变。"""
    import seedream_mcp.utils.io.io_storage as io_storage_module

    from _log_fakes import capture_loguru_messages

    monkeypatch.setattr(io_storage_module, "_CLEANUP_ENTRY_WARN_THRESHOLD", 2)
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(b"x")

    manager = FileManager(base_dir=tmp_path)
    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        all_files, _part_files, _directories = manager._collect_all_files([])

    assert len(all_files) == 3
    assert any("超过阈值 2" in message for message in warnings)
