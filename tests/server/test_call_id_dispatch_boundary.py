"""SDK 分发层调用关联作用域测试：middleware 开作用域，SDK 日志同 ID 归因。"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.client import Client

import seedream_mcp.server as server
import seedream_mcp.server._registration as registration
from seedream_mcp.server.runtime import (
    _CallIdScopeMiddleware,
    locate_request_state_boundary,
)
from seedream_mcp.utils.core.logs import get_logger

from _lib.log_fakes import is_real_call_id, stage_call_ids


async def test_sdk_crash_log_single_record_carries_call_id(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons: None,
    error_log_records: list[Any],
) -> None:
    """工具函数抛未捕获异常时，SDK 崩溃日志恰一条且携带真实 call_id。

    middleware 在 SDK 分发层开调用关联作用域，SDK 自身的崩溃记录落在作用域内、原生携带 ID。
    恰一条锁定项目侧无补记的重复崩溃行。经进程内 Client 走全分发路径，异常被 SDK 转为 isError 结果不外抛。
    """

    async def _raising(
        params: Any, config: Any = None, ctx: Any = None, workspace_roots: Any = None
    ) -> Any:
        del params, config, ctx, workspace_roots
        raise RuntimeError("boom past pipeline catch")

    monkeypatch.setattr(registration, "run_text_to_image", _raising)
    async with Client(server.mcp, mode="auto") as client:
        result = await client.call_tool("text_to_image", {"prompt": "dispatch boundary probe"})

    assert result.is_error
    crash_ids = stage_call_ids(error_log_records, "raised an unexpected exception")
    assert len(crash_ids) == 1
    assert is_real_call_id(crash_ids[0])


async def test_prompt_get_dispatch_logs_carry_call_id(
    reset_lifespan_singletons: None,
    error_log_records: list[Any],
) -> None:
    """prompts/get 经同一 middleware 路径获得作用域，渲染日志携带真实 call_id。"""

    @server.mcp.prompt(name="seedream_call_id_probe")
    async def _probe_prompt() -> str:
        get_logger().error("prompt-render-probe")
        return "probe"

    try:
        async with Client(server.mcp, mode="auto") as client:
            await client.get_prompt("seedream_call_id_probe")
    finally:
        server.mcp.remove_prompt("seedream_call_id_probe")

    probe_ids = stage_call_ids(error_log_records, "prompt-render-probe")
    assert len(probe_ids) == 1
    assert is_real_call_id(probe_ids[0])


def test_call_id_scope_middleware_ordered_outside_request_state_boundary() -> None:
    """链序守护：作用域中间件在 mcp.middleware 列表中位于「RequestStateBoundary」之前。

    SDK 2.3.0 构造先 append boundary 再 extend 用户 middleware，
    构造参数注入的作用域中间件落在 boundary 内层，boundary 的解封/封存失败日志
    在作用域之外触发、渲染占位符。runtime 经 provisional 属性 mcp.middleware
    前移到 boundary 之外。SDK 升级改变装配序或前移失效使作用域中间件
    落回 boundary 内层时本用例报警。
    """
    chain = server.mcp.middleware
    scope_indexes = [
        index
        for index, middleware in enumerate(chain)
        if isinstance(middleware, _CallIdScopeMiddleware)
    ]
    assert scope_indexes, "middleware 链中未找到调用关联作用域中间件"
    boundary = locate_request_state_boundary()
    assert boundary is not None, "middleware 链中未找到「RequestStateBoundary」"
    assert scope_indexes[0] < chain.index(boundary)
