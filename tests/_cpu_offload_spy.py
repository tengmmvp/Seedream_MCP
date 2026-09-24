"""CPU 卸载线程池测试共享定义。

包装目标函数记录每次调用的位置参数与执行线程名，断言执行落在专用线程池或其外；
saturate 上下文占满进程级专用池并保证失败路径放行，供池满窗口下的用例复用。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from seedream_mcp.utils.core.executors import (
    CPU_OFFLOAD_THREAD_PREFIX,
    cpu_offload_executor,
    run_in_cpu_pool,
)


class CpuOffloadSpy:
    """透传包装器，记录每次调用的位置参数与执行线程名。"""

    def __init__(self, wrapped: Callable[..., Any]) -> None:
        self._wrapped = wrapped
        self.calls: list[tuple[Any, ...]] = []
        self.thread_names: list[str] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        self.thread_names.append(threading.current_thread().name)
        return self._wrapped(*args, **kwargs)

    def assert_ran_in_cpu_pool(self) -> None:
        """断言至少发生一次调用且全部执行于专用线程池。"""
        assert self.thread_names, "被包装的调用未发生"
        assert all(
            name.startswith(CPU_OFFLOAD_THREAD_PREFIX) for name in self.thread_names
        ), f"存在非专用池线程的调用: {self.thread_names}"

    def assert_ran_outside_cpu_pool(self) -> None:
        """断言至少发生一次调用且全部执行于专用线程池之外。"""
        assert self.thread_names, "被包装的调用未发生"
        assert all(
            not name.startswith(CPU_OFFLOAD_THREAD_PREFIX) for name in self.thread_names
        ), f"存在专用池线程的调用: {self.thread_names}"


class SaturatedCpuOffloadPool:
    """进程级专用池被占满期间的视图：阻塞任务、放行闸门与排队观察入口。"""

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        tasks: list[asyncio.Task[None]],
        release: threading.Event,
    ) -> None:
        self.executor = executor
        self.tasks = tasks
        self.release = release

    async def wait_queued(self, timeout: float = 5.0) -> None:
        """等待有提交在饱和池的队列排队，为池满窗口内的排队行为提供就位证据。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.executor._work_queue.qsize() == 0:
            if loop.time() >= deadline:
                raise AssertionError("专用池队列未观察到排队提交")
            await asyncio.sleep(0.01)


@contextlib.asynccontextmanager
async def saturate_cpu_offload_pool() -> AsyncIterator[SaturatedCpuOffloadPool]:
    """占满进程级专用池的全部 worker，退出与任一失败路径都放行阻塞任务。"""
    executor = cpu_offload_executor()
    pool_size = executor._max_workers
    release = threading.Event()
    all_started = threading.Event()
    started_lock = threading.Lock()
    started_count = 0

    def _blocker() -> None:
        nonlocal started_count
        with started_lock:
            started_count += 1
            if started_count == pool_size:
                all_started.set()
        assert release.wait(timeout=10)

    tasks = [asyncio.ensure_future(run_in_cpu_pool(_blocker)) for _ in range(pool_size)]
    try:
        assert await asyncio.to_thread(all_started.wait, 5), "专用池 worker 未全部占满"
        yield SaturatedCpuOffloadPool(executor, tasks, release)
    finally:
        # 就绪断言或用例体失败同样放行，专用池不因一次失败级联堵死后续用例。
        release.set()
