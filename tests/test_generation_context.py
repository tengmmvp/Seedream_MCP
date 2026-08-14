"""生成执行上下文构建、并行结果聚合与响应格式化测试。"""

from dataclasses import fields

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import (
    GenerationExecutionContext,
    aggregate_parallel_generation_results,
    build_generation_context,
    format_generation_response,
    update_result_with_auto_save,
)
from seedream_mcp.utils.io.io_save import AutoSaveResult
from seedream_mcp.utils.core.errors import SeedreamValidationError


def _build_config() -> SeedreamConfig:
    return SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-4-0-250828",
        default_size="2K",
    )


def test_build_generation_context_uses_default_size_when_omitted() -> None:
    config = _build_config()
    context = build_generation_context({"prompt": "test"}, config)

    assert context.size == "2K"
    assert context.request_count == 1
    assert context.parallelism == 1


def test_generation_execution_context_field_order_matches_mcp_order() -> None:
    assert [field.name for field in fields(GenerationExecutionContext)] == [
        "prompt",
        "optimize_prompt_options",
        "size",
        "watermark",
        "response_format",
        "output_format",
        "stream",
        "tools",
        "request_count",
        "parallelism",
        "enable_auto_save",
        "save_path",
        "custom_name",
    ]


def test_build_generation_context_rejects_explicit_empty_size() -> None:
    config = _build_config()

    with pytest.raises(SeedreamValidationError, match="图像尺寸不能为空"):
        build_generation_context({"prompt": "test", "size": ""}, config)


def test_build_generation_context_sets_default_parallelism_by_request_count() -> None:
    config = _build_config()
    context = build_generation_context({"prompt": "test", "request_count": 3}, config)

    assert context.request_count == 3
    assert context.parallelism == 3


def test_build_generation_context_uses_explicit_parallelism() -> None:
    config = _build_config()
    context = build_generation_context(
        {"prompt": "test", "request_count": 4, "parallelism": 2},
        config,
    )
    assert context.request_count == 4
    assert context.parallelism == 2


def test_build_generation_context_rejects_zero_parallelism() -> None:
    config = _build_config()

    with pytest.raises(SeedreamValidationError, match="parallelism 必须在 1-4 之间"):
        build_generation_context({"prompt": "test", "request_count": 2, "parallelism": 0}, config)


def test_build_generation_context_accepts_seedream_50_output_format_and_tools() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5-0-260128",
        default_size="2K",
    )

    context = build_generation_context(
        {
            "prompt": "test",
            "output_format": "png",
            "tools": [{"type": "web_search"}],
        },
        config,
    )

    assert context.output_format == "png"
    assert context.tools == [{"type": "web_search"}]


def test_build_generation_context_rejects_output_format_for_seedream_45() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-4-5-251128",
        default_size="2K",
    )

    with pytest.raises(SeedreamValidationError, match="仅 doubao-seedream-5.0 系列"):
        build_generation_context({"prompt": "test", "output_format": "png"}, config)


def test_build_generation_context_rejects_stream_for_seedream_50_pro() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5-0-pro-260628",
        default_size="2K",
    )

    with pytest.raises(SeedreamValidationError, match="5.0-pro 不支持流式输出"):
        build_generation_context({"prompt": "test", "stream": True}, config)


def test_build_generation_context_rejects_fast_optimize_mode_for_seedream_50() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5-0-260128",
        default_size="2K",
    )

    with pytest.raises(
        SeedreamValidationError, match="仅支持 optimize_prompt_options.mode=standard"
    ):
        build_generation_context(
            {"prompt": "test", "optimize_prompt_options": {"mode": "fast"}},
            config,
        )


def test_update_result_with_auto_save_aligns_with_saveable_images_only() -> None:
    result = {
        "success": True,
        "data": [
            {
                "type": "image_generation.partial_failed",
                "image_index": 1,
                "error": {"code": "blocked", "message": "content blocked"},
            },
            {
                "type": "image_generation.partial_succeeded",
                "image_index": 2,
                "url": "https://example.com/ok.png",
            },
        ],
    }
    auto_save_results = [
        AutoSaveResult(
            success=True,
            original_url="https://example.com/ok.png",
            local_path="images/ok.png",
            markdown_ref="![ok](images/ok.png)",
        )
    ]

    updated = update_result_with_auto_save(result, auto_save_results, [1])

    failed_item = updated["data"][0]
    success_item = updated["data"][1]

    assert "local_path" not in failed_item
    assert "markdown_ref" not in failed_item
    assert success_item["local_path"] == "images/ok.png"
    assert success_item["markdown_ref"] == "![ok](images/ok.png)"


def test_aggregate_parallel_generation_results_merges_data_usage_and_failures() -> None:
    request_results = [
        {
            "success": True,
            "data": [{"url": "https://example.com/1.png"}],
            "usage": {"generated_images": 1, "total_tokens": 10},
            "status": "completed",
        },
        None,
        {
            "success": True,
            "data": [{"url": "https://example.com/3.png"}],
            "usage": {"generated_images": 1, "total_tokens": 8},
            "status": "completed",
        },
    ]
    request_errors = {2: RuntimeError("请求超时")}

    result = aggregate_parallel_generation_results(
        request_results=request_results,
        request_errors=request_errors,
    )

    assert result["success"] is True
    assert result["status"] == "partial"
    assert result["batch"]["request_count"] == 3
    assert result["batch"]["success_requests"] == 2
    assert result["batch"]["failed_requests"] == 1
    assert result["usage"]["generated_images"] == 2
    assert result["usage"]["total_tokens"] == 18
    assert result["data"][0]["request_index"] == 1
    assert result["data"][1]["request_index"] == 2
    assert "请求超时" in result["data"][1]["error"]["message"]
    assert result["data"][2]["request_index"] == 3


