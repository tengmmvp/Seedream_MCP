"""FileManager 目录 memo 的批量去重、安全不变量与回收一致性测试。

覆盖批量保存的 mkdir/resolve 调用不随图数线性增长、memo 登记仅授予验证为
真实目录链的路径（恶意 custom_name 与工具子目录清洗后仍落在 base_dir 内、
链路含链接形态的组织目录不获登记且逐次完整校验拒绝）、空目录回收使 memo
失效后下一次保存重建目录，以及命中复查兜住外部删除、跨实例清理与符号链接
替换三类登记漂移。
"""

from __future__ import annotations

import os
import shutil
import stat
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

import pytest

import seedream_mcp.utils.io.storage as io_storage
from seedream_mcp.utils.io.storage import FileManager, FileManagerError

from _lib.os_fakes import _make_dir_link


class _DatePinnedDatetime(datetime):
    """日期段钉为固定值的受控时间源：日期子目录恒定，时间仍取真实时钟。

    整体冻结时间会使同批文件名时间戳失去唯一性，仅钉日期部分；
    批量用例内日期子目录即 memo 键恒定，计数断言不受跨午夜影响。
    """

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _DatePinnedDatetime:
        real = datetime.now(tz)
        return cls(
            2024, 1, 1, real.hour, real.minute, real.second, real.microsecond, tzinfo=real.tzinfo
        )


def test_batch_save_directory_calls_not_linear_in_image_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同批保存共享同一组织目录：首图建目录并验证链路真实，后续图零 mkdir 零 resolve。"""
    monkeypatch.setattr(io_storage, "datetime", _DatePinnedDatetime)
    manager = FileManager(base_dir=tmp_path)
    counters = {"mkdir": 0, "resolve": 0}
    original_mkdir = Path.mkdir
    original_resolve = Path.resolve

    def counting_mkdir(self: Path, *args: Any, **kwargs: Any) -> Any:
        counters["mkdir"] += 1
        return original_mkdir(self, *args, **kwargs)

    def counting_resolve(self: Path, *args: Any, **kwargs: Any) -> Any:
        counters["resolve"] += 1
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)
    monkeypatch.setattr(Path, "resolve", counting_resolve)

    def save_one(index: int) -> Path:
        path = manager.create_save_path(prompt="cat", url=f"https://x/{index}.png", tool_name="t2i")
        manager.save_bytes(path, b"data")
        return path

    save_one(0)
    warmup = (counters["mkdir"], counters["resolve"])
    # 首图完成组织目录链的全部 mkdir；链路经 no-follow lstat 验证真实，不触发 resolve。
    assert warmup[0] > 0
    assert warmup[1] == 0

    for index in range(1, 11):
        save_one(index)

    assert counters["mkdir"] == warmup[0]
    assert counters["resolve"] == warmup[1]
    assert len(list(manager.base_dir.rglob("*.png"))) == 11


def test_malicious_custom_name_and_tool_name_stay_within_base_dir(
    tmp_path: Path,
) -> None:
    """含 ../ 与路径分隔符的 custom_name、工具子目录清洗后仍落在 base_dir 内落盘。"""
    manager = FileManager(base_dir=tmp_path)
    path = manager.create_save_path(
        prompt="p",
        url="https://x/1.png",
        tool_name="../escape",
        custom_name="../../evil\\name:x?.png",
    )

    assert path.resolve().is_relative_to(manager.base_dir)
    manager.save_bytes(path, b"data")
    assert path.exists()
    assert path.resolve().is_relative_to(manager.base_dir)


def test_dot_only_tool_name_and_custom_name_neutralized(tmp_path: Path) -> None:
    """恰为 "." 或 ".." 的分量替换为下划线，不可作目录或词干逃逸 base_dir。"""
    manager = FileManager(base_dir=tmp_path)
    path = manager.create_save_path(
        prompt="p", url="https://x/1.png", tool_name="..", custom_name=".."
    )

    assert path.resolve().is_relative_to(manager.base_dir)
    assert path.name.startswith("_")
    assert path.parent != manager.base_dir.parent


def test_unverifiable_chain_keeps_rejecting_on_retry(tmp_path: Path) -> None:
    """链路验证失败的组织目录不获 memo 登记，重试仍走完整校验拒绝。

    日期段替换为界外目录链接：mkdir 经链接可成功但链路不可验证真实，
    登记不发生，每次保存都回落 resolve 校验并拒绝越界路径。
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    manager = FileManager(base_dir=base)
    first = manager.create_save_path(prompt="cat", url="https://x/1.png", tool_name="t2i")
    date_dir = first.parent.parent
    shutil.rmtree(date_dir)
    _make_dir_link(outside, date_dir)

    for _ in range(2):
        with pytest.raises(FileManagerError, match="路径不安全"):
            manager.create_save_path(prompt="cat", url="https://x/2.png", tool_name="t2i")

    assert first.parent not in manager._ensured_dirs


