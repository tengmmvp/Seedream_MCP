"""有界 LRU 存放原语。

写入条目并移到最近使用端，容量超限按最久未用序驱逐；自身不加锁，检查与写入
的原子性由调用方的外部锁保证，持自有锁与 OrderedDict 的调用方直接复用。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable
from typing import TypeVar

_BoundedKeyT = TypeVar("_BoundedKeyT", bound=Hashable)
_BoundedValueT = TypeVar("_BoundedValueT")


def store_bounded_entry(
    entries: OrderedDict[_BoundedKeyT, _BoundedValueT],
    key: _BoundedKeyT,
    value: _BoundedValueT,
    max_entries: int,
) -> None:
    """写入条目并移到最近使用端，超上限按最久未用序驱逐，max_entries 非正拒绝。"""
    if max_entries < 1:
        raise ValueError("max_entries 必须为正整数")
    entries[key] = value
    entries.move_to_end(key)
    while len(entries) > max_entries:
        entries.popitem(last=False)
