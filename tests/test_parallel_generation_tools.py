"""生成类工具并行请求支持、进度序列、schema 约束与失败结果封装测试。"""

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core._helpers import (
    PROGRESS_AUTOSAVE_DONE,
    PROGRESS_AUTOSAVE_START,
    PROGRESS_COMPLETE,
    PROGRESS_GENERATION_DONE,
    PROGRESS_GENERATION_START,
    PROGRESS_RECEIVED,
    PROGRESS_SCAN_SPAN,
    PROGRESS_SCAN_START,
    PROGRESS_VALIDATED,
    _add_usage_value,
)
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
from seedream_mcp.utils.io import io_save

# 参数化用例覆盖的四种生成工具输入模型，供 handler 与 params 参数共用注解。
ParallelHandlerParams = (
    TextToImageInput | ImageToImageInput | MultiImageFusionInput | SequentialGenerationInput
)


def _build_config() -> SeedreamConfig:
    # 关闭自动保存：测试 mock 返回占位 URL，避免对 example.com 发起真实下载拖慢 CI
    return SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False)


def _success_result(url: str) -> dict[str, Any]:
    """构造 client 生成方法返回的标准单图成功 dict。"""
    return {
        "success": True,
        "data": [{"url": url}],
        "usage": {"generated_images": 1},
        "status": "completed",
    }


