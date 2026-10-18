"""inflight 单飞注册表的共享测试辅助。

未取回异常回调替身供 test_download_manager_security 与
test_prepare_cache_single_flight 复用；在途条目定位与取消、失败路径
task 回收助手供 test_image_preview、test_prepare_cache_single_flight 与
test_webapp_file_api 复用。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from seedream_mcp.utils.core.inflight import InflightEntry
from seedream_mcp.utils.core.loop_bound import LoopKeyedInflight


def _patch_unretrieved_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> list["asyncio.Task[Any]"]:
    """把 inflight.log_unretrieved_task_exception 替换为记录 task 并检索异常的替身。

    替身经模块属性遮蔽即生效，检索异常避免 "Task exception was never retrieved"
    告警。返回已触发回调的 task 列表，供断言登记时序。
    """
    from seedream_mcp.utils.core import inflight

    fired: list["asyncio.Task[Any]"] = []

    def record(task: "asyncio.Task[Any]") -> None:
        fired.append(task)
        if not task.cancelled():
            task.exception()

    monkeypatch.setattr(inflight, "log_unretrieved_task_exception", record)
    return fired


async def await_shared_leader(
    inflight: LoopKeyedInflight[Any, Any],
    key: Any = None,
    *,
    timeout: float = 5.0,
) -> InflightEntry[Any]:
    """轮询当前循环的 inflight 注册表直至目标在途条目登记，返回该条目。

    key 为 None 时要求注册表恰有一条在途条目；以有界等待而非固定迭代次数
    兜底慢环境，超时未登记或条目数歧义即 fail 并指明期望。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        registry = inflight.by_loop.get(loop)
        if registry is not None:
            if key is None:
                entries = [entry_ref() for entry_ref in registry.values()]
                live = [entry for entry in entries if entry is not None]
                if len(live) == 1:
                    return live[0]
                if len(live) > 1:
                    pytest.fail(f"注册表存在 {len(live)} 条在途条目，key=None 要求恰有一条")
            else:
                entry_ref = registry.get(key)
                if entry_ref is not None:
                    entry = entry_ref()
                    if entry is not None:
                        return entry
        if loop.time() >= deadline:
            expected = repr(key) if key is not None else "任一在途条目"
            pytest.fail(f"共享在途 leader 在 {timeout} 秒内未登记（期望 {expected}）")
        await asyncio.sleep(0.01)


async def cancel_shared_leader(
    inflight: LoopKeyedInflight[Any, Any],
    key: Any = None,
    *,
    timeout: float = 5.0,
) -> "asyncio.Task[Any]":
    """定位在途条目（语义同 await_shared_leader）并取消其 task，返回被取消的 task。"""
    entry = await await_shared_leader(inflight, key, timeout=timeout)
    entry.task.cancel()
    return entry.task


async def _reap_if_pending(task: "asyncio.Task[Any]") -> None:
    """失败路径回收未完成的 spawn task：取消并取回终态，不泄漏 pending task 到循环 teardown。"""
    if task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
