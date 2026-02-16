import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import (
    aggregate_parallel_generation_results,
    build_generation_context,
    format_generation_response,
    update_result_with_auto_save,
)
from seedream_mcp.utils.auto_save import AutoSaveResult
from seedream_mcp.utils.errors import SeedreamValidationError


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

    updated = update_result_with_auto_save(result, auto_save_results)

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
    request_errors = {2: "请求超时"}

    result = aggregate_parallel_generation_results(
        request_results=request_results,
        request_errors=request_errors,
    )

    assert result["success"] is True
    assert result["status"] == "partial_completed"
    assert result["batch"]["request_count"] == 3
    assert result["batch"]["success_requests"] == 2
    assert result["batch"]["failed_requests"] == 1
    assert result["usage"]["generated_images"] == 2
    assert result["usage"]["total_tokens"] == 18
    assert result["data"][0]["request_index"] == 1
    assert result["data"][1]["request_index"] == 2
    assert result["data"][1]["error"]["message"] == "请求超时"
    assert result["data"][2]["request_index"] == 3


def test_aggregate_parallel_generation_results_all_failed_keeps_error_details() -> None:
    result = aggregate_parallel_generation_results(
        request_results=[None, None],
        request_errors={1: "认证失败", 2: "请求频率超限"},
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "认证失败" in result["error"]
    assert result["batch"]["errors"][0]["request_index"] == 1
    assert result["batch"]["errors"][0]["message"] == "认证失败"
    assert result["batch"]["errors"][1]["request_index"] == 2
    assert result["batch"]["errors"][1]["message"] == "请求频率超限"


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
    assert "鉴权失败" in result["error"]
    assert "请求频率超限" in result["error"]
    assert result["data"][0]["error"]["message"] == "鉴权失败"
    assert result["data"][1]["error"]["message"] == "请求频率超限"
    assert result["batch"]["errors"][0]["message"] == "鉴权失败"
    assert result["batch"]["errors"][1]["message"] == "请求频率超限"


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