def test_aggregate_parallel_generation_results_all_failed_keeps_error_details() -> None:
    result = aggregate_parallel_generation_results(
        request_results=[None, None],
        request_errors={1: RuntimeError("认证失败"), 2: RuntimeError("请求频率超限")},
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "认证失败" in result["error"]["message"]
    assert result["batch"]["errors"][0]["request_index"] == 1
    assert "认证失败" in result["batch"]["errors"][0]["message"]
    assert result["batch"]["errors"][1]["request_index"] == 2
    assert "请求频率超限" in result["batch"]["errors"][1]["message"]


def test_aggregate_parallel_generation_results_uses_result_error_when_success_false() -> None:
    result = aggregate_parallel_generation_results(
        request_results=[
            {"success": False, "error": "鉴权失败"},
            {"success": False, "error": {"message": "请求频率超限"}},
        ],
        request_errors={},
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "鉴权失败" in result["error"]["message"]
    assert "请求频率超限" in result["error"]["message"]
    assert result["data"][0]["error"]["message"] == "鉴权失败"
    assert result["data"][1]["error"]["message"] == "请求频率超限"
    assert result["batch"]["errors"][0]["message"] == "鉴权失败"
    assert "请求频率超限" in result["batch"]["errors"][1]["message"]


def test_format_generation_response_reports_parallel_failure_details() -> None:
    text = format_generation_response(
        title="文生图任务完成",
        result={
            "success": False,
            "error": "并行请求全部失败。请求1: 认证失败",
            "batch": {
                "request_count": 2,
                "success_requests": 0,
                "failed_requests": 2,
                "errors": [
                    {"request_index": 1, "message": "认证失败"},
                    {"request_index": 2, "message": "请求频率超限"},
                ],
            },
        },
        prompt="test",
        size="2K",
    )

    assert "图片生成失败:" in text
    assert "并行失败详情:" in text
    assert "请求 1: 认证失败" in text
    assert "请求 2: 请求频率超限" in text


def test_format_failure_section_extracts_message_from_dict_error() -> None:
    """result['error'] 为 dict 形态时，用户可见文本应取其 message，不应输出字典 repr。"""
    text = format_generation_response(
        title="文生图任务完成",
        result={
            "success": False,
            "status": "failed",
            "error": {"type": "auth_error", "message": "并行请求全部失败。请求1: 认证失败"},
            "batch": {
                "request_count": 1,
                "success_requests": 0,
                "failed_requests": 1,
                "errors": [{"request_index": 1, "message": "认证失败"}],
            },
        },
        prompt="test",
        size="2K",
    )
    failure_line = text.splitlines()[0]
    assert "并行请求全部失败。请求1: 认证失败" in failure_line
    # 失败首行不应出现字典 repr 的花括号
    assert "{" not in failure_line
    assert "}" not in failure_line


def test_format_generation_response_shows_input_images_for_pro_usage() -> None:
    text = format_generation_response(
        title="图文生图任务完成",
        result={
            "success": True,
            "data": [{"url": "https://example.com/1.png", "size": "1024x1024"}],
            "usage": {"input_images": 1, "generated_images": 1, "output_tokens": 100},
        },
        prompt="test",
        size="1024x1024",
    )

    # 5.0 Pro 返回 usage.input_images 表示输入图数，应在文本统计中展示
    assert "输入图片数: 1" in text
    assert "生成图片数: 1" in text


def test_build_generation_context_auto_save_none_equals_omitted() -> None:
    """auto_save=None 与不传该参行为一致，均回落到 config.auto_save_enabled。"""
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=True)

    ctx_explicit_none = build_generation_context({"prompt": "t", "auto_save": None}, config)
    ctx_omitted = build_generation_context({"prompt": "t"}, config)

    assert ctx_explicit_none.enable_auto_save is True
    assert ctx_omitted.enable_auto_save is True
    assert ctx_explicit_none.enable_auto_save == ctx_omitted.enable_auto_save


def test_build_generation_context_auto_save_none_passes_through_disabled_config() -> None:
    """config.auto_save_enabled=False 时，auto_save=None 穿透结果为 False。"""
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=False)

    ctx = build_generation_context({"prompt": "t", "auto_save": None}, config)

    assert ctx.enable_auto_save is False


def test_build_generation_context_explicit_auto_save_overrides_config() -> None:
    """显式 auto_save 非 None 时覆盖 config.auto_save_enabled，确保穿透仅在 None 时发生。"""
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=True)

    ctx = build_generation_context({"prompt": "t", "auto_save": False}, config)

    assert ctx.enable_auto_save is False


def test_build_generation_context_rejects_non_bool_auto_save() -> None:
    """auto_save 为非 bool 值（如字符串）须在上下文构建处被拒绝。"""
    config = _build_config()

    with pytest.raises(SeedreamValidationError, match="auto_save 必须是布尔值"):
        build_generation_context({"prompt": "t", "auto_save": "yes"}, config)
