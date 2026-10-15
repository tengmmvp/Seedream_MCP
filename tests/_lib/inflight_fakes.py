"""inflight 单飞登记表的共享测试辅助。

供 test_download_manager_security 与 test_prepare_cache_single_flight 复用，
替代两处逐份同步的未取回异常回调替身。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


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
