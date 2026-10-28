"""InflightEntry 取消来源判别守护测试。

consume 经消费者专属 future 等待共享 task（测试内命名为 leader）完成回调的
转发：共享 task 以取消态终结而消费者未被取消时收 InflightTaskCancelledError，
不以取消态终结；消费者自身的取消原样传播并触发 on_cancel；两类取消同时到达
以消费者自身取消为准；leader 异常以独立副本投递各消费者，副本携带原始
traceback 且重抛只前置自身帧，复制不支持的类型回退共享对象；失败送达即置位
observed，Exception 与 BaseException 族均不误发孤儿日志；
外部注入的 BaseException 与转发异常身份不符
（含转发写入后、消费者恢复前的窗口期），
不置 observed，孤儿日志保留。
"""

import asyncio
import gc
import traceback
from typing import NoReturn

import pytest

from seedream_mcp.utils.core import inflight
from seedream_mcp.utils.core.inflight import InflightEntry, InflightTaskCancelledError
from seedream_mcp.utils.core.settle import await_settled_op


async def _pending_leader(started: asyncio.Event) -> "asyncio.Task[str]":
    """创建挂起不落的共享 task，置位 started 供调用方等待其进入执行。"""

    async def never_lands() -> str:
        started.set()
        await asyncio.sleep(3600)
        return "unreachable"

    return asyncio.get_running_loop().create_task(never_lands())


async def test_consume_leader_cancelled_degrades_to_error_for_uncancelled_waiter() -> None:
    """共享 task 被取消而消费者未被取消：消费者收类型化异常，不以取消态终结。"""
    started = asyncio.Event()
    leader = await _pending_leader(started)
    entry = InflightEntry(leader)
    on_cancel_calls: list[int] = []

    waiter = asyncio.ensure_future(entry.consume(on_cancel=lambda: on_cancel_calls.append(1)))
    await started.wait()
    leader.cancel()

    done, pending = await asyncio.wait({waiter})
    assert pending == set()
    assert not waiter.cancelled()
    assert isinstance(waiter.exception(), InflightTaskCancelledError)
    # on_cancel 只对本调用自身的取消负责，共享 task 取消不误触发。
    assert on_cancel_calls == []


async def test_consume_consumer_cancel_propagates_and_fires_on_cancel() -> None:
    """消费者自身被取消：on_cancel 执行、取消原样传播，共享 task 不受影响。"""
    started = asyncio.Event()
    leader = await _pending_leader(started)
    entry = InflightEntry(leader)
    on_cancel_calls: list[int] = []

    consumer = asyncio.ensure_future(entry.consume(on_cancel=lambda: on_cancel_calls.append(1)))
    await started.wait()
    consumer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert consumer.cancelled()
    assert on_cancel_calls == [1]
    assert not leader.done()

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader


async def test_consume_own_cancel_wins_when_leader_cancelled_too() -> None:
    """共享 task 取消与消费者自身取消同时到达：消费者取消原样传播，不误降级。"""
    started = asyncio.Event()
    leader = await _pending_leader(started)
    entry = InflightEntry(leader)
    on_cancel_calls: list[int] = []

    waiter = asyncio.ensure_future(entry.consume(on_cancel=lambda: on_cancel_calls.append(1)))
    await started.wait()
    assert not waiter.done()
    # 两类取消在同一让出前登记：消费者自身取消在途时，共享 task 取消不误降级。
    leader.cancel()
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert waiter.cancelled()
    assert on_cancel_calls == [1]


async def test_consume_own_cancel_wins_when_leader_exception_relayed_same_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """共享 task 异常已写入专属 future 后消费者取消同拍到达：仍以消费者取消为准。

    转发回调先于测试注入的取消回调登记，共享 task 完成批内异常先落 future，消费
    者尚未唤醒即收到取消；异常由转发回调取回，不产生未取回告警。
    """
    started = asyncio.Event()
    gate = asyncio.Event()

    async def _failing_leader() -> str:
        started.set()
        await gate.wait()
        raise RuntimeError("leader 落定失败")

    leader = asyncio.get_running_loop().create_task(_failing_leader())
    entry = InflightEntry(leader)
    on_cancel_calls: list[int] = []

    waiter = asyncio.ensure_future(entry.consume(on_cancel=lambda: on_cancel_calls.append(1)))
    await started.wait()
    # 转发回调随 consume 启动登记，其后追加的回调在同批完成序里晚于转发执行。
    leader.add_done_callback(lambda _task: waiter.cancel())
    gate.set()

    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert waiter.cancelled()
    assert on_cancel_calls == [1]

    # 异常是否真被取回以 GC 期告警判定，事先调用 exception() 会掩盖缺口。
    gc.collect()
    assert "exception was never retrieved" not in caplog.text


