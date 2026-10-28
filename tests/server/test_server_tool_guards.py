"""工具注册面守护测试：带参工具的 roots 依赖注解与错误结果工具名对齐注册名。

两处仅靠注释或平行常量维持的注册契约在此锁定：漏写 workspace_roots 解析
注解的工具静默回退环境根；_run_tool_pipeline 传入的工具名常量与注册名错位
使 structuredContent.tool 报旧名。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from mcp.types import CallToolResult

import seedream_mcp.server as server

# lifespan 复位 fixture reset_lifespan_singletons 由 tests/conftest.py 共享提供。

# 五个带参工具各触发一次构造期校验错误的输入：text_to_image、multi_image_fusion、
# sequential_generation 用 parallelism 大于 request_count 的跨字段规则，
# image_to_image 用 prompt 缺省且未开图层拆分的组合规则，
# 浏览工具用平铺签名不拦截的空白 format_filter 条目。
_CROSS_FIELD_INVALID_ARGS: dict[str, dict[str, Any]] = {
    "text_to_image": {"prompt": "一只猫", "request_count": 1, "parallelism": 2},
    "image_to_image": {"image": "https://example.com/a.png"},
    "multi_image_fusion": {
        "prompt": "融合两张图",
        "image": ["https://example.com/a.png", "https://example.com/b.png"],
        "request_count": 1,
        "parallelism": 2,
    },
    "sequential_generation": {
        "prompt": "四格漫画",
        "image": ["https://example.com/a.png"],
        "request_count": 1,
        "parallelism": 2,
    },
    "browse_images": {"format_filter": [""]},
}


def _parameterized_tools() -> list[Any]:
    """注册表中 inputSchema 声明了属性字段的工具，无参工具不适用签名守护。"""
    return [
        tool for tool in server.mcp._tool_manager.list_tools() if tool.parameters.get("properties")
    ]


def test_parameterized_tools_declare_workspace_roots_resolver() -> None:
    """带参工具的 workspace_roots 形参挂 roots 解析注解，漏写即静默回退环境根。"""
    tools = _parameterized_tools()
    assert len(tools) >= 5
    for tool in tools:
        assert "workspace_roots" in inspect.signature(tool.fn).parameters, tool.name
        resolve = tool.resolved_params.get("workspace_roots")
        assert resolve is not None, tool.name
        assert resolve[0].fn is server._workspace_roots_dependency, tool.name


@pytest.mark.parametrize("tool_name", sorted(_CROSS_FIELD_INVALID_ARGS))
async def test_validation_error_result_reports_registered_tool_name(
    tool_name: str,
    reset_lifespan_singletons: None,
) -> None:
    """构造期校验失败的 structuredContent.tool 与注册名一致，传错常量即错位。"""
    del reset_lifespan_singletons
    result = await server.mcp.call_tool(tool_name, _CROSS_FIELD_INVALID_ARGS[tool_name])

    # 校验失败路径恒为 CallToolResult；isinstance 同时为 mypy 收窄联合类型。
    assert isinstance(result, CallToolResult)
    assert result.is_error
    structured = result.structured_content
    assert structured is not None
    assert structured["tool"] == tool_name
