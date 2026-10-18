"""os 层系统调用的共享测试替身。

fsync 计数透传供 test_io_file 与 test_auto_save_and_download 复用，os.close
计数透传供 test_io_file、test_settle 与 test_download_image_branches 复用，realpath
计数透传供 test_io_path_polish、test_resolve_base_dir 与
test_browse_tool_result 复用，normcase_parts 计数透传供 test_find_images_directory
与 test_browse_tool_result 复用，目录链接创建原语与其上的链接环、悬空链接构造
供 test_io_path_polish、test_file_manager_fail_fast、test_resolve_base_dir
与 test_file_manager_cleanup 复用，文件符号链接创建原语供 test_io_file 与
test_skill_resources 复用，Path.replace 的单次失败与门控替身、
Path.resolve 的指定名失败替身供 test_io_file、test_download_image_branches、
test_browse_tool_result 与 test_find_images_directory 复用，
替代多处内联的计数包装与构造编排模式。
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

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


def _install_close_counter(
    monkeypatch: pytest.MonkeyPatch,
    record: Callable[[int], Any] = lambda fd: fd,
) -> list[Any]:
    """monkeypatch os.close 为计数透传实现，返回经 record 映射的关闭记录列表。"""
    closed: list[Any] = []
    real_close = os.close

    def _tracking_close(fd: int) -> None:
        closed.append(record(fd))
        real_close(fd)

    monkeypatch.setattr(os, "close", _tracking_close)
    return closed


def _install_counting_realpath(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """monkeypatch os.path.realpath 为计数透传实现，返回调用路径记录列表。"""
    realpath_calls: list[str] = []
    real_realpath = os.path.realpath

    def _counting_realpath(path: "str | os.PathLike[str]", *, strict: bool = False) -> str:
        realpath_calls.append(str(path))
        return real_realpath(path, strict=strict)

    monkeypatch.setattr(os.path, "realpath", _counting_realpath)
    return realpath_calls


def _install_counting_normcase_parts(
    monkeypatch: pytest.MonkeyPatch, owner: ModuleType, suffix: str | None = None
) -> list[str]:
    """monkeypatch owner.normcase_parts 为计数透传实现，返回调用路径记录列表。

    suffix 非空时仅记录以该后缀结尾的路径，None 记录全量调用。
    """
    parts_calls: list[str] = []
    real_parts: Callable[[Path], tuple[str, ...]] = owner.normcase_parts

    def _counting_parts(path: Path) -> tuple[str, ...]:
        path_str = str(path)
        if suffix is None or path_str.endswith(suffix):
            parts_calls.append(path_str)
        return real_parts(path)

    monkeypatch.setattr(owner, "normcase_parts", _counting_parts)
    return parts_calls


def _install_replace_fail_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """monkeypatch Path.replace 为首次抛瞬时 OSError、之后恢复真实替换的替身。"""
    real_replace = Path.replace
    calls = {"count": 0}

    def _failing_then_real_replace(self: Path, target: Path) -> Path:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("transient replace failure")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _failing_then_real_replace)


def _install_gated_path_replace(
    monkeypatch: pytest.MonkeyPatch, *, fail: bool = False
) -> tuple[threading.Event, threading.Event]:
    """monkeypatch Path.replace 为进入即挂起、放行后按 fail 真实替换或抛错的替身。"""
    real_replace = Path.replace
    replace_running = threading.Event()
    replace_release = threading.Event()

    def _gated_replace(self: Path, target: Path) -> Path:
        replace_running.set()
        assert replace_release.wait(5.0), "放行事件未触发"
        if fail:
            raise OSError("simulated replace failure")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _gated_replace)
    return replace_running, replace_release


def _install_failing_resolve_for(monkeypatch: pytest.MonkeyPatch, *names: str) -> dict[str, int]:
    """monkeypatch Path.resolve 为指定文件名抛瞬时 OSError 的替身，返回按名失败计数。"""
    real_resolve = Path.resolve
    failure_counts: dict[str, int] = {}

    def _failing_resolve(self: Path, strict: bool = False) -> Path:
        if self.name in names:
            failure_counts[self.name] = failure_counts.get(self.name, 0) + 1
            raise OSError("temporarily locked")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", _failing_resolve)
    return failure_counts


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


def _make_file_link(target: Path, link: Path) -> None:
    """创建指向 target 文件的符号链接 link，创建能力缺失时跳过用例。

    文件链接无 junction 免特权形态，Windows 需特权或开发者模式；仅链接创建
    自身的 OSError 与 NotImplementedError 视为能力缺失跳过，目标文件预置等
    编排失败不吞、如实上抛。
    """
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前平台或权限无法创建符号链接: {exc}")


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