async def test_consume_degrades_after_consumer_absorbed_cancel_via_settle() -> None:
    """消费者经落定等待吸收过取消后再入 consume：cancelling 残留不参与来源判别。

    收尾路径的 await_settled_op 吸收取消后重抛原始异常，消费者捕获后继续执行；
    吸收的取消不 uncancel 平衡时 cancelling() 残留 1，consume 仍把共享 task 取消
    判为共享失败降级为类型化异常，不误判为本调用取消。
    """
    started = asyncio.Event()
    leader = await _pending_leader(started)
    entry = InflightEntry(leader)
    on_cancel_calls: list[int] = []
    cancelling_when_consuming: list[int] = []
    entered = asyncio.Event()
    landed = asyncio.Event()
    reached_consume = asyncio.Event()

    async def _gated_op() -> None:
        entered.set()
        await landed.wait()

    op_task = asyncio.ensure_future(_gated_op())

    async def _consume_after_absorb() -> None:
        try:
            try:
                raise RuntimeError("原始异常")
            finally:
                await await_settled_op(op_task)
        except RuntimeError:
            pass
        current = asyncio.current_task()
        assert current is not None
        cancelling_when_consuming.append(current.cancelling())
        reached_consume.set()
        await entry.consume(on_cancel=lambda: on_cancel_calls.append(1))

    waiter = asyncio.ensure_future(_consume_after_absorb())
    await entered.wait()
    waiter.cancel()
    landed.set()
    await reached_consume.wait()
    leader.cancel()

    done, pending = await asyncio.wait({waiter})
    assert pending == set()
    assert cancelling_when_consuming == [1]
    assert not waiter.cancelled()
    assert isinstance(waiter.exception(), InflightTaskCancelledError)
    # 残留计数不影响来源判别，共享 task 取消不误触发 on_cancel。
    assert on_cancel_calls == []


async def test_leader_failure_relayed_as_independent_clones() -> None:
    """leader 异常向各消费者投递独立副本：对象互不相同，raise 帧不交错进同一 traceback。"""

    async def _failing_leader() -> str:
        raise ValueError("leader 失败")

    leader = asyncio.get_running_loop().create_task(_failing_leader())
    entry = InflightEntry(leader)
    waiters = [asyncio.ensure_future(entry.consume()) for _ in range(3)]
    outcomes = await asyncio.gather(*waiters, return_exceptions=True)

    original = leader.exception()
    assert original is not None
    assert all(isinstance(outcome, ValueError) for outcome in outcomes)
    assert all(str(outcome) == "leader 失败" for outcome in outcomes)
    # 任一消费者都不收到共享对象本身，且各收到的副本互不相同
    assert all(outcome is not original for outcome in outcomes)
    assert len({id(outcome) for outcome in outcomes}) == len(outcomes)


class _BaseDerivedFailure(BaseException):
    """非 Exception 族失败替身，锁定 BaseException 族投递的 observed 契约。"""


@pytest.mark.parametrize(
    "error_type",
    [RuntimeError, _BaseDerivedFailure],
    ids=["exception-family", "base-exception-family"],
)
async def test_relayed_failure_marks_observed_across_exception_families(
    monkeypatch: pytest.MonkeyPatch, error_type: type[BaseException]
) -> None:
    """共享 task 失败送达消费者即置位 observed：Exception 与 BaseException 族均不误发孤儿日志。"""
    orphan_log_tasks: list["asyncio.Task[str]"] = []
    monkeypatch.setattr(inflight, "log_unretrieved_task_exception", orphan_log_tasks.append)

    async def _failing_leader() -> str:
        raise error_type("leader 失败")

    leader = asyncio.get_running_loop().create_task(_failing_leader())
    entry = InflightEntry(leader)
    with pytest.raises(error_type) as excinfo:
        await entry.consume()
    assert str(excinfo.value) == "leader 失败"
    assert entry.observed
    # observed 置位即不登记兜底回调，孤儿日志必然为空；让出一拍仅作调度兜底。
    await asyncio.sleep(0)
    assert orphan_log_tasks == []


