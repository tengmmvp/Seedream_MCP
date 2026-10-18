"""事件循环键控的进程级信号量工厂与单飞注册表。

asyncio.Semaphore 绑定首次使用的事件循环，跨循环复用会 RuntimeError；本工厂
按调用点 key 与限值缓存最近一个事件循环的实例，循环更替或限值变化时重建，
多个活跃循环并发调用同 key 会互相顶替。重建不迁移在途持有者，热切换窗口内
并发上限可能瞬时超出新旧限值；本服务生产路径运行在单一事件循环且配置不可变，
测试与嵌入式宿主按此取舍接受。
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable, Hashable
from typing import Any, Generic, TypeVar

from .inflight import InflightEntry

# 各调用点的最近信号量缓存：key -> (事件循环, 限值, 信号量)。key 集合由调用点
# 决定为有限集，不做驱逐；identity 比较防 id 复用误判。
_last_semaphore: dict[str, tuple[asyncio.AbstractEventLoop, int, asyncio.Semaphore]] = {}


def loop_bound_semaphore(limit: int, *, key: str) -> asyncio.Semaphore:
    """返回绑定当前事件循环的进程级信号量，循环更替或限值变化时重建。

    Args:
        limit: 信号量并发上限，须不小于 1。
        key: 调用点标识，区分不同用途的缓存条目。

    Raises:
        ValueError: limit 小于 1（Semaphore(0) 会永久阻塞，尽早暴露配置错误）。
    """
    if limit < 1:
        raise ValueError(f"信号量并发上限须不小于 1: {limit}")
    loop = asyncio.get_running_loop()
    cached = _last_semaphore.get(key)
    if cached is not None and cached[0] is loop and cached[1] == limit:
        return cached[2]
    semaphore = asyncio.Semaphore(limit)
    _last_semaphore[key] = (loop, limit, semaphore)
    return semaphore


_RegistryKeyT = TypeVar("_RegistryKeyT", bound=Hashable)
_ResultT = TypeVar("_ResultT")

# 在途共享 task 强引用集的循环键索引，值为弱引用：模块级容器若强持集，集持
# task、task 持循环会钉死循环弱键，多循环宿主每废弃一个循环即泄漏一个循环加
# task；集的强根挂在该循环定时堆的锚定时器回调参数上，随循环对象消亡，
# 退役循环连同未决 task 一并回收。
_LOOP_TASK_HOLDS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, weakref.ref[set["asyncio.Task[Any]"]]
] = weakref.WeakKeyDictionary()

# 锚定时器延迟（秒，约 12 天），短于常见底层实现的定时上限（uvloop 约 24 天）
# 以避免截断行为差异；到期经回调自续。
_HOLDS_ANCHOR_SECONDS = 2**20


def _rearm_holds_anchor(loop: asyncio.AbstractEventLoop, holds: "set[asyncio.Task[Any]]") -> None:
    """锚定时器到期自续，强引用集在循环存活期间始终保有强根。"""
    loop.call_at(loop.time() + _HOLDS_ANCHOR_SECONDS, _rearm_holds_anchor, loop, holds)


def _loop_task_holds(task: "asyncio.Task[Any]") -> "set[asyncio.Task[Any]]":
    """取 task 所属循环上的在途强引用集，无则建档并锚定到该循环。

    强引用集经锚定时器挂到循环定时堆而非循环对象属性：
    uvloop 等 Cython 扩展循环无 __dict__，setattr 以 AttributeError 拒绝；挂起的
    定时回调被循环强持直至触发或取消，锚随循环存活成立且与循环实现无关。
    """
    loop = task.get_loop()
    holds_ref = _LOOP_TASK_HOLDS.get(loop)
    holds = holds_ref() if holds_ref is not None else None
    if holds is None:
        holds = set()
        _LOOP_TASK_HOLDS[loop] = weakref.ref(holds)
        loop.call_at(loop.time() + _HOLDS_ANCHOR_SECONDS, _rearm_holds_anchor, loop, holds)
    return holds


class LoopKeyedInflight(Generic[_RegistryKeyT, _ResultT]):
    """事件循环键控的进程级单飞注册表。

    每循环一张键→在途条目弱引用表，同循环同键并发共享首个创建的 task；共享 task
    绑定创建循环，consume 以专属 future 等待，跨循环等待会挂起而非抛错，
    注册表按循环隔离使该路径结构不可达，跨循环调用方各自登记是文档化权衡。
    命中检查与登记的原子窗口由 register_if_absent 在单次同步调用内闭合，
    同循环内无竞态；在途 task 由所属循环上的强引用集保活，消费者全部放弃后
    不因挂在无强根 future 上被循环 GC 整环回收；注册链不持循环强引用，循环退役时
    强引用集随循环对象一并消亡，未决 task 与在途条目随之回收；等待侧取消语义与
    孤儿异常兜底由 InflightEntry 承担。
    """

    def __init__(self) -> None:
        self.by_loop: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop,
            dict[_RegistryKeyT, weakref.ref[InflightEntry[_ResultT]]],
        ] = weakref.WeakKeyDictionary()

    def _registry_for(
        self, loop: asyncio.AbstractEventLoop
    ) -> dict[_RegistryKeyT, weakref.ref[InflightEntry[_ResultT]]]:
        """取该事件循环的键到在途条目弱引用表，无则建档。"""
        registry = self.by_loop.get(loop)
        if registry is None:
            registry = {}
            self.by_loop[loop] = registry
        return registry

    def peek(self, key: _RegistryKeyT) -> InflightEntry[_ResultT] | None:
        """返回当前事件循环该键的在途条目，未登记或弱引用已死时为 None。"""
        registry = self.by_loop.get(asyncio.get_running_loop())
        if registry is None:
            return None
        entry_ref = registry.get(key)
        return entry_ref() if entry_ref is not None else None

    def _register(
        self, key: _RegistryKeyT, task: "asyncio.Task[_ResultT]"
    ) -> InflightEntry[_ResultT]:
        """登记当前事件循环该键的在途条目并挂完成回调按条目身份清理。

        仅由 register_if_absent 在键确认空闲后调用，否则会顶替仍在途的条目。
        """
        registry = self._registry_for(asyncio.get_running_loop())
        entry = InflightEntry(task)
        registry[key] = weakref.ref(entry)
        # 强引用集保活防 task 被循环 GC 回收，done 回调丢失则注册表登记与调用方槽位清理不执行。
        holds = _loop_task_holds(task)
        holds.add(task)
        task.add_done_callback(holds.discard)

        def _release_if_current(finished: "asyncio.Task[_ResultT]") -> None:
            current = registry.get(key)
            if current is not None and current() is entry:
                registry.pop(key)

        task.add_done_callback(_release_if_current)
        return entry

    def register_if_absent(
        self, key: _RegistryKeyT, task_factory: Callable[[], "asyncio.Task[_ResultT]"]
    ) -> tuple[InflightEntry[_ResultT], bool]:
        """键空闲时经工厂创建 task 登记返回 (新条目, True)，被占用返回 (既有条目, False)。

        命中检查与登记在单次同步调用内完成，调用方无法在两步之间插入 await 点，
        无需约定即不会顶替仍在途的条目；工厂仅在空闲时被调用。
        """
        entry = self.peek(key)
        if entry is not None:
            return entry, False
        return self._register(key, task_factory()), True

    def __len__(self) -> int:
        """返回当前事件循环的在途条目数，弱引用已死的残留不计入。"""
        registry = self.by_loop.get(asyncio.get_running_loop())
        if registry is None:
            return 0
        return sum(1 for entry_ref in registry.values() if entry_ref() is not None)

    async def consume_shared(
        self, key: _RegistryKeyT, task_factory: Callable[[], "asyncio.Task[_ResultT]"]
    ) -> _ResultT:
        """命中在途条目直接共享等待，未命中经工厂创建 task 登记后等待。"""
        entry, _created = self.register_if_absent(key, task_factory)
        return await entry.consume()
