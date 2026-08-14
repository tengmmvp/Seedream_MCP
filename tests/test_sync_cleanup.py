"""resources._sync_cleanup 进程级清理测试。

_sync_cleanup 是 cli_main finally 的同步清理入口：先提取并清空 _active_resource，
再 asyncio.run 关闭其 client 与 download_manager。覆盖三条路径：
正常清理关闭资源、RuntimeError（无/已有事件循环）被吞、其他异常被记录吞掉。
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
    """正常路径：asyncio.run 在无运行循环下执行 _close_held，关闭 client 与 download_manager。"""
    client = _Closeable()
    download_manager = _Closeable()
    monkeypatch.setattr(resources, "_active_resource", _FakeResource(client, download_manager))

    resources._sync_cleanup()

    assert client.closed is True
    assert download_manager.closed is True
    # 引用在 asyncio.run 前已清空，无论清理成败都不泄漏
    assert resources._active_resource is None


def test_sync_cleanup_swallows_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.run 抛 RuntimeError（无/已有事件循环）时被吞，引用已清空不抛出。

    uvicorn 退出时其事件循环已停止或主线程已有运行循环，asyncio.run 无法安全执行，
    属预期场景；_sync_cleanup 捕获 RuntimeError 静默放过，余量交 GC/OS。
    """
    client = _Closeable()
    monkeypatch.setattr(resources, "_active_resource", _FakeResource(client, None))

    def _raising_run(coro: object) -> None:
        # 关闭未 await 的协程，避免 RuntimeWarning
        coro.close()  # type: ignore[attr-defined]
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    monkeypatch.setattr(asyncio, "run", _raising_run)

    # 不应抛出
    resources._sync_cleanup()

    # 引用在 asyncio.run 前已清空
    assert resources._active_resource is None
    # asyncio.run 未真正执行，close 未被调用
    assert client.closed is False


def test_sync_cleanup_swallows_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.run 抛非 RuntimeError 的意外异常时被记录并吞掉，清理路径不中断。"""
    client = _Closeable()
    monkeypatch.setattr(resources, "_active_resource", _FakeResource(client, None))

    def _raising_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]
        raise ValueError("unexpected cleanup failure")

    monkeypatch.setattr(asyncio, "run", _raising_run)

    # 不应抛出
    resources._sync_cleanup()

    assert resources._active_resource is None


def test_sync_cleanup_noop_when_no_shared_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """无活动资源时清理为 no-op，不抛出、asyncio.run 正常执行空关闭。"""
    monkeypatch.setattr(resources, "_active_resource", None)

    resources._sync_cleanup()

    assert resources._active_resource is None
