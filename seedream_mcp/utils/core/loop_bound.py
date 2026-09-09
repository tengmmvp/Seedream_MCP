"""事件循环键控的进程级信号量工厂。

asyncio.Semaphore 绑定首次使用的事件循环，跨循环复用会 RuntimeError；本工厂
按调用点 key 与限值缓存最近一个事件循环的实例，循环更替或限值变化时重建，
多个活跃循环并发调用同 key 会互相顶替。重建不迁移在途持有者，热切换窗口内
并发上限可能瞬时超出新旧限值；本服务生产路径运行在单一事件循环且配置不可变，
测试与嵌入式宿主按此取舍接受。
"""

from __future__ import annotations

import asyncio

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
