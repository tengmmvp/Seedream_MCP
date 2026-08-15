"""MCP 上下文通知容错封装与工具元数据测试。

覆盖进度上报与日志推送的容错封装，以及工具与资源的元数据注册：
- _safe_ctx_log：按级别分发到对应 Context 方法，客户端不支持 logging 能力时静默跳过
- _safe_report_progress：上报失败不影响主流程
- 工具顶层 title 注册，对齐 MCP 2025-06-18 规范的 Tool.title 顶层字段
- 工具 annotations 四项能力 hint 逐项锁定，资源 MIME 与内容格式一致
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from seedream_mcp.server import mcp
from seedream_mcp.tools.core.common import _safe_ctx_log, _safe_report_progress

# ==================== _safe_ctx_log ====================


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error"])
async def test_safe_ctx_log_dispatches_to_corresponding_method(level: str) -> None:
    ctx = MagicMock()
    setattr(ctx, level, AsyncMock())

    await _safe_ctx_log(ctx, level, "hello")

    getattr(ctx, level).assert_awaited_once_with("hello")


async def test_safe_ctx_log_silent_when_ctx_is_none() -> None:
    # ctx 为 None 时不应抛异常
    await _safe_ctx_log(None, "info", "hello")


async def test_safe_ctx_log_ignores_invalid_level() -> None:
    ctx = MagicMock()
    ctx.info = AsyncMock()

    await _safe_ctx_log(ctx, "trace", "hello")  # 非法级别应被忽略

    ctx.info.assert_not_called()


async def test_safe_ctx_log_swallows_client_errors() -> None:
    ctx = MagicMock()
    ctx.info = AsyncMock(side_effect=RuntimeError("client lacks logging capability"))

    # 客户端不支持 logging 能力时不应抛异常
    await _safe_ctx_log(ctx, "info", "hello")


# ==================== _safe_report_progress ====================


async def test_safe_report_progress_invokes_report_progress() -> None:
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()

    await _safe_report_progress(ctx, progress=42.0, message="mid")

    ctx.report_progress.assert_awaited_once_with(progress=42.0, total=100.0, message="mid")


async def test_safe_report_progress_silent_when_ctx_is_none() -> None:
    await _safe_report_progress(None, progress=10.0, message="start")


async def test_safe_report_progress_swallows_errors() -> None:
    ctx = MagicMock()
    ctx.report_progress = AsyncMock(side_effect=RuntimeError("no progress support"))

    await _safe_report_progress(ctx, progress=50.0, message="mid")


# ==================== 工具顶层 title 对齐 MCP 规范 ====================


async def test_tools_register_top_level_title() -> None:
    tools = await mcp.list_tools()
    titles = {tool.name: tool.title for tool in tools}

    assert titles["seedream_text_to_image"] == "Seedream 文生图"
    assert titles["seedream_image_to_image"] == "Seedream 图文生图"
    assert titles["seedream_multi_image_fusion"] == "Seedream 多图融合"
    assert titles["seedream_sequential_generation"] == "Seedream 组图输出"
    assert titles["seedream_browse_images"] == "Seedream 图片浏览"


async def test_tool_titles_not_duplicated_in_annotations() -> None:
    # title 应在顶层 Tool.title，不应再残留于 annotations.title
    tools = await mcp.list_tools()
    for tool in tools:
        if tool.annotations is None:
            continue
        assert getattr(tool.annotations, "title", None) is None


async def test_tool_annotations_locked_to_current_hints() -> None:
    """五工具 annotations 逐项锁定，防止行为提示被无意改动。

    生成类工具非只读、非破坏、非幂等且需联网调用 API，四个 hint 依次为
    False/False/False/True；浏览类工具只读本地文件列表、可重复调用，为
    True/False/True/False。客户端据此决定确认策略与并行调用方式。
    """
    expected = {
        "seedream_text_to_image": (False, False, False, True),
        "seedream_image_to_image": (False, False, False, True),
        "seedream_multi_image_fusion": (False, False, False, True),
        "seedream_sequential_generation": (False, False, False, True),
        "seedream_browse_images": (True, False, True, False),
    }

    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == set(expected)

    for name, hints in expected.items():
        annotations = by_name[name].annotations
        assert annotations is not None, name
        readonly, destructive, idempotent, open_world = hints
        assert annotations.readOnlyHint is readonly, name
        assert annotations.destructiveHint is destructive, name
        assert annotations.idempotentHint is idempotent, name
        assert annotations.openWorldHint is open_world, name


# ==================== MCP 资源注册 ====================


async def test_resources_registered() -> None:
    """注册 MCP resources，包含 workspace roots、server info 与 models info。"""
    resources = await mcp.list_resources()
    uris = {str(resource.uri) for resource in resources}

    assert "seedream://workspace/roots" in uris
    assert "seedream://server/info" in uris
    assert "seedream://models/info" in uris


async def test_json_resources_declare_application_json_mime() -> None:
    """三个 JSON 内容资源声明 application/json MIME，与实际序列化格式一致。"""
    resources = await mcp.list_resources()
    by_uri = {str(resource.uri): resource for resource in resources}

    for uri in (
        "seedream://workspace/roots",
        "seedream://server/info",
        "seedream://models/info",
    ):
        assert by_uri[uri].mimeType == "application/json", uri
