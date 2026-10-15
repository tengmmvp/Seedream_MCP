"""按键节流的容量上限注册表。

记录键到节流时间戳的映射，容量超限按最近使用序驱逐最久未用的键；自身不加锁，
检查与写入的原子性由调用方的外部锁保证。写入原语 store_bounded_entry 是有界
LRU 存放惯用式的单一实现，持自有锁与 OrderedDict 的调用方直接复用。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable
from typing import Generic, TypeVar

_RegistryKeyT = TypeVar("_RegistryKeyT", bound=Hashable)
_RegistryValueT = TypeVar("_RegistryValueT")


def store_bounded_entry(
    entries: OrderedDict[_RegistryKeyT, _RegistryValueT],
    key: _RegistryKeyT,
    value: _RegistryValueT,
    max_entries: int,
) -> None:
    """写入条目并移到最近使用端，超上限按最久未用序驱逐，max_entries 非正拒绝。"""
    if max_entries < 1:
        raise ValueError("max_entries 必须为正整数")
    entries[key] = value
    entries.move_to_end(key)
    while len(entries) > max_entries:
        entries.popitem(last=False)


class ThrottleRegistry(Generic[_RegistryKeyT]):
    """键到节流时间戳的容量上限注册表，驱逐按最近使用序进行。

    get 只读且不改变键的使用位置；set 把键移到最近使用端，刚刷新时间戳的键不会
    被容量上限驱逐。
    """

    def __init__(self, max_entries: int) -> None:
        """构建空注册表，max_entries 为条目数上限。"""
        if max_entries < 1:
            raise ValueError("max_entries 必须为正整数")
        self._max_entries = max_entries
        self._entries: OrderedDict[_RegistryKeyT, float] = OrderedDict()

    def get(self, key: _RegistryKeyT, default: float = 0.0) -> float:
        """读取键的节流时间戳，缺失时返回 default，不改变驱逐序。"""
        return self._entries.get(key, default)

    def set(self, key: _RegistryKeyT, value: float) -> None:
        """写入节流时间戳并把键移到最近使用端，超上限时驱逐最久未用的键。"""
        store_bounded_entry(self._entries, key, value, self._max_entries)

    def clear(self) -> None:
        """清空全部条目。"""
        self._entries.clear()

    def __len__(self) -> int:
        """返回条目数。"""
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        """判定键是否存在。"""
        return key in self._entries
