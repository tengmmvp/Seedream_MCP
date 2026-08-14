"""生成类工具并行请求支持、schema 约束与失败结果封装测试。"""

from importlib import import_module
from typing import Any

import pytest
from mcp.types import TextContent
from pydantic import ValidationError

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core._helpers import _add_usage_value
from seedream_mcp.tools.core.results import aggregate_parallel_generation_results
from seedream_mcp.tools.core.schemas import (
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.tools.impl.image_to_image import handle_image_to_image
from seedream_mcp.tools.impl.multi_image_fusion import handle_multi_image_fusion
from seedream_mcp.tools.impl.sequential_generation import handle_sequential_generation
from seedream_mcp.tools.impl.text_to_image import handle_text_to_image
from seedream_mcp.utils.core.errors import SeedreamAPIError, SeedreamValidationError


def _build_config() -> SeedreamConfig:
    # 关闭自动保存：测试 mock 返回占位 URL，避免对 example.com 发起真实下载拖慢 CI
    return SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "method_name", "params"),
    [
        (
            handle_text_to_image,
            "text_to_image",
            TextToImageInput(prompt="test", request_count=3, parallelism=2),
        ),
        (
            handle_image_to_image,
            "image_to_image",
            ImageToImageInput(
                prompt="test",
                image="https://example.com/ref.png",
                request_count=3,
                parallelism=2,
            ),
        ),
        (
            handle_multi_image_fusion,
            "multi_image_fusion",
            MultiImageFusionInput(
                prompt="test",
                image=["https://example.com/1.png", "https://example.com/2.png"],
                request_count=3,
                parallelism=2,
            ),
        ),
        (
            handle_sequential_generation,
            "sequential_generation",
            SequentialGenerationInput(
                prompt="test",
                image="https://example.com/ref.png",
                request_count=3,
                parallelism=2,
            ),
        ),
    ],
)
async def test_generation_handlers_support_parallel_requests(
    monkeypatch: pytest.MonkeyPatch,
    handler,
    method_name: str,
    params,
) -> None:
    call_count = 0

    async def fake_method(self, **kwargs):  # noqa: ANN001
        nonlocal call_count
        del self, kwargs
        call_count += 1
        return {
            "success": True,
            "data": [{"url": f"https://example.com/{call_count}.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }

    client_module = import_module("seedream_mcp.client")
    client_cls = getattr(client_module, "SeedreamClient")
    monkeypatch.setattr(client_cls, method_name, fake_method)

    result = await handler(params, _build_config())

    assert call_count == 3
    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    response_text = next(
        content.text for content in result.content if isinstance(content, TextContent)
    )
    assert "并行请求信息:" in response_text
    assert "请求总数: 3" in response_text
    assert "成功请求: 3" in response_text
    assert result.structuredContent["request_count"] == 3


@pytest.mark.asyncio
async def test_parallel_requests_partial_failure_recorded_in_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N 个并行请求中单个失败：其余成功完成，异常进入 batch.errors，status 为 partial。"""
    call_count = 0

    async def fake_method(self, **kwargs):  # noqa: ANN001
        nonlocal call_count
        del self, kwargs
        call_count += 1
        # 第 2 次调用模拟单请求失败，其余成功；因并行执行顺序非确定，仅断言失败计数
        if call_count == 2:
            raise SeedreamAPIError("模拟单请求失败", status_code=500)
        return {
            "success": True,
            "data": [{"url": f"https://example.com/{call_count}.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }

    client_module = import_module("seedream_mcp.client")
    client_cls = getattr(client_module, "SeedreamClient")
    monkeypatch.setattr(client_cls, "text_to_image", fake_method)

    # 关闭自动保存以避免对占位 URL 发起真实下载，聚焦并行失败聚合断言
    config = SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False)
    result = await handle_text_to_image(
        TextToImageInput(prompt="test", request_count=3, parallelism=2),
        config,
    )

    assert call_count == 3
    # 部分成功：有任一请求成功即 success=True，isError 为 False
    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "partial"
    batch = result.structuredContent["batch"]
    assert batch["request_count"] == 3
    assert batch["success_requests"] == 2
    assert batch["failed_requests"] == 1
    assert len(batch["errors"]) == 1
    assert "模拟单请求失败" in batch["errors"][0]["message"]
    response_text = next(
        content.text for content in result.content if isinstance(content, TextContent)
    )
    assert "成功请求: 2" in response_text
    assert "失败请求: 1" in response_text


def test_parallel_options_reject_request_count_over_limit_in_schema() -> None:
    with pytest.raises(ValidationError, match="request_count"):
        TextToImageInput(prompt="test", request_count=5)


def test_parallel_options_reject_parallelism_greater_than_request_count_in_schema() -> None:
    with pytest.raises(ValidationError, match="parallelism 不能大于 request_count"):
        TextToImageInput(prompt="test", request_count=2, parallelism=3)


def test_parallel_options_reject_stream_with_parallel_requests_in_schema() -> None:
    with pytest.raises(ValidationError, match="stream=true 时 request_count 必须为 1"):
        TextToImageInput(prompt="test", request_count=2, stream=True)


@pytest.mark.asyncio
async def test_generation_handler_returns_call_tool_error_result_when_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_method(self, **kwargs):  # noqa: ANN001
        del self, kwargs
        raise SeedreamValidationError("提示词不能为空", field="prompt", value="")

    client_module = import_module("seedream_mcp.client")
    client_cls = getattr(client_module, "SeedreamClient")
    monkeypatch.setattr(client_cls, "text_to_image", failing_method)

    result = await handle_text_to_image(TextToImageInput(prompt="test"), _build_config())

    assert result.isError is True
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["status"] == "failed"


def test_add_usage_value_recursively_accumulates_nested_dict_subkeys() -> None:
    """_add_usage_value 对嵌套 dict 值递归累加子键：多次传入同一嵌套键时数值子键累加。"""
    usage: dict[str, Any] = {}
    _add_usage_value(usage, "tool_usage", {"web_search": 5})
    _add_usage_value(usage, "tool_usage", {"web_search": 5})

    assert usage["tool_usage"]["web_search"] == 10


def test_add_usage_value_scalar_numeric_accumulates_and_bool_str_skipped() -> None:
    """标量数值累加；bool 与 str 跳过不写入，避免污染汇总。"""
    usage: dict[str, Any] = {}
    _add_usage_value(usage, "generated_images", 2)
    _add_usage_value(usage, "generated_images", 3)
    _add_usage_value(usage, "cached", True)
    _add_usage_value(usage, "model", "doubao-seedream-5.0")

    assert usage["generated_images"] == 5
    assert "cached" not in usage
    assert "model" not in usage


def test_add_usage_value_deepcopies_dict_when_existing_is_non_dict() -> None:
    """value 为 dict 但现有值为非 dict 时，deepcopy 覆盖写入并断开引用。"""
    usage: dict[str, Any] = {"count": 5}
    incoming = {"tool_usage": {"web_search": 1}}
    _add_usage_value(usage, "count", incoming)

    assert usage["count"] == {"tool_usage": {"web_search": 1}}
    # deepcopy 后修改源 dict 不影响已写入的聚合结果
    incoming["tool_usage"]["web_search"] = 999
    assert usage["count"]["tool_usage"]["web_search"] == 1


def test_aggregate_parallel_generation_results_deep_merges_usage() -> None:
    """聚合多请求 usage：嵌套 dict 子键递归累加，标量数值累加，bool/str 跳过。"""
    request_results = [
        {
            "success": True,
            "data": [{"url": "https://example.com/1.png"}],
            "usage": {
                "tool_usage": {"web_search": 3},
                "generated_images": 2,
                "cached": True,
                "model": "doubao-seedream-5.0",
            },
            "status": "completed",
        },
        {
            "success": True,
            "data": [{"url": "https://example.com/2.png"}],
            "usage": {
                "tool_usage": {"web_search": 4},
                "generated_images": 3,
                "cached": True,
                "model": "doubao-seedream-5.0",
            },
            "status": "completed",
        },
    ]

    aggregated = aggregate_parallel_generation_results(
        request_results=request_results,
        request_errors={},
    )
    usage = aggregated["usage"]

    # 嵌套 dict 子键与标量数值均为各请求之和
    assert usage["tool_usage"]["web_search"] == 7
    assert usage["generated_images"] == 5
    # bool 与 str 标量被跳过，不出现在聚合 usage 中
    assert "cached" not in usage
    assert "model" not in usage
