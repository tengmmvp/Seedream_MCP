"""ThrottleRegistry 容量驱逐与读写语义的单元测试。"""

import pytest

from seedream_mcp.utils.core.throttles import ThrottleRegistry


def test_set_moves_existing_key_to_recent_end() -> None:
    """对已有键 set 移到最近使用端，比它久未用的键先被驱逐。"""
    registry: ThrottleRegistry[str] = ThrottleRegistry(2)
    registry.set("a", 1.0)
    registry.set("b", 2.0)
    registry.set("a", 3.0)
    registry.set("c", 4.0)

    assert registry.get("b", 0.0) == 0.0
    assert registry.get("a", 0.0) == 3.0
    assert registry.get("c", 0.0) == 4.0


def test_capacity_evicts_in_recency_order() -> None:
    """超上限时按最近使用序驱逐，驱逐完成后条目数保持在上限。"""
    registry: ThrottleRegistry[str] = ThrottleRegistry(3)
    for index in range(3):
        registry.set(f"key-{index}", float(index))
    registry.set("new", 9.0)

    assert len(registry) == 3
    assert "key-0" not in registry
    assert all(key in registry for key in ("key-1", "key-2", "new"))


def test_get_keeps_eviction_order() -> None:
    """get 只读不移动键的使用位置，读取过的键仍按原序被驱逐。"""
    registry: ThrottleRegistry[str] = ThrottleRegistry(2)
    registry.set("a", 1.0)
    registry.set("b", 2.0)

    assert registry.get("a", 0.0) == 1.0
    registry.set("c", 3.0)

    assert registry.get("a", 0.0) == 0.0
    assert registry.get("b", 0.0) == 2.0
    assert registry.get("c", 0.0) == 3.0


def test_clear_removes_all_entries() -> None:
    """clear 清空全部条目，清空后可继续写入。"""
    registry: ThrottleRegistry[str] = ThrottleRegistry(2)
    registry.set("a", 1.0)
    registry.clear()

    assert len(registry) == 0
    assert registry.get("a", 0.0) == 0.0

    registry.set("b", 2.0)
    assert registry.get("b", 0.0) == 2.0


def test_constructor_rejects_non_positive_capacity() -> None:
    """非正上限直接拒绝，避免静默演变为每次写入即全量驱逐。"""
    with pytest.raises(ValueError):
        ThrottleRegistry(0)
