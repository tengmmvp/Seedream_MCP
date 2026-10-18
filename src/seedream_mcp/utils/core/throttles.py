"""按键节流的容量上限注册表。

记录键到下次到期时间戳的映射，容量超限按最近使用序驱逐最久未用的键；自身不加锁，
检查与写入的原子性由调用方的外部锁保证。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Hashable
from typing import Generic, TypeVar

from .bounded_store import store_bounded_entry

_RegistryKeyT = TypeVar("_RegistryKeyT", bound=Hashable)


class ThrottleRegistry(Generic[_RegistryKeyT]):
    """键到下次到期时间戳的容量上限注册表，驱逐按最近使用序进行。

    due 与 mark 收口到期判定与推进：以 time.monotonic 计时，墙钟回拨不冻结节流；
    未登记键缺省视为早已到期。mark 把键移到最近使用端，刚推进到期时间戳的键
    不会被容量上限驱逐。
    """

    def __init__(self, max_entries: int) -> None:
        """构建空注册表，max_entries 为条目数上限。"""
        if max_entries < 1:
            raise ValueError("max_entries 必须为正整数")
        self._max_entries = max_entries
        self._entries: OrderedDict[_RegistryKeyT, float] = OrderedDict()

    def due(self, key: _RegistryKeyT) -> bool:
        """判定键是否到达或越过下次到期时间戳，未登记键缺省视为早已到期。"""
        return time.monotonic() >= self._entries.get(key, float("-inf"))

    def mark(self, key: _RegistryKeyT, interval: float) -> None:
        """把键的下次到期时间戳记为 now + interval 并移到最近使用端，超上限驱逐。"""
        store_bounded_entry(self._entries, key, time.monotonic() + interval, self._max_entries)

    def clear(self) -> None:
        """清空全部条目。"""
        self._entries.clear()

    def __len__(self) -> int:
        """返回条目数。"""
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        """判定键是否存在。"""
        return key in self._entries
