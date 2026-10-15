"""settle 落定原语守护测试：取消只打断 await 层，脱离的 op 照常执行完，
等待其落定后放行取消，失败异常被取回，取消后落地的结果交善后回调；
收尾路径的原始异常不被等待期取消顶替，重复取消的吸收有界、放行不跳过善后，
在途逃逸的善后经落定回调延迟执行恰一次、失败异常经取回回调兜底；无条件档吸收任意次重复取消，
善后就地执行恰一次。"""

import asyncio
import gc
import os
import threading
from pathlib import Path

import pytest

from seedream_mcp.utils.core.settle import await_settled_op, settle_detached_op
from seedream_mcp.utils.io.files import open_temp_fd

from _os_fakes import _install_close_counter


async def test_settle_detached_op_absorbs_cancellation_and_retrieves_failure() -> None:
    """settle 等待脱离线程 future 落定：等待中的取消被吸收，失败异常被取回不上抛。"""
    release = threading.Event()

    def slow_failure() -> None:
        assert release.wait(5.0), "放行事件未触发"
        raise OSError("detached boom")

    future = asyncio.ensure_future(asyncio.to_thread(slow_failure))
    settle_task = asyncio.ensure_future(settle_detached_op(future))
    await asyncio.sleep(0.05)
    settle_task.cancel()
    release.set()
    await settle_task

    assert future.done()
    assert isinstance(future.exception(), OSError)


