"""ThrottleRegistry 容量驱逐序与 due/mark 计时的单元测试。"""

import pytest

from seedream_mcp.utils.core.throttles import ThrottleRegistry

from _lib.time_fakes import _install_dual_clock


def test_mark_moves_existing_key_to_recent_end() -> None:
    """对已有键 mark 移到最近使用端，比它久未用的键先被驱逐。"""
    registry: ThrottleRegistry[str] = ThrottleRegistry(2)
    registry.mark("a", 60.0)
    registry.mark("b", 60.0)
    registry.mark("a", 60.0)
    registry.mark("c", 60.0)

    assert len(registry) == 2
    assert "b" not in registry
    assert "a" in registry
    assert "c" in registry


def test_capacity_evicts_in_recency_order() -> None:
    """超上限时按最近使用序驱逐，驱逐完成后条目数保持在上限。"""
    registry: ThrottleRegistry[str] = ThrottleRegistry(3)
    for index in range(3):
        registry.mark(f"key-{index}", 60.0)
    registry.mark("new", 60.0)

    assert len(registry) == 3
    assert "key-0" not in registry
    assert all(key in registry for key in ("key-1", "key-2", "new"))


def test_due_keeps_eviction_order() -> None:
    """due 只读不移动键的使用位置，查询过的键仍按原序被驱逐。"""
    registry: ThrottleRegistry[str] = ThrottleRegistry(2)
    registry.mark("a", 60.0)
    registry.mark("b", 60.0)

    assert registry.due("a") is False
    registry.mark("c", 60.0)

    assert "a" not in registry
    assert "b" in registry
    assert "c" in registry


def test_clear_removes_all_entries() -> None:
    """clear 清空全部条目，清空后可继续写入。"""
    registry: ThrottleRegistry[str] = ThrottleRegistry(2)
    registry.mark("a", 60.0)
    registry.clear()

    assert len(registry) == 0
    assert registry.due("a") is True

    registry.mark("b", 60.0)
    assert registry.due("b") is False


def test_constructor_rejects_non_positive_capacity() -> None:
    """非正上限直接拒绝，避免静默演变为每次写入即全量驱逐。"""
    with pytest.raises(ValueError):
        ThrottleRegistry(0)


def test_due_and_mark_count_on_monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """due/mark 以单调钟计时：墙钟拨动不影响判定，间隔到点门重开。

    未登记键缺省视为早已到期，开机初期 monotonic 接近零不误判新键在节流窗内。
    """
    from seedream_mcp.utils.core import throttles as throttles_module

    wall, mono = _install_dual_clock(monkeypatch, throttles_module)
    registry: ThrottleRegistry[str] = ThrottleRegistry(2)

    assert registry.due("k") is True
    registry.mark("k", 60.0)
    assert registry.due("k") is False

    # 墙钟前拨越过间隔不开门：判定不随墙钟走。
    wall[0] += 7200.0
    assert registry.due("k") is False
    # 墙钟回拨不冻结节流，单调钟越过间隔后门重开。
    wall[0] -= 14400.0
    mono[0] += 61.0
    assert registry.due("k") is True
