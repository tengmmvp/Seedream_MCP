"""asyncio 共享后台任务的可观测性设施。

InflightEntry 提供 single-flight 在途条目与孤儿异常登记判定，供 io_download 的
DNS 在途解析与 image_prepare 的参考图预处理在途去重共用。消费者全部放弃且结果
未被消费时，失败 task 经 arm_unretrieved_exception_logging 登记的兜底回调以
warning 记录。
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from loguru import logger


def log_unretrieved_task_exception(task: "asyncio.Task[Any]") -> None:
    """检索并记录共享后台 task 的异常，消除事件循环告警噪音。

    经 asyncio.shield 共享的 task 在消费方被取消后可能无人消费结果，失败时事件循环
    会告警 "Task exception was never retrieved"；本回调显式检索异常即清除该标记并以
    warning 记录孤儿失败。task 成功或被取消时检索无异常，静默无害。

    Args:
        task: 可能不再有等待者消费结果的在途后台任务。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.opt(exception=exc).warning("后台共享任务失败: {}", exc)


# 已登记「未取回异常」检索回调的 task 集合。WeakSet 持弱引用，不延长 task 生命
# 周期；创建者与等待者相继放弃同一 task 时仅登记一次。
_UNRETRIEVED_ARMED_TASKS: "weakref.WeakSet[asyncio.Task[Any]]" = weakref.WeakSet()


def arm_unretrieved_exception_logging(
    task: "asyncio.Task[Any]",
    callback: Callable[["asyncio.Task[Any]"], None] | None = None,
) -> None:
    """为共享 task 登记「未取回异常」检索回调，供消费方放弃等待时调用。

    仅最后一个潜在消费者放弃且结果未被接收时调用本函数，此后 task 失败将无人检索
    异常，回调兜底检索并记录；异常已送抵消费者的场景不登记，避免重复入日志。

    Args:
        task: 消费方已放弃等待的共享后台任务。
        callback: 触发时执行的回调，缺省为 log_unretrieved_task_exception；调用方
            持有消费者计数等附加上下文时可传自定义回调，在触发时复查登记前提。
    """
    if task in _UNRETRIEVED_ARMED_TASKS:
        return
    _UNRETRIEVED_ARMED_TASKS.add(task)
    resolved_callback = callback if callback is not None else log_unretrieved_task_exception
    task.add_done_callback(resolved_callback)


# single-flight 在途条目持有的共享 task 结果类型。
_InflightResultT = TypeVar("_InflightResultT")


class InflightEntry(Generic[_InflightResultT]):
    """single-flight 共享在途条目：共享 task、活动消费者计数与结果消费标记。

    消费者经 consume 以 shield 等待共享 task：本调用被取消时仅取消自身 await，
    底层 task 继续运行保护其他等待者；结果或异常送达任一消费者即置位 observed，
    此后计数归零不再登记兜底日志；全部消费者放弃且结果未被消费时经
    arm_orphan_logging 登记兜底日志回调。on_cancel 在取消异常向外传播前执行，
    供创建者转移信号量槽位等清理责任。
    """

    __slots__ = ("task", "consumers", "observed")

    def __init__(self, task: "asyncio.Task[_InflightResultT]") -> None:
        self.task = task
        self.consumers = 0
        self.observed = False

    async def consume(self, on_cancel: Callable[[], None] | None = None) -> _InflightResultT:
        """以消费者身份等待共享 task，返回其结果或向调用方重放其异常。

        消费者计数在 await 期间登记、finally 释放；取消异常向外传播前执行
        on_cancel；结果与异常送达均置位 observed，计数归零且结果未被消费时经
        arm_orphan_logging 登记兜底日志回调。
        """
        self.consumers += 1
        try:
            try:
                result = await asyncio.shield(self.task)
            except asyncio.CancelledError:
                if on_cancel is not None:
                    on_cancel()
                raise
            except Exception:
                # 异常经 shield 送达本消费者，置位已消费标记，不再登记兜底日志。
                self.observed = True
                raise
            self.observed = True
            return result
        finally:
            self.consumers -= 1
            self.arm_orphan_logging()

    def arm_orphan_logging(self) -> None:
        """最后一个潜在消费者放弃且结果未被消费时，登记兜底日志回调。

        回调触发时复查登记前提：登记后新消费者加入又放弃或消费的窗口内前提可能
        已不成立，复查不成立即静默跳过，避免同一异常重复入日志。
        """

        def _log_if_orphaned(task: "asyncio.Task[_InflightResultT]") -> None:
            # 触发时经模块全局名解析通用记录函数，保持调用方可替换该实现做测试观测。
            if self.consumers == 0 and not self.observed:
                log_unretrieved_task_exception(task)

        if self.consumers > 0 or self.observed:
            return
        arm_unretrieved_exception_logging(self.task, _log_if_orphaned)
