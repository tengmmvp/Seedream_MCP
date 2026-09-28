"""os 层系统调用的共享测试替身。

fsync 计数透传供 test_io_file 与 test_auto_save_and_download 复用，resolve
计数透传供 test_browse_tool_result、test_io_path_polish 与
test_helpers_resolve_base_dir 复用，替代多处内联的计数包装模式。
"""

from __future__ import annotations

import os
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


def _install_counting_resolve(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """monkeypatch Path.resolve 为计数透传实现，返回调用路径记录列表。"""
    resolve_calls: list[str] = []
    real_resolve = Path.resolve

    def _counting_resolve(self: Path, strict: bool = False) -> Path:
        resolve_calls.append(str(self))
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", _counting_resolve)
    return resolve_calls