async def test_settle_detached_op_retrieves_already_failed_future(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """op 在 settle 前已失败完成：入口即落定分支同样取回异常，不遗留未取回告警。"""

    def _boom() -> None:
        raise OSError("already failed")

    future = asyncio.ensure_future(asyncio.to_thread(_boom))
    while not future.done():
        await asyncio.sleep(0.01)
    await settle_detached_op(future)

    # 异常是否真被取回以 GC 期告警判定，事先调用 exception() 会掩盖缺口。
    del future
    gc.collect()
    assert "exception was never retrieved" not in caplog.text


async def test_await_settled_op_returns_result() -> None:
    """正常路径透传线程 op 的结果。"""
    assert await await_settled_op(asyncio.to_thread(os.getpid)) == os.getpid()


async def test_await_settled_op_cancel_waits_then_disposes_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消等待已提交的 op：脱离线程照常执行，落地结果交善后回调后再放行取消。"""
    opened: list[tuple[int, Path]] = []
    landed = threading.Event()
    release = threading.Event()
    closed = _install_close_counter(monkeypatch)

    def _open_worker() -> tuple[int, Path]:
        opened.append(open_temp_fd(tmp_path))
        landed.set()
        assert release.wait(5.0), "放行事件未触发"
        return opened[0]

    def _dispose_opened(result: tuple[int, Path]) -> None:
        os.close(result[0])
        result[1].unlink(missing_ok=True)

    async def _call() -> tuple[int, Path]:
        return await await_settled_op(
            asyncio.to_thread(_open_worker), dispose_on_cancel=_dispose_opened
        )

    task = asyncio.ensure_future(_call())
    assert await asyncio.to_thread(landed.wait, 5.0), "op 线程未进入执行"
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 落地的临时 fd 经善后回调关闭恰一次，临时文件无残留。
    assert opened
    assert closed == [opened[0][0]]
    assert list(tmp_path.iterdir()) == []


async def test_await_settled_op_repeated_cancel_still_disposes_landed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """op 落定同拍送达的重复取消中断等待：落定结果仍交善后回调，取消随后放行。"""
    opened: list[tuple[int, Path]] = []
    opened_evt = threading.Event()
    release = threading.Event()
    closed = _install_close_counter(monkeypatch)

    def _open_worker() -> tuple[int, Path]:
        opened.append(open_temp_fd(tmp_path))
        opened_evt.set()
        assert release.wait(5.0), "放行事件未触发"
        return opened[0]

    def _dispose_opened(result: tuple[int, Path]) -> None:
        os.close(result[0])
        result[1].unlink(missing_ok=True)

    op_task = asyncio.ensure_future(asyncio.to_thread(_open_worker))

    async def _call() -> tuple[int, Path]:
        return await await_settled_op(op_task, dispose_on_cancel=_dispose_opened)

    task = asyncio.ensure_future(_call())
    assert await asyncio.to_thread(opened_evt.wait, 5.0), "op 线程未进入执行"
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.05)
    # 取消三注册进 op 落定回调序：future 落定与重复取消同拍送达落定等待。
    op_task.add_done_callback(lambda _future: task.cancel())
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 落定的临时 fd 经善后回调关闭恰一次，取消仍放行，临时文件无残留。
    assert op_task.done() and not op_task.cancelled() and op_task.exception() is None
    assert opened
    assert closed == [opened[0][0]]
    assert list(tmp_path.iterdir()) == []


async def test_await_settled_op_repeated_cancel_in_flight_defers_dispose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重复取消在 op 仍在途时打断落定等待：立即放行取消，op 后落定的裸 fd 经落定回调善后恰一次。"""
    opened: list[tuple[int, Path]] = []
    opened_evt = threading.Event()
    release = threading.Event()
    closed = _install_close_counter(monkeypatch)

    def _open_worker() -> tuple[int, Path]:
        opened.append(open_temp_fd(tmp_path))
        opened_evt.set()
        assert release.wait(5.0), "放行事件未触发"
        return opened[0]

    def _dispose_opened(result: tuple[int, Path]) -> None:
        os.close(result[0])
        result[1].unlink(missing_ok=True)

    op_task = asyncio.ensure_future(asyncio.to_thread(_open_worker))

    async def _call() -> tuple[int, Path]:
        return await await_settled_op(op_task, dispose_on_cancel=_dispose_opened)

    task = asyncio.ensure_future(_call())
    assert await asyncio.to_thread(opened_evt.wait, 5.0), "op 线程未进入执行"
    task.cancel()  # 取消一：打断外层 shield 进入落定等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消二：落定等待吸收首个取消继续等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消三：打断落定等待，此刻 op 仍在途。
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled(), "放行不等 op 落定，任务保持可取消"
    assert not op_task.done(), "前置：取消三打断等待时 op 仍在途"

    release.set()  # op 此刻才成功落定，裸 fd 结果只能经落定回调善后。
    while not op_task.done():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # 落定回调经 call_soon 排队，让出一拍执行。

    assert op_task.done() and not op_task.cancelled() and op_task.exception() is None
    assert opened
    assert closed == [opened[0][0]]
    assert list(tmp_path.iterdir()) == []


async def test_await_settled_op_repeated_cancel_escape_retrieves_late_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """无善后回调的调用点三连取消后在途逃逸：op 随后以异常落定经取回回调消费，无未取回告警。"""
    entered = threading.Event()
    release = threading.Event()

    def _late_failure() -> None:
        entered.set()
        assert release.wait(5.0), "放行事件未触发"
        raise OSError("escape boom")

    op_task = asyncio.ensure_future(asyncio.to_thread(_late_failure))

    async def _call(op: asyncio.Future[None]) -> None:
        await await_settled_op(op)

    task = asyncio.ensure_future(_call(op_task))
    assert await asyncio.to_thread(entered.wait, 5.0), "op 线程未进入执行"
    task.cancel()  # 取消一：打断外层 shield 进入落定等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消二：落定等待吸收首个取消继续等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消三：op 仍在途即打断落定等待放行。
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled(), "放行不等 op 落定，任务保持可取消"
    assert not op_task.done(), "前置：取消三打断等待时 op 仍在途"

    release.set()  # op 此刻才以异常落定，异常只能经取回回调消费。
    while not op_task.done():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # 取回回调经 call_soon 排队，让出一拍执行。

    # 异常是否真被取回以 GC 期告警判定，事先调用 exception() 会掩盖缺口。
    del task, op_task
    gc.collect()
    assert "exception was never retrieved" not in caplog.text


async def test_await_settled_op_cancel_does_not_displace_in_flight_error() -> None:
    """finally 收尾路径上等待期间到达的取消降级吸收：原始异常优先传播，不被取消顶替。"""
    entered = threading.Event()
    release = threading.Event()

    def _slow_success() -> str:
        entered.set()
        assert release.wait(5.0), "放行事件未触发"
        return "landed"

    async def _cleanup_with_error() -> None:
        try:
            raise RuntimeError("原始异常")
        finally:
            await await_settled_op(asyncio.to_thread(_slow_success))

    task = asyncio.ensure_future(_cleanup_with_error())
    assert await asyncio.to_thread(entered.wait, 5.0), "op 线程未进入执行"
    task.cancel()
    release.set()
    with pytest.raises(RuntimeError, match="原始异常"):
        await task
    assert not task.cancelled(), "等待期取消已降级，任务以原始异常收尾"


async def test_settle_detached_op_repeated_cancel_is_bounded() -> None:
    """settle 等待只吸收首个取消：重复取消中断等待放行，脱离 op 照常执行完。"""
    hold = asyncio.Event()
    settled = asyncio.Event()

    async def _hanging_op() -> None:
        await hold.wait()
        settled.set()

    future = asyncio.ensure_future(_hanging_op())
    settle_task = asyncio.ensure_future(settle_detached_op(future))
    await asyncio.sleep(0.05)
    settle_task.cancel()
    await asyncio.sleep(0.05)
    assert not settle_task.done(), "首个取消应被吸收，继续等待落定"

    settle_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await settle_task
    assert settle_task.cancelled(), "重复取消须中断等待放行，任务保持可取消"

    hold.set()
    assert await asyncio.wait_for(settled.wait(), timeout=5), "脱离 op 未照常执行完"


async def test_await_settled_op_unconditionally_returns_result() -> None:
    """无条件档正常路径透传线程 op 的结果。"""
    assert (
        await await_settled_op(asyncio.to_thread(os.getpid), absorb_all_cancels=True) == os.getpid()
    )


async def test_await_settled_op_unconditionally_absorbs_repeated_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无条件落定等待吸收任意次重复取消：op 落定并善后后才放行取消，不提前逃逸。"""
    opened: list[tuple[int, Path]] = []
    opened_evt = threading.Event()
    release = threading.Event()
    closed = _install_close_counter(monkeypatch)

    def _open_worker() -> tuple[int, Path]:
        opened.append(open_temp_fd(tmp_path))
        opened_evt.set()
        assert release.wait(5.0), "放行事件未触发"
        return opened[0]

    def _dispose_opened(result: tuple[int, Path]) -> None:
        os.close(result[0])
        result[1].unlink(missing_ok=True)

    op_task = asyncio.ensure_future(asyncio.to_thread(_open_worker))

    async def _call() -> tuple[int, Path]:
        return await await_settled_op(
            op_task, dispose_on_cancel=_dispose_opened, absorb_all_cancels=True
        )

    task = asyncio.ensure_future(_call())
    assert await asyncio.to_thread(opened_evt.wait, 5.0), "op 线程未进入执行"
    task.cancel()  # 取消一：打断外层 shield 进入落定等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消二：落定等待吸收后继续等待。
    await asyncio.sleep(0.05)
    task.cancel()  # 取消三：仍被吸收，放行以 op 落定为界。
    await asyncio.sleep(0.05)
    assert not task.done(), "重复取消不提前放行，落定等待保持"
    assert not op_task.done(), "前置：取消全部送达时 op 仍在途"

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    # op 落定后结果经善后回调关闭恰一次，取消随后放行，临时文件无残留。
    assert op_task.done() and not op_task.cancelled() and op_task.exception() is None
    assert opened
    assert closed == [opened[0][0]]
    assert list(tmp_path.iterdir()) == []


async def test_await_settled_op_unconditionally_retrieves_late_failure() -> None:
    """取消后在途失败的 op 异常经落定等待取回不上抛，取消照常放行。"""
    release = threading.Event()

    def _late_failure() -> None:
        assert release.wait(5.0), "放行事件未触发"
        raise OSError("late boom")

    task = asyncio.ensure_future(
        await_settled_op(asyncio.to_thread(_late_failure), absorb_all_cancels=True)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_await_settled_op_unconditionally_keeps_in_flight_error_first() -> None:
    """收尾路径上的等待期取消被尽数吸收：原始异常优先传播，不被取消顶替。"""
    entered = threading.Event()
    release = threading.Event()

    def _slow_success() -> str:
        entered.set()
        assert release.wait(5.0), "放行事件未触发"
        return "landed"

    async def _cleanup_with_error() -> None:
        try:
            raise RuntimeError("原始异常")
        finally:
            await await_settled_op(asyncio.to_thread(_slow_success), absorb_all_cancels=True)

    task = asyncio.ensure_future(_cleanup_with_error())
    assert await asyncio.to_thread(entered.wait, 5.0), "op 线程未进入执行"
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()
    release.set()
    with pytest.raises(RuntimeError, match="原始异常"):
        await task
    assert not task.cancelled(), "等待期取消已吸收，任务以原始异常收尾"
