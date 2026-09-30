"""os 层系统调用的共享测试替身。

fsync 计数透传供 test_io_file 与 test_auto_save_and_download 复用，realpath
计数透传供 test_io_path_polish、test_helpers_resolve_base_dir 与
test_browse_tool_result 复用，目录链接创建原语与其上的链接环、悬空链接构造
供 test_io_path_polish、test_file_manager_fail_fast、test_helpers_resolve_base_dir
与 test_file_manager_cleanup 复用，替代多处内联的计数包装与构造编排模式。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


def _install_fsync_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """monkeypatch os.fsync 为计数透传实现，返回调用记录列表。"""
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def _tracking_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _tracking_fsync)
    return fsync_calls


def _install_counting_realpath(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """monkeypatch os.path.realpath 为计数透传实现，返回调用路径记录列表。"""
    realpath_calls: list[str] = []
    real_realpath = os.path.realpath

    def _counting_realpath(path: "str | os.PathLike[str]", *, strict: bool = False) -> str:
        realpath_calls.append(str(path))
        return real_realpath(path, strict=strict)

    monkeypatch.setattr(os.path, "realpath", _counting_realpath)
    return realpath_calls


def _make_dir_link(target: Path, link: Path) -> None:
    """以平台可用形态创建指向 target 的目录链接 link，创建能力缺失时跳过用例。

    win32 经 junction（普通用户可创建），POSIX 经符号链接；仅链接创建自身的
    OSError 视为能力缺失跳过，目标目录预置等编排失败不吞、如实上抛。
    """
    if sys.platform == "win32":
        import _winapi

        create: Callable[[str, str], None] = _winapi.CreateJunction
    else:
        create = os.symlink
    try:
        create(str(target), str(link))
    except OSError as exc:
        pytest.skip(f"当前平台或卷不支持创建目录链接: {exc}")


def _make_cycle_link(parent: Path, name: str = "cycle") -> Path:
    """构造互指链接环并返回环入口，链接能力缺失时跳过用例。"""
    link = parent / name
    partner = parent / f"{name}-partner"
    if sys.platform == "win32":
        # junction 目标须先存在：先立实体中转再对调，双 junction 互指成环。
        partner.mkdir()
        _make_dir_link(partner, link)
        partner.rmdir()
        _make_dir_link(link, partner)
    else:
        _make_dir_link(partner, link)
        _make_dir_link(link, partner)
    return link


def _make_dangling_link(parent: Path, name: str = "dangling") -> tuple[Path, Path]:
    """构造指向缺失目标的悬空链接，返回 (链接, 缺失目标)，链接能力缺失时跳过用例。"""
    link = parent / name
    target = parent / f"{name}-target"
    if sys.platform == "win32":
        # junction 目标须先存在：先立目录建链再移除目标，留下悬空链接。
        target.mkdir()
        _make_dir_link(target, link)
        target.rmdir()
    else:
        _make_dir_link(target, link)
    return link, target
