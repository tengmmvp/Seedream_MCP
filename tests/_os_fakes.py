"""os 层系统调用的共享测试替身。

供 test_os_utils 与 test_auto_save_and_download 复用 fsync 计数透传，
替代多处内联的 _tracking_fsync 模式。
"""

from __future__ import annotations

import os

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