def _patch_generation_success(
    monkeypatch: pytest.MonkeyPatch, method_name: str = "text_to_image"
) -> None:
    """monkeypatch SeedreamClient 指定生成方法返回固定单图成功结果。"""

    async def fake_method(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        return _success_result("https://example.com/1.png")

    monkeypatch.setattr(SeedreamClient, method_name, fake_method)


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
    handler: Callable[[ParallelHandlerParams, SeedreamConfig], Awaitable[CallToolResult]],
    method_name: str,
    params: ParallelHandlerParams,
) -> None:
    """四个生成工具的 handler 均支持并行请求，按 request_count 分发并汇总。"""
    call_count = 0

    async def fake_method(self: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        del self, kwargs
        call_count += 1
        return _success_result(f"https://example.com/{call_count}.png")

    monkeypatch.setattr(SeedreamClient, method_name, fake_method)

    result = await handler(params, _build_config())

    assert call_count == 3
    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    response_text = next(
        content.text for content in result.content if isinstance(content, TextContent)
    )
    assert "并行请求信息:" in response_text
    assert "请求总数: 3" in response_text
    assert "成功请求: 3" in response_text
    assert result.structured_content["request_count"] == 3


async def test_parallel_requests_partial_failure_recorded_in_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N 个并行请求中单个失败：其余成功完成，异常进入 batch.errors，status 为 partial。"""
    call_count = 0

    async def fake_method(self: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        del self, kwargs
        call_count += 1
        # 第 2 次调用模拟单请求失败，其余成功；因并行执行顺序非确定，仅断言失败计数
        if call_count == 2:
            raise SeedreamAPIError("模拟单请求失败", status_code=500)
        return _success_result(f"https://example.com/{call_count}.png")

    monkeypatch.setattr(SeedreamClient, "text_to_image", fake_method)

    # 关闭自动保存以避免对占位 URL 发起真实下载，聚焦并行失败聚合断言
    config = SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False)
    result = await handle_text_to_image(
        TextToImageInput(prompt="test", request_count=3, parallelism=2),
        config,
    )

    assert call_count == 3
    # 部分成功：有任一请求成功即 success=True，isError 为 False
    assert result.is_error is False
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "partial"
    batch = result.structured_content["batch"]
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


class _ProgressCollectingContext:
    """收集 report_progress 调用序列的替身 ctx。

    request_context 属性缺失使流水线回退按需新建客户端；report_progress 仅记录进度值。
    """

    def __init__(self) -> None:
        self.progress_values: list[float] = []

    @property
    def request_context(self) -> Any:
        raise AttributeError("测试替身不提供请求上下文")

    async def report_progress(self, *, progress: float, total: float, message: str) -> None:
        del total, message
        self.progress_values.append(progress)


async def test_parallel_batch_progress_strictly_increasing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并行批次全程进度值严格递增，PROGRESS_GENERATION_DONE 恰好上报一次。

    进度规范要求严格递增且不重复；收尾曾重报 70.0，与末请求完成的 70.0 相邻重复。
    """

    _patch_generation_success(monkeypatch)

    ctx = _ProgressCollectingContext()
    result = await handle_text_to_image(
        TextToImageInput(prompt="test", request_count=3, parallelism=2),
        _build_config(),
        ctx,
    )

    assert result.is_error is False
    values = ctx.progress_values
    assert all(left < right for left, right in zip(values, values[1:]))
    assert values.count(PROGRESS_GENERATION_DONE) == 1
    assert values[-1] == PROGRESS_COMPLETE


class _ReorderingProgressContext:
    """模拟慢客户端交错送达的进度收集替身。

    首个批次中间进度进入 report_progress 后先让出一次事件循环再记录，后完成请求
    的高值进度可先行送达；未序列化上报时收集序列出现回退。
    """

    def __init__(self) -> None:
        self.progress_values: list[float] = []
        self._yielded = False

    @property
    def request_context(self) -> Any:
        raise AttributeError("测试替身不提供请求上下文")

    async def report_progress(self, *, progress: float, total: float, message: str) -> None:
        del total, message
        if not self._yielded and PROGRESS_GENERATION_START < progress < PROGRESS_GENERATION_DONE:
            self._yielded = True
            await asyncio.sleep(0)
        self.progress_values.append(progress)


async def test_parallel_batch_progress_delivery_order_strictly_increasing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """慢客户端交错送达下，并行批次的进度通知仍按严格递增顺序到达。

    进度按完成数快照计算、快照与发送间隔着 await，上报经批次级锁序列化。
    """

    _patch_generation_success(monkeypatch)

    ctx = _ReorderingProgressContext()
    result = await handle_text_to_image(
        TextToImageInput(prompt="test", request_count=3, parallelism=2),
        _build_config(),
        ctx,
    )

    assert result.is_error is False
    values = ctx.progress_values
    assert all(left < right for left, right in zip(values, values[1:]))
    # 交错替身确实触发过让出，保证本测试覆盖的是慢客户端交错路径而非顺序快路径。
    assert ctx._yielded is True


async def test_single_request_progress_full_sequence_with_auto_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """单请求成功路径在 auto_save 开启下的完整进度序列恰为七个里程碑且严格递增。"""

    _patch_generation_success(monkeypatch)

    async def fake_save_multiple(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, tool_name
        return [
            io_save.AutoSaveResult(
                success=True,
                original_url=images[0]["url"],
                local_path=str(tmp_path / "saved.png"),
                markdown_ref="![image](saved.png)",
            )
        ]

    monkeypatch.setattr(io_save.AutoSaveManager, "save_multiple_images", fake_save_multiple)

    # 关闭预览聚焦进度序列，预览分支不新增进度上报。
    config = SeedreamConfig(
        api_key="test_key",
        max_retries=1,
        auto_save_base_dir=str(tmp_path),
        preview_enabled=False,
    )
    ctx = _ProgressCollectingContext()
    result = await handle_text_to_image(TextToImageInput(prompt="test"), config, ctx)

    assert result.is_error is False
    values = ctx.progress_values
    assert all(left < right for left, right in zip(values, values[1:]))
    assert values == [
        PROGRESS_RECEIVED,
        PROGRESS_VALIDATED,
        PROGRESS_GENERATION_START,
        PROGRESS_GENERATION_DONE,
        PROGRESS_AUTOSAVE_START,
        PROGRESS_AUTOSAVE_DONE,
        PROGRESS_COMPLETE,
    ]


async def test_single_request_progress_without_auto_save_jumps_70_to_100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_save 关闭时进度从生成完成直接跳到请求处理完成，序列仍严格递增。"""

    _patch_generation_success(monkeypatch)

    ctx = _ProgressCollectingContext()
    result = await handle_text_to_image(TextToImageInput(prompt="test"), _build_config(), ctx)

    assert result.is_error is False
    values = ctx.progress_values
    assert all(left < right for left, right in zip(values, values[1:]))
    # 70 到 100 的跳变保持严格递增，自动保存两档不再上报。
    assert PROGRESS_AUTOSAVE_START not in values
    assert PROGRESS_AUTOSAVE_DONE not in values
    assert values[values.index(PROGRESS_GENERATION_DONE) + 1] == PROGRESS_COMPLETE
    assert values[-1] == PROGRESS_COMPLETE


async def test_context_failure_progress_jumps_directly_to_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """上下文构建失败的错误路径进度从已接收直达完成，两值序列仍严格递增。"""

    async def unexpected_call(self: Any, **kwargs: Any) -> None:
        del self, kwargs
        raise AssertionError("上下文构建失败不应触达生成请求")

    monkeypatch.setattr(SeedreamClient, "text_to_image", unexpected_call)

    base = tmp_path / "save_root"
    base.mkdir()
    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(base))
    ctx = _ProgressCollectingContext()
    result = await handle_text_to_image(
        TextToImageInput(prompt="test", save_path="../../outside"), config, ctx
    )

    assert result.is_error is True
    assert ctx.progress_values == [PROGRESS_RECEIVED, PROGRESS_COMPLETE]


def test_progress_milestone_constants_strictly_increasing() -> None:
    """生成阶梯七个里程碑常量严格递增，浏览阶梯峰值不越过完成里程碑。

    PROGRESS_SCAN_SPAN 是跨度增量而非里程碑，不参与排序，单独约束峰值区间。
    """
    milestones = [
        PROGRESS_RECEIVED,
        PROGRESS_VALIDATED,
        PROGRESS_GENERATION_START,
        PROGRESS_GENERATION_DONE,
        PROGRESS_AUTOSAVE_START,
        PROGRESS_AUTOSAVE_DONE,
        PROGRESS_COMPLETE,
    ]
    assert all(left < right for left, right in zip(milestones, milestones[1:]))
    assert PROGRESS_SCAN_START < PROGRESS_SCAN_START + PROGRESS_SCAN_SPAN < PROGRESS_COMPLETE


def test_parallel_options_reject_request_count_over_limit_in_schema() -> None:
    """request_count 超上限 10 被 schema 拒绝。"""
    with pytest.raises(ValidationError, match="request_count"):
        TextToImageInput(prompt="test", request_count=11)


def test_parallel_options_reject_parallelism_greater_than_request_count_in_schema() -> None:
    """parallelism 大于 request_count 被 schema 拒绝。"""
    with pytest.raises(ValidationError, match="parallelism 不能大于 request_count"):
        TextToImageInput(prompt="test", request_count=2, parallelism=3)


def test_parallel_options_reject_stream_with_parallel_requests_in_schema() -> None:
    """并行请求与 stream 互斥，schema 层拒绝组合。"""
    with pytest.raises(ValidationError, match="stream=true 时 request_count 必须为 1"):
        TextToImageInput(prompt="test", request_count=2, stream=True)


async def test_generation_handler_returns_call_tool_error_result_when_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """底层校验异常翻为 CallToolResult 工具错误，不向外抛。"""

    async def failing_method(self: Any, **kwargs: Any) -> None:
        del self, kwargs
        raise SeedreamValidationError("提示词不能为空", field="prompt", value="")

    monkeypatch.setattr(SeedreamClient, "text_to_image", failing_method)

    result = await handle_text_to_image(TextToImageInput(prompt="test"), _build_config())

    assert result.is_error is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["status"] == "failed"


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


def test_aggregate_all_failed_requests_reuse_exception_error_code() -> None:
    """并行全失败时 error.type 取代表异常的归约错误码，与单发路径契约一致。"""
    aggregated = aggregate_parallel_generation_results(
        request_results=[None],
        request_errors={1: SeedreamAPIError("x", status_code=401)},
    )

    assert aggregated["success"] is False
    assert aggregated["status"] == "failed"
    assert aggregated["error"]["type"] == "auth_error"


def test_aggregate_all_failed_without_exceptions_falls_back_to_generation_failed() -> None:
    """无异常映射的全失败批次回落 generation_failed 兜底码。"""
    aggregated = aggregate_parallel_generation_results(
        request_results=[
            {"success": False, "status": "failed", "data": [], "error": {"code": "E"}}
        ],
        request_errors={},
    )

    assert aggregated["success"] is False
    assert aggregated["error"]["type"] == "generation_failed"


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
