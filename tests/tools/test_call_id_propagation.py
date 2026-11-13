"""runners 入口调用关联 ID 的分配、复用与传播测试。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest
from mcp.types import CallToolResult, TextContent

import seedream_mcp.tools.runners as runners_module
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.runners import run_browse_images, run_get_model_info, run_text_to_image
from seedream_mcp.tools.schemas import BrowseImagesInput, TextToImageInput
from seedream_mcp.utils.core.logs import call_id_scope, get_logger

from _lib.log_fakes import is_real_call_id, stage_call_ids


def _ok_result() -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text="ok")])


def _unused_config() -> SeedreamConfig:
    """probe handler 不读 config，类型占位满足 runner 签名。"""
    return cast("SeedreamConfig", None)


def _generation_probe() -> Callable[..., Awaitable[CallToolResult]]:
    """构造在当前调用上下文记 handler-stage 与线程内 thread-stage 日志的生成 handler 替身。"""

    async def probe(params: Any, config: Any, ctx: Any) -> CallToolResult:
        del params, config, ctx
        get_logger().info("handler-stage")
        await asyncio.to_thread(get_logger().info, "thread-stage")
        return _ok_result()

    return probe


async def test_generation_runner_assigns_shared_call_id(
    debug_log_records: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成 runner 入口分配的 call_id 覆盖 roots 回退、handler 与线程内日志行。

    runners 的 call_id_scope 兜底经用户裁决保留：包门面再导出的入口不经 MCP/Web 外层作用域调用时，过程日志同样保有调用归因。
    """
    monkeypatch.setattr(runners_module, "handle_text_to_image", _generation_probe())
    await run_text_to_image(TextToImageInput(prompt="probe"), _unused_config())

    shared: set[str] = set()
    for keyword in ("客户端未声明 roots capability", "handler-stage", "thread-stage"):
        stage_ids = stage_call_ids(debug_log_records, keyword)
        assert stage_ids, f"未捕获关键词 {keyword!r} 的日志记录"
        shared.update(stage_ids)
    assert len(shared) == 1
    assert is_real_call_id(shared.pop())


async def test_consecutive_calls_get_distinct_call_ids(
    debug_log_records: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """两次工具调用的日志行各持相异 call_id，并发事故可按行配对归因。"""
    monkeypatch.setattr(runners_module, "handle_text_to_image", _generation_probe())

    await run_text_to_image(TextToImageInput(prompt="probe"), _unused_config())
    await run_text_to_image(TextToImageInput(prompt="probe"), _unused_config())

    handler_ids = stage_call_ids(debug_log_records, "handler-stage")
    assert len(handler_ids) == 2
    assert handler_ids[0] != handler_ids[1]


async def test_outer_scope_reused_by_runner_single_id(
    debug_log_records: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """外层已开调用关联作用域（Web 端点形态）时 runner 复用同一 ID。"""
    monkeypatch.setattr(runners_module, "handle_text_to_image", _generation_probe())

    with call_id_scope() as outer:
        get_logger().info("preamble-stage")
        await run_text_to_image(TextToImageInput(prompt="probe"), _unused_config())

    assert stage_call_ids(debug_log_records, "preamble-stage") == [outer]
    assert stage_call_ids(debug_log_records, "handler-stage") == [outer]


async def test_browse_runner_assigns_call_id(
    debug_log_records: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """browse runner 入口同样分配 call_id，查询链路日志行可归因。"""

    async def probe_browse(params: Any, *, ctx: Any, entry_bound: Any) -> CallToolResult:
        del params, ctx, entry_bound
        get_logger().info("browse-stage")
        return _ok_result()

    monkeypatch.setattr(runners_module, "handle_browse_images", probe_browse)
    await run_browse_images(BrowseImagesInput(directory=".", recursive=False))

    browse_ids = stage_call_ids(debug_log_records, "browse-stage")
    assert len(browse_ids) == 1
    assert is_real_call_id(browse_ids[0])


async def test_get_model_info_runner_assigns_call_id(
    debug_log_records: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_model_info runner 入口同样分配 call_id，模型查询日志行可归因。"""

    async def probe_model_info(config: Any) -> CallToolResult:
        del config
        get_logger().info("model-info-stage")
        return _ok_result()

    monkeypatch.setattr(runners_module, "handle_get_model_info", probe_model_info)
    await run_get_model_info(_unused_config())

    model_info_ids = stage_call_ids(debug_log_records, "model-info-stage")
    assert len(model_info_ids) == 1
    assert is_real_call_id(model_info_ids[0])
