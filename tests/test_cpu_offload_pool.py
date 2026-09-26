"""长时 CPU 卸载专用线程池测试：池间隔离、跨循环共享单池、上下文传播、池关闭
后的提交拒绝转换、池关闭与协程取消的归因语义、池深经提供者的注册与未注册两
形态推导。"""

from __future__ import annotations

import asyncio
import contextvars
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from _cpu_offload_spy import saturate_cpu_offload_pool
from seedream_mcp.utils.core.executors import (
    CPU_OFFLOAD_THREAD_PREFIX,
    CpuOffloadPoolClosedError,
    cpu_offload_executor,
    run_in_cpu_pool,
    shutdown_cpu_offload_executor,
)


async def test_cpu_pool_threads_distinct_from_default_executor() -> None:
    """长时卸载运行在专用池线程，与默认执行器线程不重叠。"""
    cpu_thread = await run_in_cpu_pool(threading.current_thread)
    default_thread = await asyncio.to_thread(threading.current_thread)

    assert cpu_thread.name.startswith(CPU_OFFLOAD_THREAD_PREFIX)
    assert not default_thread.name.startswith(CPU_OFFLOAD_THREAD_PREFIX)


async def test_cpu_pool_saturation_keeps_default_executor_responsive() -> None:
    """长任务占满专用池时默认池短任务即时完成，追加长任务仍在专用池排队。"""
    async with saturate_cpu_offload_pool() as saturated:

        async def _short_task() -> str:
            return await asyncio.to_thread(lambda: "default-pool")

        assert await asyncio.wait_for(_short_task(), timeout=2) == "default-pool"

        extra = asyncio.ensure_future(run_in_cpu_pool(lambda: "queued"))
        # 留出调度窗口：若长任务可插队默认池空闲线程，此处已完成。
        await asyncio.sleep(0.1)
        assert not extra.done()

    results = await asyncio.wait_for(asyncio.gather(*saturated.tasks, extra), timeout=5)
    assert results == [None] * len(saturated.tasks) + ["queued"]


def _double(value: int) -> int:
    return value * 2


def test_cpu_executor_single_shared_pool_across_loops() -> None:
    """不同事件循环取到同一执行器实例，进程内全程仅一个共享池。"""

    async def _current() -> ThreadPoolExecutor:
        return cpu_offload_executor()

    with asyncio.Runner() as first_runner:
        first = first_runner.run(_current())
        assert first_runner.run(_current()) is first
    with asyncio.Runner() as second_runner:
        second = second_runner.run(_current())

    assert second is first


def test_concurrent_submissions_from_two_active_loops_never_fail() -> None:
    """双活跃循环并发提交全部成功，共享单池不因循环交替逐出关闭。"""
    submit_count = 500
    barrier = threading.Barrier(2, timeout=10)
    errors: list[BaseException] = []
    executors_seen: list[ThreadPoolExecutor] = []

    async def _submit_batch() -> None:
        executors_seen.append(cpu_offload_executor())
        results = await asyncio.gather(*(run_in_cpu_pool(_double, i) for i in range(submit_count)))
        assert results == [i * 2 for i in range(submit_count)]

    def _run_second_loop() -> None:
        try:
            barrier.wait()
            asyncio.run(_submit_batch())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=_run_second_loop)
    worker.start()
    try:
        barrier.wait()
        with asyncio.Runner() as main_runner:
            main_runner.run(_submit_batch())
    finally:
        worker.join(timeout=10)

    # join 超时静默返回时 errors 的 append 可能落在断言之后，挂死须在此响亮失败。
    assert not worker.is_alive(), "第二个事件循环线程 join 超时未退出"
    assert not errors
    assert len(executors_seen) == 2
    assert executors_seen[0] is executors_seen[1]


async def test_run_in_cpu_pool_propagates_context() -> None:
    """调用点上下文变量在专用池线程内可见，语义与 asyncio.to_thread 一致。"""
    marker: contextvars.ContextVar[str | None] = contextvars.ContextVar(
        "cpu-pool-context-test", default=None
    )
    token = marker.set("carried")
    try:
        assert await run_in_cpu_pool(marker.get) == "carried"
    finally:
        marker.reset(token)


async def test_pool_shutdown_degrades_queued_cancellation_to_runtime_error() -> None:
    """池关闭取消排队任务转专用异常，await 可被 except Exception 降级守护捕获。"""
    async with saturate_cpu_offload_pool() as saturated:
        queued = asyncio.ensure_future(run_in_cpu_pool(_double, 21))
        await asyncio.sleep(0.1)
        assert not queued.done()

        shutdown_cpu_offload_executor()

        caught: BaseException | None = None
        try:
            await queued
        except Exception as exc:
            caught = exc
        assert isinstance(caught, RuntimeError)
        assert isinstance(caught, CpuOffloadPoolClosedError)
        assert "已关闭" in str(caught)

    assert await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5) == [None] * len(
        saturated.tasks
    )


