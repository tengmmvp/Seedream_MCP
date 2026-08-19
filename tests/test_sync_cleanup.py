"""resources._sync_cleanup 进程级清理测试。

_sync_cleanup 是 cli_main finally 的同步清理入口：提取并清空活动与退役资源后
asyncio.run 关闭。覆盖正常清理、RuntimeError 与意外异常被吞、无资源 no-op 与
退役资源兜底关闭。
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
    """活动资源桩：仅提供 _sync_cleanup 关闭路径所需的 client 与 download_manager。"""

    def __init__(self, client: object, download_manager: object) -> None:
        self.client = client
        self.download_manager = download_manager


def test_sync_cleanup_closes_shared_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径下 asyncio.run 关闭 client 与 download_manager，引用不残留。"""
    client = _Closeable()
    download_manager = _Closeable()
    monkeypatch.setattr(resources, "_active_resource", _FakeResource(client, download_manager))

    resources._sync_cleanup()

    assert client.closed is True
    assert download_manager.closed is True
    # 引用在 asyncio.run 前已清空，无论清理成败都不泄漏。
    assert resources._active_resource is None


def test_sync_cleanup_swallows_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.run 抛 RuntimeError（无/已有事件循环）时被吞，引用已清空不抛出。

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
    resources._sync_cleanup()

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
    resources._sync_cleanup()

    assert resources._active_resource is None


def test_sync_cleanup_noop_when_no_shared_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """无活动资源时清理为 no-op，不抛出、asyncio.run 正常执行空关闭。"""
    monkeypatch.setattr(resources, "_active_resource", None)

    resources._sync_cleanup()

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

    resources._sync_cleanup()

    assert retired_client_a.closed and retired_manager_a.closed
    assert retired_client_b.closed and retired_manager_b.closed
    assert resources._retired_resources == []
