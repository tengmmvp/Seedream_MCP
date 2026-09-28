"""runtime structuredContent 与声明 outputSchema 的一致性测试。

验证 _build_generation_structured_result 与 _build_browse_structured_result 的产出
能实例化对应输出模型，防止 schema 与实际输出漂移。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import extract_images
from seedream_mcp.tools.core.context import GenerationExecutionContext, build_generation_context
from seedream_mcp.tools.core.outputs import (
    BrowseImagesStructuredOutput,
    GenerationStructuredOutput,
)
from seedream_mcp.tools.core.browse import _BrowseRequestState, _build_browse_structured_result
from seedream_mcp.tools.core.results import _build_generation_structured_result
from seedream_mcp.tools.core.schemas import (
    BackgroundMode,
    BrowseImagesInput,
    GenerationTool,
    GenerationToolType,
    ImageToImageInput,
    OptimizePromptOptions,
    OutputFormat,
    SequentialGenerationInput,
    TextToImageInput,
)


def test_generation_success_path_matches_schema() -> None:
    """成功路径 structuredContent 可实例化 GenerationStructuredOutput。"""
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": True,
        "status": "completed",
        "prompt": "a cat",
        "size": "2K",
        "response_format": "url",
        "output_format": None,
        "stream": False,
        "tools": None,
        "request_count": 1,
        "parallelism": 1,
        "data": [{"url": "https://example.com/1.png"}],
        "usage": {"generated_images": 1, "total_tokens": 10},
        "batch": None,
        "auto_save": {"enabled": True, "error": None, "results": []},
    }
    obj = GenerationStructuredOutput(**structured)
    assert obj.success is True
    assert obj.data == [{"url": "https://example.com/1.png"}]


def test_generation_exception_path_error_is_dict() -> None:
    """异常路径 error 为 dict 形态且可实例化输出模型。"""
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": False,
        "status": "failed",
        "error": {"type": "SeedreamAPIError", "message": "认证失败"},
    }
    obj = GenerationStructuredOutput(**structured)
    assert obj.success is False
    assert isinstance(obj.error, dict)


def test_generation_failed_result_error_is_dict() -> None:
    """失败路径完整字段的 structuredContent 可实例化，error 为 dict 形态。"""
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": False,
        "status": "failed",
        "prompt": "a cat",
        "size": "2K",
        "response_format": "url",
        "output_format": None,
        "stream": False,
        "tools": None,
        "request_count": 1,
        "parallelism": 1,
        "data": [],
        "usage": {},
        "batch": None,
        "auto_save": {"enabled": False},
        "error": {"type": "generation_failed", "message": "图片生成失败: 超时"},
    }
    obj = GenerationStructuredOutput(**structured)
    assert isinstance(obj.error, dict)
    assert obj.error["type"] == "generation_failed"


def test_output_model_stringifies_non_string_dict_field_keys() -> None:
    """模型自身字符串化 data/usage/batch/error 的非字符串键，任意构造路径无需调用方预处理。"""
    data: list[dict[Any, Any]] = [{1: "x", "url": "https://example.com/a.png"}]
    usage: dict[Any, Any] = {2: 3}
    batch: dict[Any, Any] = {3: "b"}
    error: dict[Any, Any] = {"1": "a", 1: "b"}
    obj = GenerationStructuredOutput(
        tool="text_to_image",
        success=False,
        data=data,
        usage=usage,
        batch=batch,
        error=error,
    )

    assert obj.data == [{"1": "x", "url": "https://example.com/a.png"}]
    assert obj.usage == {"2": 3}
    assert obj.batch == {"3": "b"}
    assert obj.error == {"1": "a", "1<dup>": "b"}


def test_browse_success_path_matches_schema() -> None:
    """浏览成功路径 structuredContent 可实例化 BrowseImagesStructuredOutput。"""
    structured: dict[str, Any] = {
        "tool": "browse_images",
        "success": True,
        "status": "completed",
        "directory": ".",
        "resolved_directories": ["/ws"],
        "workspace_roots": ["/ws"],
        "count": 1,
        "images": [{"index": 1, "path": "a.png"}],
        "recursive": True,
        "max_depth": 3,
        "limit": 50,
        "show_details": False,
        "format_filter": [".png", ".jpg"],
    }
    obj = BrowseImagesStructuredOutput(**structured)
    assert obj.count == 1
    assert obj.format_filter == [".png", ".jpg"]


def test_browse_failure_path_matches_schema() -> None:
    """浏览失败路径 error 为 dict 形态且可实例化输出模型。"""
    structured: dict[str, Any] = {
        "tool": "browse_images",
        "success": False,
        "status": "failed",
        "error": {"type": "browse_failed", "message": "目录超出允许范围"},
        "workspace_roots": ["/ws"],
    }
    obj = BrowseImagesStructuredOutput(**structured)
    assert obj.success is False


def test_browse_empty_path_matches_schema() -> None:
    """空结果路径 status=empty 可实例化输出模型。"""
    structured: dict[str, Any] = {
        "tool": "browse_images",
        "success": True,
        "status": "empty",
        "directory": ".",
        "resolved_directories": ["/ws"],
        "workspace_roots": ["/ws"],
        "images": [],
        "count": 0,
    }
    obj = BrowseImagesStructuredOutput(**structured)
    assert obj.status == "empty"


def test_extract_images_normalizes_dict_data_to_list() -> None:
    """上游非列表 data 形态统一归一为列表，与 outputSchema 的 List 声明一致。"""
    assert extract_images({"data": {"url": "https://example.com/x.png"}}) == [
        {"url": "https://example.com/x.png"}
    ]
    assert extract_images({"data": [{"url": "a"}, {"url": "b"}]}) == [
        {"url": "a"},
        {"url": "b"},
    ]
    assert extract_images({"data": {"data": [{"url": "nested"}]}}) == [{"url": "nested"}]


def test_extract_images_normalizes_null_and_non_dict_to_empty() -> None:
    """null 与非字典元素剔除，不产出 [None] 违反 List[Dict] 声明使成功响应翻为 ToolError。"""
    assert extract_images({"data": None}) == []
    assert extract_images({"data": [None, {"url": "x"}, None]}) == [{"url": "x"}]
    assert extract_images({}) == []  # 缺少 data 键
    assert extract_images({"data": "https://example.com/str"}) == []  # 标量无法表达图片


def test_extract_images_nested_null_data_not_treated_as_image_entry() -> None:
    """仅含 data:null 的嵌套字典是包裹形态而非图片条目，下钻后归空。"""
    assert extract_images({"data": {"data": None}}) == []


def test_real_generation_builder_success_output_instantiates_schema() -> None:
    """调用真实 _build_generation_structured_result 须能实例化输出 schema。

    手工 dict 用例发现不了 builder 漏写字段或类型漂移，本用例端到端构造真实输出。
    """
    config = SeedreamConfig(api_key="k")
    context = build_generation_context(TextToImageInput(prompt="a cat", size="2K"), config)
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": "https://example.com/1.png"}],
        "usage": {"generated_images": 1},
    }
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=context,
        auto_save_results=None,
        auto_save_error=None,
    )
    obj = GenerationStructuredOutput(**structured)
    assert obj.success is True
    assert obj.tool == "text_to_image"
    assert obj.prompt == "a cat"
    assert obj.data == [{"url": "https://example.com/1.png"}]


def _image_to_image_echo_context() -> GenerationExecutionContext:
    """携带透明背景、输出格式、联网工具与提示词优化取值的图文生图上下文。"""
    return build_generation_context(
        ImageToImageInput(
            prompt="a cat",
            image="https://example.com/ref.png",
            optimize_prompt_options=OptimizePromptOptions(mode="standard"),
            output_format=OutputFormat.PNG,
            background=BackgroundMode.TRANSPARENT,
            tools=[GenerationTool(type=GenerationToolType.WEB_SEARCH)],
        ),
        # 未知家族模型放行全部能力，背景透明与联网工具得以在同一上下文并存。
        SeedreamConfig(api_key="k", model_id="ep-echo-guard"),
    )


def _sequential_echo_context(max_images: int) -> GenerationExecutionContext:
    """携带指定 max_images 生效值的组图上下文。"""
    return build_generation_context(
        SequentialGenerationInput(prompt="四格漫画", max_images=max_images),
        SeedreamConfig(api_key="k"),
    )


@pytest.mark.parametrize(
    ("context", "non_none_fields"),
    [
        (
            _image_to_image_echo_context(),
            {"optimize_prompt_options", "background", "output_format", "tools"},
        ),
        (_sequential_echo_context(1), {"max_images"}),
        (_sequential_echo_context(15), {"max_images"}),
    ],
    ids=["image_to_image", "sequential_min", "sequential_max"],
)
def test_echo_payload_covers_output_model_echo_fields(
    context: GenerationExecutionContext, non_none_fields: set[str]
) -> None:
    """成功路径回显字段的取值与上下文逐项相等，漏构造的新字段恒 None 即失败。

    回显字段与结果区字段在模型与 payload 构造两处双列，extra=forbid 只拦反向
    漂移，漏构造的新字段退化为 None，与上下文实际值不等在此暴露；五个可选回显
    字段经两类上下文全部取非 None 值，避免 None 与 None 的恒真比较。
    """
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result={"success": True, "status": "completed", "data": [], "usage": {}},
        context=context,
        auto_save_results=None,
        auto_save_error=None,
    )
    # 非回显字段：基类元数据、由 result 组装的结果区与自动保存摘要，均非对生成参数的回显。
    non_echo = {
        "tool",
        "success",
        "status",
        "error",
        "data",
        "usage",
        "batch",
        "auto_save",
        "truncated_events",
        "deadline_exceeded",
    }
    echo_fields = set(GenerationStructuredOutput.model_fields) - non_echo
    for name in echo_fields:
        assert structured[name] == getattr(context, name), name
    for name in non_none_fields:
        assert getattr(context, name) is not None, name


def test_real_generation_builder_failure_output_instantiates_schema() -> None:
    """失败路径的 builder 输出同样须能实例化 schema，覆盖 error 归一化分支。"""
    config = SeedreamConfig(api_key="k")
    context = build_generation_context(TextToImageInput(prompt="a cat", size="2K"), config)
    result = {"success": False, "status": "failed", "error": "生成超时"}
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=context,
        auto_save_results=None,
        auto_save_error=None,
    )
    obj = GenerationStructuredOutput(**structured)
    assert obj.success is False
    assert obj.error == {"type": "generation_failed", "message": "生成超时"}


def test_real_browse_builder_output_instantiates_schema(tmp_path: Path) -> None:
    """调用真实 _build_browse_structured_result 须能实例化 BrowseImagesStructuredOutput。"""
    workspace = tmp_path / "ws"
    state = _BrowseRequestState.from_params(
        BrowseImagesInput(directory=".", recursive=True, max_depth=3, limit=50, offset=0),
        workspace_roots=[workspace],
        resolved_directories=[workspace],
        format_filter=[".png", ".jpg"],
    )
    structured = _build_browse_structured_result(
        state,
        status="completed",
        images=[{"index": 1, "path": "a.png"}],
    )
    obj = BrowseImagesStructuredOutput(**structured)
    assert obj.success is True
    assert obj.count == 1
    assert obj.format_filter == [".png", ".jpg"]
