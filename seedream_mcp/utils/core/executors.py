"""进程级长时 CPU 卸载线程池。

大体量 json.loads 与 PIL 解码这类百毫秒至秒级任务，与默认执行器里的路径
解析、mkdir 等延迟敏感短任务同池排队会放大短任务尾延迟；本模块为长任务
提供专用线程池，进程内全部事件循环共用同一实例。
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from .sanitizers import estimate_output_length

T = TypeVar("T")

CPU_OFFLOAD_THREAD_PREFIX = "seedream-cpu-offload"

# 池深封顶防止调高并发配置催生无界线程数。
_CPU_OFFLOAD_MAX_WORKERS_CAP = 32
# 预览解码并发的单一定义点，池深加数与 image_thumbnail 的解码限流信号量共用；
# 像素上限 36MP 的单张解码最坏逾百 MB，并发 3 封顶瞬态。
CPU_OFFLOAD_DECODE_SLOTS = 3
# 提供者缺失或配置不可构建时的并发回退值，对齐 SEEDREAM_GENERATE_CONCURRENCY
# 与 SEEDREAM_IMAGE_PREPARE_CONCURRENCY 默认。
_CPU_OFFLOAD_FALLBACK_GENERATE_CONCURRENCY = 3
_CPU_OFFLOAD_FALLBACK_PREPARE_CONCURRENCY = 5
# 池深下限兜底无准入闸的 webapp 请求体解析并发，量级取 8 核宿主上 asyncio
# 默认执行器的池深 min(32, cpu+4)。
_CPU_OFFLOAD_MIN_WORKERS = 12

# 进程级共享单池：线程池无事件循环亲和，按循环分池会在多循环并发时互相逐出。
_CPU_OFFLOAD_EXECUTOR: ThreadPoolExecutor | None = None
_CPU_OFFLOAD_INIT_LOCK = threading.Lock()

# 池深提供者：config 侧模块加载期注入活动配置的读取入口，返回 (生成并发, 图像预处理
# 并发)，未注册时按默认并发回退。
_CPU_OFFLOAD_DEPTH_PROVIDER: Callable[[], tuple[int, int] | None] | None = None


def register_cpu_offload_depth_provider(
    provider: Callable[[], tuple[int, int] | None],
) -> None:
    """注册池深提供者，config 侧在模块加载时注入活动配置的并发读取入口。

    Args:
        provider: 返回活动配置的 (生成并发, 图像预处理并发)，配置不可构建时返回 None。
    """
    global _CPU_OFFLOAD_DEPTH_PROVIDER
    _CPU_OFFLOAD_DEPTH_PROVIDER = provider


def _derive_cpu_offload_max_workers() -> int:
    """推导池深：两类并发之和加解码槽位，下限兜底无准入闸的解析并发，封顶防无界。"""
    provider = _CPU_OFFLOAD_DEPTH_PROVIDER
    concurrency = provider() if provider is not None else None
    if concurrency is None:
        # 提供者未注册或配置不可构建时按默认并发回退，池可用性不退化。
        concurrency = (
            _CPU_OFFLOAD_FALLBACK_GENERATE_CONCURRENCY,
            _CPU_OFFLOAD_FALLBACK_PREPARE_CONCURRENCY,
        )
    generate_concurrency, prepare_concurrency = concurrency
    return min(
        _CPU_OFFLOAD_MAX_WORKERS_CAP,
        max(
            generate_concurrency + prepare_concurrency + CPU_OFFLOAD_DECODE_SLOTS,
            _CPU_OFFLOAD_MIN_WORKERS,
        ),
    )


def cpu_offload_executor() -> ThreadPoolExecutor:
    """返回进程级长任务线程池，全部事件循环共用同一实例。

    首次调用经注册的池深提供者推导池深，此后不随配置变化重建；进程级清理
    关闭后再次调用重新推导。
    """
    global _CPU_OFFLOAD_EXECUTOR
    executor = _CPU_OFFLOAD_EXECUTOR
    if executor is None:
        with _CPU_OFFLOAD_INIT_LOCK:
            executor = _CPU_OFFLOAD_EXECUTOR
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=_derive_cpu_offload_max_workers(),
                    thread_name_prefix=CPU_OFFLOAD_THREAD_PREFIX,
                )
                _CPU_OFFLOAD_EXECUTOR = executor
    return executor


def shutdown_cpu_offload_executor() -> None:
    """关闭进程级 CPU 卸载池，退出不等队列：排队任务取消、在途任务自然完成。"""
    global _CPU_OFFLOAD_EXECUTOR
    with _CPU_OFFLOAD_INIT_LOCK:
        executor = _CPU_OFFLOAD_EXECUTOR
        _CPU_OFFLOAD_EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _executor_retired(executor: ThreadPoolExecutor) -> bool:
    """判断执行器是否已关闭：全局单池已被替换，或实例自身已置关闭标志。"""
    return _CPU_OFFLOAD_EXECUTOR is not executor or executor._shutdown


class CpuOffloadPoolClosedError(RuntimeError):
    """CPU 卸载池关闭使任务提交被拒或排队任务被取消时 run_in_cpu_pool 的失败形态，供调用点按类型兜底。"""


# 下沉尺寸门槛的单一来源：低于该量级的序列化与解析在微秒级完成，线程往返得不偿失，
# 64KB 起的载荷阻塞事件循环的代价才超过调度开销。
CPU_OFFLOAD_SIZE_THRESHOLD = 64 * 1024


def should_offload_size(size: int) -> bool:
    """载荷尺寸达到卸载阈值即下沉。"""
    return size >= CPU_OFFLOAD_SIZE_THRESHOLD


def should_offload_to_cpu_pool(
    value: Any, *, value_leaf_cost: Callable[[Any, Any, int], int | None] | None = None
) -> bool:
    """池下沉判定单源：载荷长度估算达到卸载阈值或嵌套超深不可估时为真。"""
    estimated = estimate_output_length(
        value,
        limit=CPU_OFFLOAD_SIZE_THRESHOLD,
        value_leaf_cost=value_leaf_cost,
    )
    return estimated is None or should_offload_size(estimated)


async def run_in_cpu_pool(func: Callable[..., T], /, *args: Any) -> T:
    """在专用线程池执行同步长任务，上下文传播对齐 asyncio.to_thread。

    池关闭后的提交拒绝与排队任务被取消都以 CpuOffloadPoolClosedError 失败，
    供调用方按类型兜底；其余异常照原样传播，协程自身的取消传播
    CancelledError。
    """
    func_call = functools.partial(contextvars.copy_context().run, func, *args)
    executor = cpu_offload_executor()
    try:
        future = executor.submit(func_call)
    except RuntimeError as exc:
        # submit 拒绝按实例关闭标志与解释器退出标志归因转换，不匹配消息文案，
        # 标准库改文案时裸 RuntimeError 不击穿调用方的按类型兜底。
        if executor._shutdown or sys.is_finalizing():
            raise CpuOffloadPoolClosedError("CPU 卸载线程池已关闭，任务提交被拒绝") from exc
        raise
    task = asyncio.current_task()
    cancelling_before = task.cancelling() if task is not None else 0
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        # future 取消、池已退役且任务取消计数无增量时该取消只能来自池关闭，转
        # 专用异常供按类型兜底；await 期间到达的新取消请求照常传播 CancelledError。
        if (
            future.cancelled()
            and _executor_retired(executor)
            and (task is None or task.cancelling() == cancelling_before)
        ):
            raise CpuOffloadPoolClosedError("CPU 卸载线程池已关闭，排队任务被取消") from None
        raise
