"""调用关联 ID 原语、patcher 注入与日志行渲染测试。"""

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from seedream_mcp.utils.core.logs import (
    CALL_ID_PLACEHOLDER,
    _prepare_log_record,
    call_id_scope,
    cleared_call_id_context,
    current_call_id,
    setup_logging,
)

from _lib.log_fakes import line_with, preserved_loguru_globals


def test_call_id_scope_assigns_unique_short_id_and_restores() -> None:
    """scope 内获得 12 位十六进制 ID，两次进入互不相同，退出恢复占位符。"""
    assert current_call_id() == CALL_ID_PLACEHOLDER
    with call_id_scope() as first:
        assert current_call_id() == first
        assert len(first) == 12
        int(first, 16)
    with call_id_scope() as second:
        assert second != first
    assert current_call_id() == CALL_ID_PLACEHOLDER


def test_call_id_scope_nested_reuses_outer_id() -> None:
    """嵌套 scope 复用外层 ID，一次逻辑调用的日志保持单一 ID。"""
    with call_id_scope() as outer:
        with call_id_scope() as inner:
            assert inner == outer
            assert current_call_id() == outer
        assert current_call_id() == outer


def test_cleared_call_id_context_returns_to_placeholder() -> None:
    """cleared_call_id_context 的上下文副本清除 ID，后台任务与回调回到占位符形态。"""
    with call_id_scope() as call_id:
        assert current_call_id() == call_id
        assert cleared_call_id_context().run(current_call_id) == CALL_ID_PLACEHOLDER


def test_patcher_injects_call_id_into_extra() -> None:
    """patcher 注入当前上下文的 ID 值，显式 bind 的 call_id 不被覆盖。"""
    record: dict[str, Any] = {"message": "x", "extra": {}}
    _prepare_log_record(record)
    assert record["extra"]["call_id"] == CALL_ID_PLACEHOLDER

    with call_id_scope() as call_id:
        scoped: dict[str, Any] = {"message": "x", "extra": {}}
        _prepare_log_record(scoped)
        assert scoped["extra"]["call_id"] == call_id

    bound: dict[str, Any] = {"message": "x", "extra": {"call_id": "explicit"}}
    _prepare_log_record(bound)
    assert bound["extra"]["call_id"] == "explicit"


def test_log_line_renders_call_id_segment(tmp_path: Path) -> None:
    """真实文件 sink 渲染 [call_id] 段：scope 内渲染 ID，scope 外渲染占位符。"""
    log_file = tmp_path / "log"
    with preserved_loguru_globals():
        setup_logging("INFO", str(log_file), enable_console=False, force_standard_logging=True)
        logger.info("outside-scope")
        with call_id_scope() as call_id:
            logger.info("inside-scope")

    assert f"[{CALL_ID_PLACEHOLDER}]" in line_with(log_file, "outside-scope")
    assert f"[{call_id}]" in line_with(log_file, "inside-scope")


async def test_call_id_carried_into_to_thread_logs(tmp_path: Path) -> None:
    """asyncio.to_thread 复制任务上下文，工作线程内日志携带同一 call_id。"""
    log_file = tmp_path / "log"
    with preserved_loguru_globals():
        setup_logging("INFO", str(log_file), enable_console=False, force_standard_logging=True)
        with call_id_scope() as call_id:
            logger.info("async-stage")
            await asyncio.to_thread(logger.info, "thread-stage")

    for stage in ("async-stage", "thread-stage"):
        assert f"[{call_id}]" in line_with(log_file, stage)
