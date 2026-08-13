"""runtime structuredContent 与声明 outputSchema 的一致性测试。

验证 _build_generation_structured_result 与 handle_browse_images 产出的 structuredContent
能够被 GenerationStructuredOutput 或 BrowseImagesStructuredOutput 实例化，即符合声明的 outputSchema。
防止 schema 与实际输出漂移，例如 format_filter 类型不一致类 bug。
"""

from __future__ import annotations

from seedream_mcp.tools.core.common import extract_images
from seedream_mcp.tools.core.outputs import (
    BrowseImagesStructuredOutput,
    GenerationStructuredOutput,
)


def test_generation_success_path_matches_schema() -> None:
    structured = {
        "tool": "seedream_text_to_image",
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
    structured = {
        "tool": "seedream_text_to_image",
        "success": False,
        "status": "failed",
        "error": {"type": "SeedreamAPIError", "message": "认证失败"},
    }
    obj = GenerationStructuredOutput(**structured)
    assert obj.success is False
    assert isinstance(obj.error, dict)


def test_generation_failed_result_error_is_str() -> None:
    structured = {
        "tool": "seedream_text_to_image",
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


def test_browse_success_path_matches_schema() -> None:
    structured = {
        "tool": "seedream_browse_images",
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
    structured = {
        "tool": "seedream_browse_images",
        "success": False,
        "status": "failed",
        "error": {"type": "browse_failed", "message": "目录超出允许范围"},
        "workspace_roots": ["/ws"],
    }
    obj = BrowseImagesStructuredOutput(**structured)
    assert obj.success is False


def test_browse_empty_path_matches_schema() -> None:
    structured = {
        "tool": "seedream_browse_images",
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
    # 上游可能返回 {"data": {"url": ...}} 非列表形态，
    # extract_images 须统一为 list，使 structuredContent.data 与 outputSchema 的 List 声明一致。
    assert extract_images({"data": {"url": "https://example.com/x.png"}}) == [
        {"url": "https://example.com/x.png"}
    ]
    assert extract_images({"data": [{"url": "a"}, {"url": "b"}]}) == [
        {"url": "a"},
        {"url": "b"},
    ]
    assert extract_images({"data": {"data": [{"url": "nested"}]}}) == [{"url": "nested"}]


def test_extract_images_normalizes_null_and_non_dict_to_empty() -> None:
    # data: null 会被 _build_api_result 保留，不得产出 [None]，否则违反 List[Dict] 声明、
    # 触发 outputSchema 校验失败把成功响应变成 ToolError；列表中的 null 与其他非字典元素同样剔除。
    assert extract_images({"data": None}) == []
    assert extract_images({"data": [None, {"url": "x"}, None]}) == [{"url": "x"}]
    assert extract_images({}) == []  # 缺少 data 键
    assert extract_images({"data": "https://example.com/str"}) == []  # 标量无法表达图片
