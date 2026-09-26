"""resources.sync_cleanup 进程级清理测试。

sync_cleanup 是 cli_main finally 的同步清理入口：提取并清空活动与退役资源后
asyncio.run 关闭，并一并关闭 CPU 卸载线程池。覆盖正常清理、RuntimeError 与
意外异常被吞、无资源 no-op 与退役资源兜底关闭，以及关闭体内被中断时池关闭
仍执行。
"""

import asyncio

import pytest

import seedream_mcp.resources as resources


class _Closeable:
    """可关闭资源桩：记录 close 是否被调用，模拟 SeedreamClient/DownloadManager。"""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeResource:
    """活动资源桩：仅提供 sync_cleanup 关闭路径所需的 client 与 download_manager。"""

    def __init__(self, client: object, download_manager: object) -> None:
        self.client = client
        self.download_manager = download_manager


def test_sync_cleanup_closes_shared_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径下 asyncio.run 关闭 client 与 download_manager，引用不残留。"""
    client = _Closeable()
    download_manager = _Closeable()
    monkeypatch.setattr(resources, "_active_resource", _FakeResource(client, download_manager))

    resources.sync_cleanup()

    assert client.closed is True
    assert download_manager.closed is True
    # 引用在 asyncio.run 前已清空，无论清理成败都不泄漏。
    assert resources._active_resource is None


def test_sync_cleanup_swallows_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.run 因无事件循环或已有事件循环抛 RuntimeError 时被吞，引用已清空不抛出。

    uvicorn 退出时事件循环已停止或主线程已有运行循环，属预期场景，余量交 GC/OS。
    """
    client = _Closeable()
    monkeypatch.setattr(resources, "_active_resource", _FakeResource(client, None))

    def _raising_run(coro: object) -> None:
        # 关闭未 await 的协程，避免 RuntimeWarning。
        coro.close()  # type: ignore[attr-defined]
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    monkeypatch.setattr(asyncio, "run", _raising_run)

    # 不应抛出。
    resources.sync_cleanup()

    # 引用在 asyncio.run 前已清空。
    assert resources._active_resource is None
    # asyncio.run 未真正执行，close 未被调用。
    assert client.closed is False


def test_sync_cleanup_swallows_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.run 抛非 RuntimeError 的意外异常时被记录并吞掉，清理路径不中断。"""
    client = _Closeable()
    monkeypatch.setattr(resources, "_active_resource", _FakeResource(client, None))

    def _raising_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]
        raise ValueError("unexpected cleanup failure")

    monkeypatch.setattr(asyncio, "run", _raising_run)

    # 不应抛出。
    resources.sync_cleanup()

    assert resources._active_resource is None


def test_sync_cleanup_noop_when_no_shared_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """无活动资源时清理为 no-op，不抛出、asyncio.run 正常执行空关闭。"""
    monkeypatch.setattr(resources, "_active_resource", None)

    resources.sync_cleanup()

    assert resources._active_resource is None


def test_sync_cleanup_closes_retired_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """同步清理兜底同样关闭退役资源并清空追踪列表。

    config 热切换产生的退役资源若不在进程同步退出时关闭，连接池将遗留到进程结束。
    """
    retired_client_a, retired_manager_a = _Closeable(), _Closeable()
    retired_client_b, retired_manager_b = _Closeable(), _Closeable()
    monkeypatch.setattr(resources, "_active_resource", None)
    monkeypatch.setattr(
        resources,
        "_retired_resources",
        [
            _FakeResource(retired_client_a, retired_manager_a),
            _FakeResource(retired_client_b, retired_manager_b),
        ],
    )

    resources.sync_cleanup()

    assert retired_client_a.closed and retired_manager_a.closed
    assert retired_client_b.closed and retired_manager_b.closed
    assert resources._retired_resources == []


def test_sync_cleanup_closes_pool_when_close_body_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncio.run 关闭体内被二次 Ctrl+C 打断时，CPU 卸载池关闭仍不被跳过。"""
    from seedream_mcp.utils.core.executors import cpu_offload_executor

    monkeypatch.setattr(resources, "_active_resource", None)

    def _interrupting_run(coro: object) -> None:
        # 关闭未 await 的协程，避免 RuntimeWarning。
        coro.close()  # type: ignore[attr-defined]
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", _interrupting_run)

    executor_before = cpu_offload_executor()
    resources.sync_cleanup()

    # 未 patch 的真实 shutdown 已执行：池被关闭后按需重建为新实例。
    assert cpu_offload_executor() is not executor_before


async def test_sync_cleanup_cancels_queued_cpu_offload_pool_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_cleanup 关闭 CPU 卸载池，池上排队任务取消为专用异常。"""
    from _cpu_offload_spy import saturate_cpu_offload_pool
    from seedream_mcp.utils.core.executors import (
        CpuOffloadPoolClosedError,
        cpu_offload_executor,
        run_in_cpu_pool,
    )

    monkeypatch.setattr(resources, "_active_resource", None)

    def _closing_run(coro: object) -> None:
        # 关闭未 await 的协程，避免 RuntimeWarning。
        coro.close()  # type: ignore[attr-defined]
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    async with saturate_cpu_offload_pool() as saturated:
        queued = asyncio.ensure_future(run_in_cpu_pool(lambda: "queued"))
        await saturated.wait_queued()
        monkeypatch.setattr(asyncio, "run", _closing_run)

        resources.sync_cleanup()

        with pytest.raises(CpuOffloadPoolClosedError):
            await queued

    assert cpu_offload_executor() is not saturated.executor
    assert await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5) == [None] * len(
        saturated.tasks
    )
