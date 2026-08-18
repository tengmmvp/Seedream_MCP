"""MCP 上下文通知容错封装与工具元数据测试。

覆盖进度上报的容错封装，以及工具与资源的元数据注册：
- _safe_report_progress：上报失败不影响主流程
- 工具顶层 title 注册，对齐 MCP 2025-06-18 规范的 Tool.title 顶层字段
- 工具 annotations 四项能力 hint 逐项锁定，资源 MIME 与内容格式一致

SDK 2.0 起 logging capability 按 SEP-2577 弃用，ctx.debug/info/warning/error 调用
触发 MCPDeprecationWarning 且无替代推送 API；日志推送通道已移除，客户端实时通知
仅经 report_progress(message=...) 的进度消息承载，离线排查走 loguru 文件日志。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from seedream_mcp.server import mcp
from seedream_mcp.tools.core.common import _safe_report_progress

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


# ==================== 日志推送通道移除守护 ====================


def test_deprecated_ctx_log_push_channel_removed() -> None:
    """ctx.debug/info/warning/error 的推送封装不复存在，防止弃用通道被重新引入。

    SDK 2.0 对四个方法标注 MCPDeprecationWarning（SEP-2577，logging capability 自
    2026-07-28 弃用），且 2026-07-28 起推送需请求级 opt-in、默认不送达；重新封装该
    通道会使全量测试重新出现弃用告警并依赖已弃用的送达语义。
    """
    import seedream_mcp.tools.core._helpers as helpers_module

    for name in ("_safe_ctx_log", "_VALID_LOG_LEVELS"):
        assert not hasattr(helpers_module, name), name


# ==================== 工具顶层 title 对齐 MCP 规范 ====================


async def test_tools_register_top_level_title() -> None:
    tools = await mcp.list_tools()
    titles = {tool.name: tool.title for tool in tools}

    assert titles["text_to_image"] == "Seedream 文生图"
    assert titles["image_to_image"] == "Seedream 图文生图"
    assert titles["multi_image_fusion"] == "Seedream 多图融合"
    assert titles["sequential_generation"] == "Seedream 组图输出"
    assert titles["browse_images"] == "Seedream 图片浏览"


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
    False/False/False/True；浏览类工具只读本地文件列表，read_only 与 open_world
    为 True/False。规范仅为非只读工具定义 destructive_hint 与 idempotent_hint，
    只读工具不携带两者，断言其为 None 锁定该规范口径。客户端据此决定确认策略
    与并行调用方式。
    """
    expected = {
        "text_to_image": (False, False, False, True),
        "image_to_image": (False, False, False, True),
        "multi_image_fusion": (False, False, False, True),
        "sequential_generation": (False, False, False, True),
        "browse_images": (True, None, None, False),
    }

    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == set(expected)

    for name, hints in expected.items():
        annotations = by_name[name].annotations
        assert annotations is not None, name
        readonly, destructive, idempotent, open_world = hints
        assert annotations.read_only_hint is readonly, name
        assert annotations.destructive_hint is destructive, name
        assert annotations.idempotent_hint is idempotent, name
        assert annotations.open_world_hint is open_world, name


# ==================== MCP 资源注册 ====================


async def test_resources_registered() -> None:
    """注册 MCP 资源：workspace roots 以模板注册（SDK 2.0 起 Context 仅注入模板资源），
    server info 与 models info 为静态资源。"""
    resources = await mcp.list_resources()
    uris = {str(resource.uri) for resource in resources}

    assert "seedream://server/info" in uris
    assert "seedream://models/info" in uris

    templates = await mcp.list_resource_templates()
    template_uris = {str(template.uri_template) for template in templates}
    assert "seedream://workspace/roots{?verbose}" in template_uris


async def test_json_resources_declare_application_json_mime() -> None:
    """JSON 内容资源声明 application/json MIME，与实际序列化格式一致。

    workspace roots 为模板资源，其 MIME 经 ResourceTemplate 携带；静态资源经
    Resource 携带，两侧分别校验。
    """
    resources = await mcp.list_resources()
    by_uri = {str(resource.uri): resource for resource in resources}

    for uri in (
        "seedream://server/info",
        "seedream://models/info",
    ):
        assert by_uri[uri].mime_type == "application/json", uri

    templates = await mcp.list_resource_templates()
    by_template = {str(t.uri_template): t for t in templates}
    assert by_template["seedream://workspace/roots{?verbose}"].mime_type == "application/json"
