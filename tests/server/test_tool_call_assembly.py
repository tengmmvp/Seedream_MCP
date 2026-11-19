"""MCP tools/call 平铺参数装配语义测试。

经 MCPServer 真实 call 路径锁定平铺签名的 wire 契约：平铺键名反序列化、可选字段
缺省不进 fields_set、组图配置缺省时生效上限按参考图数量自动推导、嵌套模型接受 JSON
形态入参、浏览工具缺省携带默认值。run_* 处理器以 spy 替换。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

import seedream_mcp.server._registration as server
from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.server.runtime import mcp
from seedream_mcp.tools.schemas import (
    BackgroundMode,
    BrowseImagesInput,
    GenerationTool,
    GenerationToolType,
    ImageToImageInput,
    LayerDecomposeInput,
    LayerDecompositionOptions,
    MultiImageFusionInput,
    OptimizePromptOptions,
    OutputFormat,
    ResponseFormat,
    SequentialGenerationInput,
    SequentialImageGenerationOptions,
    TextToImageInput,
)


def _ok_result() -> CallToolResult:
    """构造能通过 outputSchema 校验的最小成功结果，供 spy 处理器返回。"""
    return CallToolResult(
        content=[TextContent(type="text", text="ok")],
        structured_content={"tool": "spy", "success": True},
    )


@pytest.fixture
def spy_run_handlers(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """以 spy 替换 server 模块的全部生成与浏览 run_* 处理器，捕获平铺参数组装出的输入模型。

    工具函数体内的 run_* 名经模块全局查找，替换后隔离下游流水线；同时注入活动
    配置并在用例结束后复位为 None，spy 路径不消费但 _config_from_context 解析
    必须成功。
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
        "run_layer_decompose",
    ):
        monkeypatch.setattr(server, name, _spy_generation)
    monkeypatch.setattr(server, "run_browse_images", _spy_browse)
    # spy 已隔离下游能力门控，未识别端点全放行仅作门控前移时的防御。
    set_active_config(SeedreamConfig(api_key="test_key", model_id="ep-assembly-guard"))
    yield captured
    set_active_config(None)


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

    assert isinstance(result, CallToolResult)
    assert result.is_error is False
    params = spy_run_handlers["params"]
    assert isinstance(params, TextToImageInput)
    assert params.prompt == "一只戴墨镜的猫"
    assert params.size == "2K"
    assert params.response_format is ResponseFormat.URL
    assert params.auto_save is False
    assert params.watermark is None
    assert "watermark" not in params.model_fields_set


