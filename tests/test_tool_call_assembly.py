"""MCP tools/call 平铺参数装配语义测试。

经 MCPServer 真实 call 路径锁定平铺签名的 wire 契约：平铺键名反序列化、可选字段
缺省不进 fields_set、组图 max_images 按参考图数量自动推导、嵌套模型接受 JSON
形态入参、浏览工具缺省携带默认值。run_* 处理器以间谍替换。
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.resources import mcp
from seedream_mcp.tools.core.schemas import (
    BrowseImagesInput,
    OptimizePromptOptions,
    ResponseFormat,
    SequentialGenerationInput,
    TextToImageInput,
)


def _ok_result() -> CallToolResult:
    """构造能通过 outputSchema 校验的最小成功结果，供间谍处理器返回。"""
    return CallToolResult(
        content=[TextContent(type="text", text="ok")],
        structured_content={"tool": "spy", "success": True},
    )


@pytest.fixture
def spy_run_handlers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """以间谍替换 server 模块的五个 run_* 处理器，捕获平铺参数组装出的输入模型。

    工具函数体内的 run_* 名经模块全局查找，替换后隔离下游流水线；同时注入活动
    配置，间谍路径不消费但 _config_from_context 解析必须成功。
    """
    captured: dict[str, Any] = {}

    async def _spy_generation(
        params: Any, config: Any = None, ctx: Any = None, workspace_roots: Any = None
    ) -> CallToolResult:
        del config, ctx, workspace_roots
        captured["params"] = params
        return _ok_result()

    async def _spy_browse(
        params: Any, ctx: Any = None, workspace_roots: Any = None
    ) -> CallToolResult:
        del ctx, workspace_roots
        captured["params"] = params
        return _ok_result()

    for name in (
        "run_text_to_image",
        "run_image_to_image",
        "run_multi_image_fusion",
        "run_sequential_generation",
    ):
        monkeypatch.setattr(server, name, _spy_generation)
    monkeypatch.setattr(server, "run_browse_images", _spy_browse)
    server.set_active_config(SeedreamConfig(api_key="test_key"))
    return captured


async def test_flat_arguments_assemble_into_input_model(
    spy_run_handlers: dict[str, Any],
) -> None:
    """平铺键名经 MCPServer 反序列化并组装为输入模型，可选字段缺省不进 fields_set。"""
    result = await mcp.call_tool(
        "text_to_image",
        {
            "prompt": "一只戴墨镜的猫",
            "size": "2K",
            "response_format": "url",
            "auto_save": False,
        },
    )

    assert result.is_error is False
    params = spy_run_handlers["params"]
    assert isinstance(params, TextToImageInput)
    assert params.prompt == "一只戴墨镜的猫"
    assert params.size == "2K"
    assert params.response_format is ResponseFormat.URL
    assert params.auto_save is False
    assert params.watermark is None
    assert "watermark" not in params.model_fields_set


async def test_sequential_max_images_omitted_derives_from_reference_count(
    spy_run_handlers: dict[str, Any],
) -> None:
    """组图 max_images 未提供时按参考图数量自动推导，与嵌套形态行为一致。"""
    await mcp.call_tool(
        "sequential_generation",
        {
            "prompt": "生成4格漫画",
            "image": ["https://example.com/a.png", "https://example.com/b.png"],
        },
    )

    params = spy_run_handlers["params"]
    assert isinstance(params, SequentialGenerationInput)
    assert params.image == ["https://example.com/a.png", "https://example.com/b.png"]
    assert "max_images" not in params.model_fields_set
    # 未显式提供时上限 = 总上限 15 - 参考图 2 张。
    assert params.max_images == 13


async def test_sequential_max_images_explicit_value_is_respected(
    spy_run_handlers: dict[str, Any],
) -> None:
    """显式提供的 max_images 登记进 fields_set 并以传入值为准，不触发推导。"""
    await mcp.call_tool(
        "sequential_generation",
        {
            "prompt": "生成4格漫画",
            "image": ["https://example.com/a.png"],
            "max_images": 5,
        },
    )

    params = spy_run_handlers["params"]
    assert isinstance(params, SequentialGenerationInput)
    assert "max_images" in params.model_fields_set
    assert params.max_images == 5


async def test_nested_model_field_accepts_plain_dict(
    spy_run_handlers: dict[str, Any],
) -> None:
    """optimize_prompt_options 以普通 JSON 对象传入，经 MCPServer 反序列化为模型实例。"""
    await mcp.call_tool(
        "text_to_image",
        {"prompt": "一只猫", "optimize_prompt_options": {"mode": "fast"}},
    )

    params = spy_run_handlers["params"]
    assert isinstance(params, TextToImageInput)
    assert params.optimize_prompt_options == OptimizePromptOptions(mode="fast")


async def test_browse_defaults_apply_when_arguments_omitted(
    spy_run_handlers: dict[str, Any],
) -> None:
    """浏览工具全部参数缺省时组装出的输入模型携带模型声明的默认值。"""
    await mcp.call_tool("browse_images", {})

    params = spy_run_handlers["params"]
    assert isinstance(params, BrowseImagesInput)
    assert params.directory is None
    assert params.recursive is True
    assert params.max_depth == 3
    assert params.limit == 50
    assert params.offset == 0
    assert params.format_filter is None
    assert params.show_details is False


async def test_flat_constraint_violation_raises_tool_error() -> None:
    """平铺参数违反约束时经 Tool.run 包装为 ToolError，不逃逸为未捕获异常。"""
    with pytest.raises(ToolError, match="request_count"):
        await mcp.call_tool("text_to_image", {"prompt": "一只猫", "request_count": 99})