async def test_task_cancellation_with_queued_work_propagates_cancelled_error() -> None:
    """排队任务未启动时协程自身取消传播 CancelledError，不转 RuntimeError。"""
    async with saturate_cpu_offload_pool() as saturated:
        queued = asyncio.ensure_future(run_in_cpu_pool(_double, 21))
        await asyncio.sleep(0.1)
        assert not queued.done()

        queued.cancel()

        with pytest.raises(asyncio.CancelledError):
            await queued

    assert await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5) == [None] * len(
        saturated.tasks
    )


async def test_task_cancellation_with_running_work_propagates_cancelled_error() -> None:
    """任务运行中协程自身取消传播 CancelledError，不转 RuntimeError。"""
    release = threading.Event()
    started = threading.Event()

    def _blocker() -> int:
        started.set()
        assert release.wait(timeout=10)
        return 42

    subject = asyncio.ensure_future(run_in_cpu_pool(_blocker))
    assert await asyncio.to_thread(started.wait, 5)
    try:
        subject.cancel()

        with pytest.raises(asyncio.CancelledError):
            await subject
    finally:
        release.set()


async def test_absorbed_cancel_then_pool_shutdown_attributes_to_pool_closure() -> None:
    """吸收过一次取消的任务遭关池取消排队任务时归因池关闭，不误判为自身取消。

    吸收 CancelledError 后未 uncancel 使 cancelling 恒不小于 1，按取消计数归因
    会把关池误判为自身取消，裸 CancelledError 击穿 except Exception 降级守护。
    """
    async with saturate_cpu_offload_pool() as saturated:
        offload_submitted = asyncio.Event()

        async def _absorb_then_offload() -> int:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            offload_submitted.set()
            return await run_in_cpu_pool(_double, 21)

        subject = asyncio.ensure_future(_absorb_then_offload())
        await asyncio.sleep(0.05)
        subject.cancel()
        await asyncio.wait_for(offload_submitted.wait(), timeout=5)
        assert subject.cancelling() >= 1
        await saturated.wait_queued()
        assert not subject.done()

        shutdown_cpu_offload_executor()

        caught: BaseException | None = None
        try:
            await subject
        except BaseException as exc:
            caught = exc

    assert isinstance(caught, CpuOffloadPoolClosedError)
    assert await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5) == [None] * len(
        saturated.tasks
    )


async def test_cancel_arriving_after_pool_closed_propagates_cancelled_error() -> None:
    """池已关后 await 期间到达的新取消请求传播 CancelledError，任务终态为已取消。"""
    async with saturate_cpu_offload_pool() as saturated:
        queued = asyncio.ensure_future(run_in_cpu_pool(_double, 21))
        await saturated.wait_queued()
        assert not queued.done()

        # 关池取消排队 future 后、任务恢复前注入新取消请求，恢复时按取消增量归因。
        shutdown_cpu_offload_executor()
        queued.cancel()

        with pytest.raises(asyncio.CancelledError):
            await queued
        assert queued.cancelled()

    assert await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5) == [None] * len(
        saturated.tasks
    )


