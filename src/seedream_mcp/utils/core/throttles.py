"""按键节流的容量上限注册表。

记录键到到期条目的映射，容量超限按最近使用序驱逐最久未用的键；自身不加锁，
检查与写入的原子性由调用方的外部锁保证。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Hashable
from typing import Generic, TypeVar

from .bounded_store import store_bounded_entry

_RegistryKeyT = TypeVar("_RegistryKeyT", bound=Hashable)
_RegistryEntryT = TypeVar("_RegistryEntryT")


class BoundedExpiryRegistry(ABC, Generic[_RegistryKeyT, _RegistryEntryT]):
    """键到到期条目的容量上限注册表，驱逐按最近使用序进行。

    due 与 mark 收口到期判定与推进：以 time.monotonic 计时，墙钟回拨不冻结节流；
    未登记键缺省视为早已到期。mark 把键移到最近使用端，刚推进到期时间戳的键
    不会被容量上限驱逐。条目载荷形态由子类经两个钩子定义：_entry_expiry 读取
    条目到期时间戳，_entry_for 在推进到期时派生待存条目并保全随键载荷。
    """

    def __init__(self, max_entries: int) -> None:
        """构建空注册表，max_entries 为条目数上限。"""
        if max_entries < 1:
            raise ValueError("max_entries 必须为正整数")
        self._max_entries = max_entries
        self._entries: OrderedDict[_RegistryKeyT, _RegistryEntryT] = OrderedDict()

    def due(self, key: _RegistryKeyT) -> bool:
        """判定键是否到达或越过下次到期时间戳，未登记键缺省视为早已到期。"""
        entry = self._entries.get(key)
        return entry is None or self._now() >= self._entry_expiry(entry)

    def mark(self, key: _RegistryKeyT, interval: float) -> None:
        """把键的下次到期时间戳记为 now + interval 并移到最近使用端，超上限驱逐。"""
        self._write(key, self._entry_for(key, self._now() + interval))

    def _now(self) -> float:
        """到期计时的单调钟读数单点。"""
        return time.monotonic()

    def _write(self, key: _RegistryKeyT, entry: _RegistryEntryT) -> None:
        """写入条目并移到最近使用端，超上限按最久未用序驱逐。"""
        store_bounded_entry(self._entries, key, entry, self._max_entries)

    @abstractmethod
    def _entry_expiry(self, entry: _RegistryEntryT) -> float:
        """读取条目携带的下次到期时间戳。"""

    @abstractmethod
    def _entry_for(self, key: _RegistryKeyT, expires_at: float) -> _RegistryEntryT:
        """由推进后的到期时间戳派生待存条目，随键载荷由实现保全。"""

    def clear(self) -> None:
        """清空全部条目。"""
        self._entries.clear()

    def __len__(self) -> int:
        """返回条目数。"""
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        """判定键是否存在。"""
        return key in self._entries


class ThrottleRegistry(BoundedExpiryRegistry[_RegistryKeyT, float]):
    """条目为裸到期时间戳的节流注册表，不携带随键附加状态。"""

    def _entry_expiry(self, entry: float) -> float:
        """到期时间戳即条目本身。"""
        return entry

    def _entry_for(self, key: _RegistryKeyT, expires_at: float) -> float:
        """无随键载荷，推进后的到期时间戳即条目。"""
        del key
        return expires_at
