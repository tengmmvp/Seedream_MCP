"""asyncio 共享后台任务的可观测性设施。

InflightEntry 提供单飞在途条目与孤儿异常登记判定，经 loop_bound 的
LoopKeyedInflight 供 download 的 DNS 在途解析、prepare 的参考图预处理
与 thumbnail 的缩略图解码在途去重间接共用。消费者全部放弃且结果
未被消费时，失败 task 经 arm_unretrieved_exception_logging 登记的兜底回调以
warning 记录。TransientInflightError 为共享在途瞬态失败标记，
clone_shared_exception 为共享异常对象的独立投递副本构造助手，供本模块
投递点与计划层重放共用。
"""

from __future__ import annotations

import asyncio
import copy
import weakref
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from loguru import logger


def log_unretrieved_task_exception(task: "asyncio.Task[Any]") -> None:
    """检索并记录共享后台 task 的异常，消除事件循环告警噪音。

    共享 task 的消费者全部放弃且结果未被消费时，失败无人检索，事件循环会告警
    "Task exception was never retrieved"；本回调显式检索异常即清除该标记并以
    warning 记录孤儿失败。task 成功或被取消时检索无异常，静默无害。

    Args:
        task: 可能不再有等待者消费结果的在途后台 task。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.opt(exception=exc).warning("后台共享任务失败: {}", exc)


# 已登记「未取回异常」检索回调的 task 集合。WeakSet 持弱引用，不延长 task 生命
# 周期；同一在途 task 仅登记一次，已 done 的重复登记仍重排回调以复查孤儿前提。
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
    # 已 done 的 task 此前回调可能已跑过且复查未成立，登记去重仅对在途 task 生效。
    if not task.done() and task in _UNRETRIEVED_ARMED_TASKS:
        return
    _UNRETRIEVED_ARMED_TASKS.add(task)
    resolved_callback = callback if callback is not None else log_unretrieved_task_exception
    task.add_done_callback(resolved_callback)


# 单飞在途条目持有的共享 task 结果类型。
_InflightResultT = TypeVar("_InflightResultT")


class TransientInflightError(Exception):
    """共享在途瞬态失败的标记基类：异常在源头混入本类声明自身属外部瞬态，
    继承 Exception 使消费点可直接以 except 子句捕获。

    所有权契约：瞬态的生产者拥有有界重试并上抛终态类型化错误（如
    PrepareInflightExhaustedError）；未被吸收而直达计划层的标记瞬态不冻结、
    放行同键重建，为未来瞬态留安全网。计划层与消费方检查本标记而非枚举
    异常类型，新瞬态类型在源头混入即被覆盖。
    """


class InflightTaskCancelledError(TransientInflightError, RuntimeError):
    """共享 task 被取消而消费者未被取消时 consume 的降级异常，供调用方按类型兜底。"""


def clone_shared_exception(cached: BaseException) -> BaseException:
    """构造共享异常对象的独立投递副本：复制不支持的类型回退原对象本身。

    重抛共享对象会向其 __traceback__ 追加帧，多个接收方交错 raise 使帧互相
    污染；副本继承原始 traceback 使根因帧可见，重抛只前置自身帧，共享冻结
    尾部不互染；cause/context 补齐 copy.copy 不复制的部分；构造参数不能经
    构造器往返的异常类型（如 httpx.HTTPStatusError）复制即抛 TypeError，
    回退共享对象本身，真实失败不因副本机制被掩盖。
    """
    try:
        clone = copy.copy(cached)
    except Exception:
        return cached
    # 赋值 __cause__ 会隐式置 __suppress_context__，还原其值须在后。
    clone.__cause__ = cached.__cause__
    clone.__context__ = cached.__context__
    clone.__suppress_context__ = cached.__suppress_context__
    # copy.copy 不复制 __traceback__，显式继承。
    clone.__traceback__ = cached.__traceback__
    return clone


class InflightEntry(Generic[_InflightResultT]):
    """单飞共享在途条目：共享 task、活动消费者计数与结果消费标记。

    消费者经 consume 以专属 future 等待共享 task 完成回调的转发：本调用被取消
    时仅自身 future 被取消，底层 task 继续运行保护其他等待者；共享 task 以取消
    态终结时回调向消费者 future 写入 InflightTaskCancelledError，取消来源经
    等待载体天然区分；共享 task 的异常经 clone_shared_exception 以独立副本
    投递各消费者，traceback 不因多等待者交错 raise 互相污染；结果或异常送达
    任一消费者即置位 observed，此后计数归零不再登记兜底日志；全部消费者放弃
    且结果未被消费时经 arm_orphan_logging 登记兜底日志回调，同一孤儿失败至多
    记录一次。on_cancel 仅在本调用自身被取消时于取消异常向外传播前执行，
    供创建者转移信号量槽位等清理责任。
    """

    # __weakref__ 槽使条目可被弱引用，供 loop_bound 注册表仅弱持在途条目。
    __slots__ = ("task", "consumers", "observed", "orphan_logged", "__weakref__")

    def __init__(self, task: "asyncio.Task[_InflightResultT]") -> None:
        self.task = task
        self.consumers = 0
        self.observed = False
        self.orphan_logged = False

    async def consume(self, on_cancel: Callable[[], None] | None = None) -> _InflightResultT:
        """以消费者身份等待共享 task，返回其结果或向调用方重放其异常。

        消费者计数在 await 期间登记、finally 释放；等待经专属 future 完成，
        共享 task 的完成回调把结果或异常写入该 future，本调用自身被取消时在
        取消异常向外传播前执行 on_cancel；共享 task 以取消态终结时 future 收到
        InflightTaskCancelledError，本调用不以取消态终结；结果与异常送达均置位
        observed，计数归零且结果未被消费时经 arm_orphan_logging 登记兜底日志回调。
        """
        self.consumers += 1
        waiter: asyncio.Future[_InflightResultT] = asyncio.Future()

        def _relay(finished: "asyncio.Task[_InflightResultT]") -> None:
            # 消费者已放弃时 future 处于终态，写入跳过。
            if waiter.done():
                return
            if finished.cancelled():
                # cancelled task 的 exception() 会抛取消异常，先分流转类型化降级。
                waiter.set_exception(InflightTaskCancelledError("共享在途任务已被取消"))
                return
            exc = finished.exception()
            if exc is not None:
                # 每次投递独立副本，多等待者的 raise 帧不交错进同一 __traceback__。
                waiter.set_exception(clone_shared_exception(exc))
            else:
                waiter.set_result(finished.result())

        try:
            # 登记入 try：登记抛异常时 finally 仍释放计数，对未登记回调的移除为无害空操作。
            self.task.add_done_callback(_relay)
            try:
                result = await waiter
            except asyncio.CancelledError:
                # 专属 future 只会因本调用的取消进入取消态，来源无歧义。
                if on_cancel is not None:
                    on_cancel()
                raise
            except Exception:
                # 共享 task 的异常（含取消态终结降级的 InflightTaskCancelledError）
                # 经专属 future 送达本消费者，置位已消费标记，不再登记兜底日志。
                self.observed = True
                raise
            self.observed = True
            return result
        finally:
            # 消费者退出即摘除转发回调，不长占共享 task 的回调列表；task 已 done
            # 时回调已消费，未命中移除为无害空操作。
            self.task.remove_done_callback(_relay)
            self.consumers -= 1
            self.arm_orphan_logging()

    def arm_orphan_logging(self) -> None:
        """最后一个潜在消费者放弃且结果未被消费时，登记兜底日志回调。

        回调触发时复查登记前提：登记后新消费者加入又放弃或消费的窗口内前提可能
        已不成立，复查不成立即静默跳过；复查成立的重复触发经已记录标记去重，
        同一孤儿失败至多入日志一次。
        """

        def _log_if_orphaned(task: "asyncio.Task[_InflightResultT]") -> None:
            # 触发时经模块全局名解析通用记录函数，保持调用方可替换该实现做测试观测。
            if self.consumers == 0 and not self.observed and not self.orphan_logged:
                self.orphan_logged = True
                log_unretrieved_task_exception(task)

        if self.consumers > 0 or self.observed:
            return
        arm_unretrieved_exception_logging(self.task, _log_if_orphaned)
