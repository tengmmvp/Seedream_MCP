"""core 流水线守卫测试：聚合状态摊平、结构化输出净化与 SSE 错误重试契约。

覆盖四类契约：并行批次内任一请求自身为 partial 时批次 status 至多为 partial；
失败分支非 dict 形态 error 的兜底 message 与 usage 共享引用、未知嵌套键同样过
净化管线；MCP Roots 为空列表时自动保存目录解析归入校验档而非未知错误；
SSE 请求级错误事件按 4xx 终态处理，_call_api 不对其重试。
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest
from mcp.types import TextContent

import seedream_mcp.client as client_module
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core._helpers import _resolve_base_dir
from seedream_mcp.tools.core.context import GenerationExecutionContext
from seedream_mcp.tools.core.results import (
    _build_generation_structured_result,
    _sanitize_image_errors,
    _sanitize_usage,
    aggregate_parallel_generation_results,
)
from seedream_mcp.tools.core.schemas import TextToImageInput
from seedream_mcp.tools.impl.text_to_image import handle_text_to_image
from seedream_mcp.utils.core.errors import (
    SeedreamAPIError,
    SeedreamValidationError,
    format_error_for_user,
)
from seedream_mcp.utils.io import io_path as io_path_module

from _generation_fixtures import make_generation_context


def _context() -> GenerationExecutionContext:
    """构造本文件共用的最小生成执行上下文，关闭自动保存。"""
    return make_generation_context(enable_auto_save=False)


# ==================== 聚合状态摊平 ====================


def test_aggregate_partial_request_status_flattens_batch_to_partial() -> None:
    """任一成功请求自身为 partial 时批次 status 为 partial，不得上报 completed。"""
    request_results = [
        {
            "success": True,
            "data": [{"url": "https://example.com/1.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        },
        {
            "success": True,
            "data": [
                {
                    "type": "image_generation.partial_failed",
                    "image_index": 2,
                    "error": {"code": "blocked", "message": "blocked"},
                }
            ],
            "usage": {"generated_images": 1},
            "status": "partial",
        },
    ]

    aggregated = aggregate_parallel_generation_results(
        request_results=request_results,
        request_errors={},
    )

    assert aggregated["success"] is True
    assert aggregated["status"] == "partial"
    # 两请求均成功，失败计数为 0，partial 仅来自请求自身的部分失败
    assert aggregated["batch"]["failed_requests"] == 0


def test_aggregate_all_completed_requests_report_completed() -> None:
    """全部请求 completed 且无失败时批次 status 保持 completed，不误降级。"""
    request_results = [
        {
            "success": True,
            "data": [{"url": f"https://example.com/{index}.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }
        for index in (1, 2)
    ]

    aggregated = aggregate_parallel_generation_results(
        request_results=request_results,
        request_errors={},
    )

    assert aggregated["status"] == "completed"


def test_aggregate_failed_request_with_top_level_error_extracts_message() -> None:
    """请求级软失败结果进入并行聚合时，错误消息取自透传的顶层 error 而非兜底文案。"""
    request_results = [
        {
            "success": False,
            "status": "failed",
            "data": [],
            "error": {"code": "StreamRejected", "message": "流式请求被拒绝"},
        },
        {
            "success": True,
            "data": [{"url": "https://example.com/1.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        },
    ]

    aggregated = aggregate_parallel_generation_results(
        request_results=request_results,
        request_errors={},
    )

    assert aggregated["success"] is True
    assert aggregated["status"] == "partial"
    assert aggregated["batch"]["failed_requests"] == 1
    assert "流式请求被拒绝" in aggregated["batch"]["errors"][0]["message"]


# ==================== 失败分支与嵌套净化 ====================


def test_structured_non_dict_error_sanitized() -> None:
    """失败分支 error 为非 dict 形态时，兜底 message 同样过净化管线。"""
    result = {
        "success": False,
        "status": "failed",
        "error": "鉴权失败\r\nFAKE api_key=leaked",
    }

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    message = structured["error"]["message"]
    assert message == "鉴权失败  FAKE api_key=***"
    assert "leaked" not in message
    assert "\r" not in message
    assert "\n" not in message


def test_structured_usage_shared_reference_preserved_not_truncated() -> None:
    """多处引用同一对象不构成循环引用，净化后各处引用均完整展开，不误标 <truncated:cyclic>。"""
    shared = {"note": "echo\r\nFAKE"}
    shared_list = ["x\r\ny"]
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": "https://example.com/a.png"}],
        "usage": {"first": shared, "second": shared, "items": [shared_list, shared_list]},
    }

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    usage = structured["usage"]
    assert "<truncated:cyclic>" not in str(usage)
    assert usage["first"]["note"] == "echo  FAKE"
    assert usage["second"]["note"] == "echo  FAKE"
    assert usage["items"][0] == ["x  y"]
    assert usage["items"][1] == ["x  y"]


def test_sanitize_usage_list_root_and_values_sanitized() -> None:
    """list 根与嵌套 list 按下标写回不越界，字符串值同样净化。

    usage 经 client 守卫恒为 dict，本测试锁定净化器自身对 list 形态的防御：
    下标写入落在预置槽位内，不因空列表索引赋值抛 IndexError。
    """
    sanitized = _sanitize_usage(["a\r\nb", {"note": "Bearer sk-1"}, 3])

    assert sanitized == ["a  b", {"note": "Bearer ***"}, 3]


def test_sanitize_image_errors_unknown_nested_containers_sanitized() -> None:
    """未知键值为嵌套容器时递归净化，深处的凭据片段与 CRLF 不进入 structuredContent。"""
    images = [
        {
            "url": "https://example.com/a.png",
            "custom_meta": {
                "note": "hi\r\nFAKE api_key=leaked",
                "flags": ["Bearer sk-1\r\nFAKE", 7],
                "inner": {"path": "images/a.png\r\nFAKE-PATH: injected"},
            },
        }
    ]

    sanitized = _sanitize_image_errors(images)

    meta = sanitized[0]["custom_meta"]
    assert meta["note"] == "hi  FAKE api_key=***"
    assert meta["flags"][0] == "Bearer ***  FAKE"
    assert meta["flags"][1] == 7
    assert meta["inner"]["path"] == "images/a.png  FAKE-PATH: injected"
    assert "leaked" not in str(meta)
    assert "\r" not in str(meta)


def test_sanitize_image_errors_clean_nested_containers_untouched() -> None:
    """嵌套容器内容干净时不产生更新，列表条目对象保持原引用。"""
    images = [{"url": "https://example.com/a.png", "custom": {"count": 7, "tags": ["x"]}}]

    sanitized = _sanitize_image_errors(images)

    assert sanitized[0] is images[0]


# ==================== 空工作区根降级文案 ====================


def test_resolve_base_dir_empty_workspace_roots_maps_to_validation_error() -> None:
    """无 auto_save_base_dir 且 MCP Roots 为空列表时归入校验档，不再呈未知错误。"""
    config = SeedreamConfig(api_key="k")
    token = io_path_module._WORKSPACE_ROOTS_VAR.set(())
    try:
        with pytest.raises(SeedreamValidationError) as excinfo:
            _resolve_base_dir(config, None)
    finally:
        io_path_module._WORKSPACE_ROOTS_VAR.reset(token)

    user_message = format_error_for_user(excinfo.value)
    assert not user_message.startswith("未知错误")
    assert user_message.startswith("参数验证失败")
    assert "工作区" in user_message


# ==================== SSE 请求级错误重试契约 ====================


def _client_with_mock_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> SeedreamClient:
    """构造挂载 MockTransport 的客户端，上游响应由 handler 生成，调用方负责 close。"""
    config = SeedreamConfig(api_key="k", max_retries=2)
    client = SeedreamClient(config)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_sse_request_level_error_event_not_retried(no_sleep: None) -> None:
    """SSE 请求级错误事件按 4xx 终态处理：_call_api 不重试，上游仅收到一次请求。

    io_sse 对请求级错误事件固定标记 status_code=400，与 _call_api 仅 429 与 5xx
    可重试的判定形成隐式契约；该状态码若变为 5xx，已被服务端拒绝并可能计费的
    请求将被静默重试，本测试锁定只发一次请求。
    """
    upstream_calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"error":{"code":"InvalidParameter","message":"bad param"}}\n\n',
        )

    client = _client_with_mock_transport(_handler)
    try:
        with pytest.raises(SeedreamAPIError) as excinfo:
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})
    finally:
        await client.close()

    assert upstream_calls == 1
    assert excinfo.value.status_code == 400


async def test_http_400_api_error_not_retried(no_sleep: None) -> None:
    """非 SSE 路径同契约：携带 status_code 的 4xx API 错误为立即终态，不重试。"""
    upstream_calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(400, json={"error": {"code": "InvalidParameter"}})

    client = _client_with_mock_transport(_handler)
    try:
        with pytest.raises(SeedreamAPIError) as excinfo:
            await client._call_api("text_to_image", {"prompt": "p"})
    finally:
        await client.close()

    assert upstream_calls == 1
    assert excinfo.value.status_code == 400


# ==================== 并行批次前置校验契约 ====================


async def test_parallel_batch_prevalidate_failure_zero_dispatch_and_message_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公共参数前置校验失败时批次零请求分发，错误档位与文案和单请求路径一致。

    锁定 _run_generation_requests 的 prevalidate 分支承诺：校验失败在分发前上抛，
    由外层流水线统一降级为 validation_error 档，不进入逐请求错误聚合。公共参数
    校验失败经替换 validate_common_generation_params 注入；schema 与 context 层的
    同源校验已拦截自然非法输入，测试聚焦批次分发前的拦截行为本身。
    """

    def _failing_validate(**kwargs: Any) -> Any:
        del kwargs
        raise SeedreamValidationError("提示词超过长度上限", field="prompt", value=None)

    monkeypatch.setattr(client_module, "validate_common_generation_params", _failing_validate)

    api_calls = 0

    async def fake_method(self: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal api_calls
        del self, kwargs
        api_calls += 1
        return {"success": True, "data": [], "usage": {}, "status": "completed"}

    monkeypatch.setattr(SeedreamClient, "text_to_image", fake_method)

    config = SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False)
    batch_result = await handle_text_to_image(
        TextToImageInput(prompt="test", request_count=3, parallelism=2), config
    )
    single_result = await handle_text_to_image(TextToImageInput(prompt="test"), config)

    # 分发前上抛：批内三个请求与单请求均未触达生成方法
    assert api_calls == 0
    assert batch_result.is_error is True
    assert single_result.is_error is True
    assert batch_result.structured_content is not None
    assert batch_result.structured_content["error"]["type"] == "validation_error"
    # 失败走外层统一降级分支，未进入逐请求错误聚合
    assert batch_result.structured_content["batch"] is None

    batch_text = next(
        content.text for content in batch_result.content if isinstance(content, TextContent)
    )
    single_text = next(
        content.text for content in single_result.content if isinstance(content, TextContent)
    )
    assert batch_text == single_text
    assert "提示词超过长度上限" in batch_text
