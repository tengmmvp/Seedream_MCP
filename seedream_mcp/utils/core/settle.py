"""取消安全的落定原语：等待脱离等待层的 op 落定后再放行取消。

ensure_future 加 shield 的惯用法散落各处会漂移出不同的取消语义，收敛为
core 层单一实现，供文件 IO 的线程卸载与传输层的脱离任务发送共用。落定等待
对重复取消分有界吸收与无条件吸收两档，无条件档要求 op 保证落定。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

_T = TypeVar("_T")


async def settle_detached_op(
    future: asyncio.Future[Any], *, absorb_all_cancels: bool = False
) -> None:
    """等待因取消而脱离等待层的 future 落定，不向调用方上抛其结果或异常。

    取消只打断 await 层，已提交的脱离 op 照常执行完；提前返回会使后续
    清理与脱离的 op 竞争，失败异常也在此取回避免未取回告警——入口已落定
    或等待期间落定的失败 op 同样经 await 取回。absorb_all_cancels 为真时
    吸收等待期间到达的任意次取消，否则仅吸收首个取消以维持落定等待、重复
    取消中断等待放行，脱离 op 无界挂起时任务不因此不可取消。
    """
    absorbed = False
    while True:
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.cancelled():
                # future 自身被取消已属落定，无异常可取。
                return
            if not absorb_all_cancels:
                if absorbed:
                    # 重复取消中断等待：落定等待不得吸收任意多次取消使任务不可取消。
                    raise
                absorbed = True
            continue
        except BaseException:
            # 操作失败的异常经 await 已取回。
            return
        return


async def await_settled_op(
    op: Awaitable[_T],
    *,
    dispose_on_cancel: Callable[[_T], None] | None = None,
    absorb_all_cancels: bool = False,
) -> _T:
    """等待执行器卸载或脱离任务的 op 返回结果；取消时先等待脱离的 op 落定再放行取消。

    正常路径透传结果与异常；取消路径等待脱离的 op 落定后重抛 CancelledError，
    调用方的资源清理不与在途 op 竞争；op 在取消后成功落地时其结果交
    dispose_on_cancel 善后（如关闭 fd），防资源随取消泄漏。
    absorb_all_cancels 为真时落定等待吸收任意次重复取消，前置条件为 op
    保证落定（有界系统调用的线程卸载或必达发送）；fd 等资源的关闭善后依赖
    在途 op 先落定的调用点必须用该档，防重复取消放行后关闭与在途截断、
    写回竞争同号 fd。默认有界档重复取消中断落定等待后立即放行保持可取消，
    op 未落定时的善后转由落定回调延迟执行恰一次；入口处在飞异常的收尾路径
    （finally/except 清理）上，等待期间到达的取消降级吸收，原始异常优先
    传播不被取消顶替。
    """
    # 入口同步取在飞异常：脱离 op 的落定等待不得以取消顶替收尾中的原始异常。
    in_flight = sys.exception()
    future = asyncio.ensure_future(op)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        try:
            await settle_detached_op(future, absorb_all_cancels=absorb_all_cancels)
        except asyncio.CancelledError:
            # 有界档重复取消中断落定等待：落定结果的善后不因提前放行被跳过。
            pass
        if future.done():
            if (
                dispose_on_cancel is not None
                and not future.cancelled()
                and future.exception() is None
            ):
                dispose_on_cancel(future.result())
        elif dispose_on_cancel is not None:
            # op 仍在途即放行取消：善后转由落定回调延迟执行，与同步路径互斥。
            def _dispose_when_landed(landed: asyncio.Future[_T]) -> None:
                # 成功落地时经回调 dispose 恰一次，失败异常顺手取回免告警。
                if not landed.cancelled() and landed.exception() is None:
                    dispose_on_cancel(landed.result())

            future.add_done_callback(_dispose_when_landed)
        if in_flight is not None:
            # 取消已被落定等待吸收，重抛在飞原始异常使外层错误处理不被顶替。
            raise in_flight
        raise
