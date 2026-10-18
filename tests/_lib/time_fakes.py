"""time 层双假钟的共享测试替身。

假 time 供 test_throttles、test_auto_save_and_download 与 test_auto_save_batch
检验 due/mark 计时，两钟一并安装防用例漏装单调钟；mark(key, backoff) 到期
公式的 due 翻转断言供两处清理退避用例复用。
"""

from __future__ import annotations

import time
import types

import pytest

from seedream_mcp.utils.core import throttles
from seedream_mcp.utils.core.throttles import ThrottleRegistry


def _install_dual_clock(
    monkeypatch: pytest.MonkeyPatch, module: types.ModuleType
) -> tuple[list[float], list[float]]:
    """monkeypatch module 的 time 名字为墙钟/单调钟双假钟，返回可分别拨动的 (wall, mono)。"""
    wall = [time.time()]
    mono = [time.monotonic()]
    monkeypatch.setattr(
        module,
        "time",
        types.SimpleNamespace(time=lambda: wall[0], monotonic=lambda: mono[0]),
    )
    return wall, mono


def _assert_due_flips_at_window_edges(
    monkeypatch: pytest.MonkeyPatch,
    registry: ThrottleRegistry[str],
    key: str,
    t_before: float,
    t_after: float,
    backoff: float,
) -> None:
    """锁定 mark(key, backoff) 写入的到期时间戳形态为 now + backoff：双假钟在时钟窗口两端翻转 due。"""
    _, mono = _install_dual_clock(monkeypatch, throttles)
    mono[0] = t_before + backoff - 1.0
    assert registry.due(key) is False
    mono[0] = t_after + backoff + 1.0
    assert registry.due(key) is True