@pytest.mark.parametrize(
    "message",
    [
        "cannot schedule new futures after shutdown",
        "worker 线程创建失败",
    ],
    ids=["shutdown-rejection-text", "arbitrary-text"],
)
async def test_submit_after_shutdown_flag_converts_regardless_of_message(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    """实例关闭标志置位后 submit 的任意 RuntimeError 都转专用异常，归因只看池状态不看文案。"""
    from seedream_mcp.utils.core import executors as executors_module

    class _ClosedExecutor:
        _shutdown = True

        def submit(self, func_call: Any) -> None:
            del func_call
            raise RuntimeError(message)

    monkeypatch.setattr(executors_module, "cpu_offload_executor", lambda: _ClosedExecutor())

    with pytest.raises(CpuOffloadPoolClosedError):
        await run_in_cpu_pool(_double, 21)


async def test_submit_rejection_while_interpreter_finalizing_converts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解释器退出标志置位时的调度拒绝转专用异常，实例关闭标志彼时未置位。"""
    from seedream_mcp.utils.core import executors as executors_module

    class _OpenExecutor:
        _shutdown = False

        def submit(self, func_call: Any) -> None:
            del func_call
            raise RuntimeError("cannot schedule new futures after interpreter shutdown")

    monkeypatch.setattr(sys, "is_finalizing", lambda: True)
    monkeypatch.setattr(executors_module, "cpu_offload_executor", lambda: _OpenExecutor())

    with pytest.raises(CpuOffloadPoolClosedError):
        await run_in_cpu_pool(_double, 21)


async def test_submit_arbitrary_error_while_interpreter_finalizing_converts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解释器退出标志置位时与关池无关文案的 submit 拒绝同样转专用异常。"""
    from seedream_mcp.utils.core import executors as executors_module

    class _OpenExecutor:
        _shutdown = False

        def submit(self, func_call: Any) -> None:
            del func_call
            raise RuntimeError("worker 线程创建失败")

    monkeypatch.setattr(sys, "is_finalizing", lambda: True)
    monkeypatch.setattr(executors_module, "cpu_offload_executor", lambda: _OpenExecutor())

    with pytest.raises(CpuOffloadPoolClosedError):
        await run_in_cpu_pool(_double, 21)


@pytest.mark.parametrize(
    ("message", "shutdown_flag"),
    [
        ("worker 线程创建失败", False),
        ("cannot schedule new futures after shutdown", False),
    ],
    ids=["other-runtime-error", "open-pool-rejection-text"],
)
async def test_submit_other_runtime_error_propagates_untouched(
    monkeypatch: pytest.MonkeyPatch, message: str, shutdown_flag: bool
) -> None:
    """池未关闭且解释器未退出时 submit 的 RuntimeError 原样上抛，与消息文案无关。"""
    from seedream_mcp.utils.core import executors as executors_module

    class _StubExecutor:
        def __init__(self, shutdown_flag: bool) -> None:
            self._shutdown = shutdown_flag

        def submit(self, func_call: Any) -> None:
            del func_call
            raise RuntimeError(message)

    monkeypatch.setattr(
        executors_module, "cpu_offload_executor", lambda: _StubExecutor(shutdown_flag)
    )

    with pytest.raises(RuntimeError, match=message) as excinfo:
        await run_in_cpu_pool(_double, 21)

    assert not isinstance(excinfo.value, CpuOffloadPoolClosedError)


def test_pool_depth_follows_generate_and_prepare_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """池深经注册提供者随两类活动并发推导并封顶，低并发由下限兜底，已建池不重建。"""
    from seedream_mcp import config as config_module
    from seedream_mcp.config import SeedreamConfig

    def _rebuild_depth(generate_concurrency: int, image_prepare_concurrency: int = 5) -> int:
        config = SeedreamConfig(
            api_key="test_key",
            generate_concurrency=generate_concurrency,
            image_prepare_concurrency=image_prepare_concurrency,
        )
        monkeypatch.setattr(config_module, "_active_config", config)
        shutdown_cpu_offload_executor()
        return cpu_offload_executor()._max_workers

    # 生成并发 3 与 4 由下限 12 兜底，其后随默认预处理并发 5 与解码槽位线性放大，
    # 24 起封顶 32；调低预处理并发同等缩小池深。
    assert _rebuild_depth(3) == 12
    assert _rebuild_depth(4) == 12
    assert _rebuild_depth(5) == 13
    assert _rebuild_depth(10) == 18
    assert _rebuild_depth(10, image_prepare_concurrency=1) == 14
    assert _rebuild_depth(1024) == 32

    # 已建池不随启动后的配置变化重建，共享单池语义保持。
    built = cpu_offload_executor()
    monkeypatch.setattr(
        config_module, "_active_config", SeedreamConfig(api_key="test_key", generate_concurrency=8)
    )
    assert cpu_offload_executor() is built


def test_pool_depth_unregistered_provider_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """提供者未注册时不读活动配置，按默认并发回退推导，池照常可用。"""
    from seedream_mcp import config as config_module
    from seedream_mcp.config import SeedreamConfig
    from seedream_mcp.utils.core import executors as executors_module

    monkeypatch.setattr(
        config_module,
        "_active_config",
        SeedreamConfig(api_key="test_key", generate_concurrency=16),
    )
    monkeypatch.setattr(executors_module, "_CPU_OFFLOAD_DEPTH_PROVIDER", None)
    shutdown_cpu_offload_executor()

    # 活动配置的 16 不被读取，回退默认生成并发 3 与预处理并发 5 加解码槽位后由下限兜底。
    assert cpu_offload_executor()._max_workers == 12


def test_pool_depth_falls_back_when_config_build_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """活动配置不可构建时按默认并发回退推导，池可用性不随配置错误退化。"""
    from seedream_mcp import config as config_module
    from seedream_mcp.config import SeedreamConfig
    from seedream_mcp.utils.core.errors import SeedreamConfigError

    monkeypatch.setattr(config_module, "_active_config", None)
    monkeypatch.setattr(config_module, "_global_config", None)

    def _raising_from_env() -> SeedreamConfig:
        raise SeedreamConfigError("未找到ARK_API_KEY环境变量")

    monkeypatch.setattr(SeedreamConfig, "from_env", _raising_from_env)
    shutdown_cpu_offload_executor()

    assert cpu_offload_executor()._max_workers == 12


async def test_shutdown_cancels_queued_and_revives() -> None:
    """shutdown 退出不等队列：旧池拒绝新任务、排队任务取消、在途任务自然完成。"""
    async with saturate_cpu_offload_pool() as saturated:
        executor = saturated.executor
        queued = [executor.submit(lambda: "queued") for _ in range(2)]

        # 全部 worker 被占住时关闭：wait=True 形态会使本调用挂死直至超时。
        shutdown_cpu_offload_executor()

        assert all(future.cancelled() for future in queued)
        with pytest.raises(RuntimeError):
            executor.submit(lambda: None)

    assert await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5) == [None] * len(
        saturated.tasks
    )

    revived = cpu_offload_executor()
    assert revived is not executor
    assert revived.submit(lambda: "ok").result(timeout=5) == "ok"