async def test_injected_base_exception_preserves_orphan_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部注入的 BaseException 不置位 observed：共享 task 随后失败仍入孤儿日志。"""
    orphan_log_leaders: list["asyncio.Task[str]"] = []
    monkeypatch.setattr(inflight, "log_unretrieved_task_exception", orphan_log_leaders.append)

    gate = asyncio.Event()

    async def _failing_leader() -> str:
        await gate.wait()
        raise RuntimeError("leader 失败")

    leader = asyncio.get_running_loop().create_task(_failing_leader())
    entry = InflightEntry(leader)

    # 手动驱动 consume 挂起于专属 future 后向协程注入异常，模拟非 future 送达路径。
    consumer = entry.consume()
    consumer.send(None)
    with pytest.raises(KeyboardInterrupt):
        consumer.throw(KeyboardInterrupt)
    assert not entry.observed
    assert entry.consumers == 0

    gate.set()
    await asyncio.wait({leader})
    # 孤儿复查回调登记先于 wait 的完成回调，wait 返回即已入日志；让出一拍仅作调度兜底。
    await asyncio.sleep(0)
    assert orphan_log_leaders == [leader]


async def test_injected_base_exception_in_relay_window_preserves_orphan_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """转发异常已写入专属 future 后的窗口注入身份不符：不置 observed，仍入孤儿日志。"""
    orphan_log_leaders: list["asyncio.Task[str]"] = []
    monkeypatch.setattr(inflight, "log_unretrieved_task_exception", orphan_log_leaders.append)

    async def _failing_leader() -> str:
        raise RuntimeError("leader 失败")

    leader = asyncio.get_running_loop().create_task(_failing_leader())
    entry = InflightEntry(leader)

    # 手动驱动 consume 挂起于专属 future，待转发写入异常后、消费者恢复前注入。
    consumer = entry.consume()
    consumer.send(None)
    # 转发回调登记先于 wait 的完成回调，wait 返回即转发已写入专属 future。
    await asyncio.wait({leader})
    with pytest.raises(KeyboardInterrupt):
        consumer.throw(KeyboardInterrupt)
    # 注入异常不是转发写入的那个对象，专属 future 已完成也不置位。
    assert not entry.observed
    assert entry.consumers == 0

    # 让出一拍跑完 throw 后登记的孤儿复查回调，未送达的共享失败仍应入孤儿日志。
    await asyncio.sleep(0)
    assert orphan_log_leaders == [leader]


def _origin_failure_frame() -> NoReturn:
    """独立命名的根因抛出帧，供 traceback 可见性与互不污染断言定位。"""
    raise ValueError("leader 失败")


def _frame_names(exc: BaseException) -> list[str]:
    """返回异常 traceback 全链的帧名列表，供帧级断言。"""
    return [frame.name for frame in traceback.extract_tb(exc.__traceback__)]


async def test_clone_carries_original_traceback_without_cross_pollution() -> None:
    """副本继承原始 traceback：根因帧对各消费者可见；重抛只前置自身帧，原对象与其余副本不受污染。"""

    async def _failing_leader() -> str:
        _origin_failure_frame()

    leader = asyncio.get_running_loop().create_task(_failing_leader())
    entry = InflightEntry(leader)
    waiters = [asyncio.ensure_future(entry.consume()) for _ in range(2)]
    first, second = await asyncio.gather(*waiters, return_exceptions=True)

    original = leader.exception()
    assert original is not None
    assert isinstance(first, ValueError) and isinstance(second, ValueError)
    # 副本携带原始 traceback：根因帧在各副本链上可见。
    assert "_origin_failure_frame" in _frame_names(first)
    assert "_origin_failure_frame" in _frame_names(second)
    original_frames = _frame_names(original)

    def _reraise_first(exc: BaseException) -> None:
        raise exc

    with pytest.raises(ValueError):
        _reraise_first(first)

    # 重抛只向副本自身前置帧：原对象与其余副本的链不新增该帧。
    assert "_reraise_first" in _frame_names(first)
    assert "_reraise_first" not in _frame_names(second)
    assert _frame_names(original) == original_frames


async def test_non_copyable_leader_failure_falls_back_to_shared_object() -> None:
    """复制不支持的异常类型投递回退共享对象本身，真实失败不因副本机制被掩盖。"""

    class _NonCopyableError(RuntimeError):
        """copy.copy 即抛 TypeError 的替身，锁定投递回退共享对象。"""

        def __reduce__(self) -> NoReturn:
            raise TypeError("copy 不支持")

    async def _failing_leader() -> str:
        raise _NonCopyableError("不可复制失败")

    leader = asyncio.get_running_loop().create_task(_failing_leader())
    entry = InflightEntry(leader)
    waiters = [asyncio.ensure_future(entry.consume()) for _ in range(2)]
    outcomes = await asyncio.gather(*waiters, return_exceptions=True)

    original = leader.exception()
    assert original is not None
    assert all(outcome is original for outcome in outcomes)
    assert all(isinstance(outcome, _NonCopyableError) for outcome in outcomes)