def test_pruned_directory_recreated_on_next_save(tmp_path: Path) -> None:
    """空目录回收使 memo 失效，下一次保存重建目录并落盘成功。"""
    manager = FileManager(base_dir=tmp_path)
    first = manager.create_save_path(prompt="cat", url="https://x/1.png", tool_name="t2i")
    manager._prune_empty_dirs([first.parent])
    assert not first.parent.exists()

    second = manager.create_save_path(prompt="cat", url="https://x/2.png", tool_name="t2i")
    manager.save_bytes(second, b"data")

    assert second.parent == first.parent
    assert second.exists()


def test_external_directory_removal_recovers_on_next_save(tmp_path: Path) -> None:
    """组织目录被外部删除后登记残留，命中复查失效并回落重建，后续保存持续成功。"""
    manager = FileManager(base_dir=tmp_path)
    first = manager.create_save_path(prompt="cat", url="https://x/1.png", tool_name="t2i")
    shutil.rmtree(first.parent)
    assert not first.parent.exists()

    second = manager.create_save_path(prompt="cat", url="https://x/2.png", tool_name="t2i")
    manager.save_bytes(second, b"data")

    assert second.parent == first.parent
    assert second.exists()


def test_cross_instance_prune_recovers_via_memo_recheck(tmp_path: Path) -> None:
    """清理实例修剪只失效自身 memo：写入实例经命中复查发现目录消失并重建。

    建模调用级 save_path 场景下 writer 与清理解析到不同缓存 FileManager 的形态。
    """
    writer = FileManager(base_dir=tmp_path)
    cleaner = FileManager(base_dir=tmp_path)
    first = writer.create_save_path(prompt="cat", url="https://x/1.png", tool_name="t2i")
    cleaner._prune_empty_dirs([first.parent])
    assert not first.parent.exists()

    second = writer.create_save_path(prompt="cat", url="https://x/2.png", tool_name="t2i")
    writer.save_bytes(second, b"data")

    assert second.parent == first.parent
    assert second.exists()


def test_memo_hit_symlink_swap_falls_back_to_full_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命中复查发现目录被换成符号链接：丢弃登记并回落完整 resolve 校验。"""
    manager = FileManager(base_dir=tmp_path)
    first = manager.create_save_path(prompt="cat", url="https://x/1.png", tool_name="t2i")

    validate_calls = {"count": 0}
    original_validate = manager.validate_path

    def counting_validate(path: Path) -> bool:
        validate_calls["count"] += 1
        return original_validate(path)

    monkeypatch.setattr(manager, "validate_path", counting_validate)

    real_lstat = os.lstat
    symlink_stat = os.stat_result((stat.S_IFLNK | 0o777, 0, 0, 0, 0, 0, 0, 0, 0, 0))

    def fake_lstat(path: os.PathLike[str] | str, *args: Any, **kwargs: Any) -> os.stat_result:
        if Path(path) == first.parent:
            return symlink_stat
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake_lstat)

    manager.create_save_path(prompt="cat", url="https://x/2.png", tool_name="t2i")

    assert validate_calls["count"] == 1