async def test_image_to_image_full_flat_arguments_assemble_into_input_model(
    spy_run_handlers: dict[str, Any],
) -> None:
    """图文生图全字段平铺参数装配为输入模型，全部显式传入字段登记进 fields_set。"""
    result = await mcp.call_tool(
        "image_to_image",
        {
            "prompt": "把背景换成雪山",
            "optimize_prompt_options": {"mode": "fast"},
            "image": "https://example.com/ref.png",
            "size": "2K",
            "background": "transparent",
            "watermark": False,
            "tools": [{"type": "web_search"}],
            "output_format": "png",
            "response_format": "b64_json",
            "stream": True,
            "request_count": 1,
            "parallelism": 1,
            "auto_save": True,
            "save_path": "./saved",
            "custom_name": "portrait",
        },
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is False
    params = spy_run_handlers["params"]
    assert isinstance(params, ImageToImageInput)
    assert params.prompt == "把背景换成雪山"
    assert params.optimize_prompt_options == OptimizePromptOptions(mode="fast")
    assert params.image == "https://example.com/ref.png"
    assert params.size == "2K"
    assert params.background == BackgroundMode.TRANSPARENT
    assert params.watermark is False
    assert params.tools == [GenerationTool(type=GenerationToolType.WEB_SEARCH)]
    assert params.output_format == OutputFormat.PNG
    assert params.response_format is ResponseFormat.B64_JSON
    assert params.stream is True
    assert params.request_count == 1
    assert params.parallelism == 1
    assert params.auto_save is True
    assert params.save_path == "./saved"
    assert params.custom_name == "portrait"
    assert params.model_fields_set == {
        "prompt",
        "optimize_prompt_options",
        "image",
        "size",
        "background",
        "watermark",
        "tools",
        "output_format",
        "response_format",
        "stream",
        "request_count",
        "parallelism",
        "auto_save",
        "save_path",
        "custom_name",
    }


async def test_layer_decompose_flat_arguments_assemble_into_input_model(
    spy_run_handlers: dict[str, Any],
) -> None:
    """图层拆分特有字段平铺装配为输入模型，prompt 缺省成立。"""
    result = await mcp.call_tool(
        "layer_decompose",
        {
            "image": "https://example.com/ref.png",
            "layer_decomposition_options": {"max_images": 50, "text_recognition": True},
            "size": "auto",
            "watermark": False,
            "response_format": "b64_json",
            "request_count": 1,
            "parallelism": 1,
            "auto_save": True,
        },
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is False
    params = spy_run_handlers["params"]
    assert isinstance(params, LayerDecomposeInput)
    assert params.prompt is None
    assert params.image == "https://example.com/ref.png"
    assert params.layer_decomposition_options == LayerDecompositionOptions(
        max_images=50, text_recognition=True
    )
    assert params.size == "auto"
    assert params.watermark is False
    assert params.response_format is ResponseFormat.B64_JSON
    assert "layer_decomposition" not in params.model_fields_set


async def test_multi_image_fusion_full_flat_arguments_assemble_into_input_model(
    spy_run_handlers: dict[str, Any],
) -> None:
    """多图融合全字段平铺参数装配为输入模型，image 列表与嵌套模型均按传入值落位。"""
    result = await mcp.call_tool(
        "multi_image_fusion",
        {
            "prompt": "将图1的服装换到图2的模特身上",
            "optimize_prompt_options": {"mode": "standard"},
            "image": ["https://example.com/a.png", "./.seedream/images/b.jpg"],
            "size": "2048x2048",
            "background": "transparent",
            "watermark": False,
            "tools": [{"type": "web_search"}],
            "output_format": "jpeg",
            "response_format": "url",
            "stream": False,
            "request_count": 2,
            "parallelism": 2,
            "auto_save": False,
            "save_path": "./fusion",
            "custom_name": "fusion_set",
        },
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is False
    params = spy_run_handlers["params"]
    assert isinstance(params, MultiImageFusionInput)
    assert params.prompt == "将图1的服装换到图2的模特身上"
    assert params.optimize_prompt_options == OptimizePromptOptions(mode="standard")
    assert params.image == ["https://example.com/a.png", "./.seedream/images/b.jpg"]
    assert params.size == "2048x2048"
    assert params.background == BackgroundMode.TRANSPARENT
    assert params.watermark is False
    assert params.tools == [GenerationTool(type=GenerationToolType.WEB_SEARCH)]
    assert params.output_format == OutputFormat.JPEG
    assert params.response_format is ResponseFormat.URL
    assert params.stream is False
    assert params.request_count == 2
    assert params.parallelism == 2
    assert params.auto_save is False
    assert params.save_path == "./fusion"
    assert params.custom_name == "fusion_set"
    assert params.model_fields_set == {
        "prompt",
        "optimize_prompt_options",
        "image",
        "size",
        "background",
        "watermark",
        "tools",
        "output_format",
        "response_format",
        "stream",
        "request_count",
        "parallelism",
        "auto_save",
        "save_path",
        "custom_name",
    }


async def test_sequential_max_images_omitted_derives_from_reference_count(
    spy_run_handlers: dict[str, Any],
) -> None:
    """组图配置未提供时按参考图数量自动推导生效上限。"""
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
    assert "sequential_image_generation_options" not in params.model_fields_set
    assert params.sequential_image_generation_options is None
    # 未显式提供时生效上限 = 总上限 15 - 参考图 2 张。
    assert params.effective_max_images == 13


async def test_sequential_max_images_explicit_value_is_respected(
    spy_run_handlers: dict[str, Any],
) -> None:
    """组图配置显式提供时登记进 fields_set 并以传入值为准，不触发推导。"""
    await mcp.call_tool(
        "sequential_generation",
        {
            "prompt": "生成4格漫画",
            "image": ["https://example.com/a.png"],
            "sequential_image_generation_options": {"max_images": 5},
        },
    )

    params = spy_run_handlers["params"]
    assert isinstance(params, SequentialGenerationInput)
    assert "sequential_image_generation_options" in params.model_fields_set
    assert params.sequential_image_generation_options == SequentialImageGenerationOptions(
        max_images=5
    )
    assert params.effective_max_images == 5


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


async def test_cross_field_validation_error_carries_structured_content(
    spy_run_handlers: dict[str, Any],
) -> None:
    """跨字段校验失败经工具体错误出口返回 is_error 结果，structuredContent 非空。

    parallelism 大于 request_count 在输入模型构造时抛 ValidationError，不得冒泡为
    无 structuredContent 的协议层错误；错误形态与流水线失败分支一致，error.type
    为 validation_error；content 在摘要后回传与 structuredContent 等值的 JSON 块。
    """
    result = await mcp.call_tool(
        "sequential_generation",
        {"prompt": "四格漫画", "request_count": 1, "parallelism": 2},
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    structured = result.structured_content
    assert structured is not None
    assert structured["success"] is False
    assert structured["error"]["type"] == "validation_error"
    assert "parallelism" in structured["error"]["message"]
    text_blocks = [content for content in result.content if isinstance(content, TextContent)]
    assert len(text_blocks) == 2
    assert "parallelism" in text_blocks[0].text
    # 两通道同源：可见文本取结构化 error.message 的净化产物，边界不做二次净化。
    assert text_blocks[0].text == structured["error"]["message"]
    assert json.loads(text_blocks[1].text) == structured
    # 失败发生在输入模型构造阶段，spy 处理器未被触达。
    assert "params" not in spy_run_handlers


async def test_run_tool_pipeline_rejects_unknown_kwargs_keys() -> None:
    """参数字典含输入模型未知键时立即抛 TypeError，指明工具名与未知键。

    工具体内 kwargs 字典是输入模型字段集的手工拷贝，拼错键若无前置校验只会
    下沉为运行时校验错误或被 None 过滤吞掉；TypeError 须发生在输入模型构造
    之前，runner 不得被触达。
    """
    reached = False

    async def _spy(params: Any, **_: Any) -> CallToolResult:
        nonlocal reached
        reached = True
        return _ok_result()

    with pytest.raises(TypeError, match=r"text_to_image.*watermarkss"):
        await server._run_tool_pipeline(
            "text_to_image",
            TextToImageInput,
            {"prompt": "一只猫", "watermarkss": True},
            _spy,
            config=None,
            ctx=None,
            workspace_roots=None,
        )

    assert reached is False
