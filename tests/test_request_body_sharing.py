"""并行批次请求体共享语义测试。

同一工具调用的多个并行请求经共享请求计划只构建一次 request_data、只序列化一次
body，各请求复用同一 bytes 对象；批次结束后计划释放，跨调用互不共享。网络层经
monkeypatch 注入，不触达真实 API。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from seedream_mcp.client import SeedreamClient, SharedRequestPlan
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.schemas import TextToImageInput
from seedream_mcp.tools.impl.text_to_image import handle_text_to_image
from seedream_mcp.utils.core.errors import (
    SeedreamValidationError,
    format_error_for_user,
    resolve_error_profile,
)

import seedream_mcp.client as client_module


def _build_config() -> SeedreamConfig:
    # 关闭自动保存：测试 mock 返回占位 URL，避免对 example.com 发起真实下载拖慢 CI
    return SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False)


def _install_serialize_spy(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    """在类上替换 _serialize_request 为计数替身，返回计数器字典。"""
    calls = {"serialize": 0}
    original = SeedreamClient._serialize_request

    def _spy(request_data: dict[str, Any]) -> bytes:
        calls["serialize"] += 1
        return original(request_data)

    monkeypatch.setattr(SeedreamClient, "_serialize_request", staticmethod(_spy))
    return calls


def _install_build_spy(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    """在类上替换 _build_common_request 为计数替身，返回计数器字典。"""
    calls = {"build": 0}
    original = SeedreamClient._build_common_request

    def _spy(self: Any, **kwargs: Any) -> dict[str, Any]:
        calls["build"] += 1
        return original(self, **kwargs)

    monkeypatch.setattr(SeedreamClient, "_build_common_request", _spy)
    return calls


def _install_send_capture(monkeypatch: pytest.MonkeyPatch, bodies: List[bytes]) -> None:
    """在类上替换 _send_standard_request，记录每个请求实际发送的 body 对象。"""

    async def fake_send(
        self: Any,
        *,
        client: Any,
        url: str,
        request_body: bytes,
        request_timeout: Any,
    ) -> Dict[str, Any]:
        del self, client, url, request_timeout
        bodies.append(request_body)
        # 在途窗口：让同批请求先后进入序列化阶段再完成，模拟真实网络并发
        await asyncio.sleep(0)
        return {
            "success": True,
            "data": [{"url": f"https://example.com/{len(bodies)}.png"}],
            "usage": {},
            "status": "completed",
        }

    monkeypatch.setattr(SeedreamClient, "_send_standard_request", fake_send)


@pytest.mark.asyncio
async def test_parallel_batch_builds_and_serializes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 个并行请求恰好构建一次、序列化一次，且 4 份发送复用同一 bytes 对象。"""
    serialize_calls = _install_serialize_spy(monkeypatch)
    build_calls = _install_build_spy(monkeypatch)
    sent_bodies: List[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    result = await handle_text_to_image(
        TextToImageInput(prompt="parallel", request_count=4, parallelism=4),
        _build_config(),
    )

    assert result.is_error is False
    # 4 个请求都真实发出，共享 body 不代表去重 API 调用
    assert len(sent_bodies) == 4
    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1
    # 各请求复用同一 bytes 对象，而非各持一份等大拷贝
    assert all(body is sent_bodies[0] for body in sent_bodies)
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["batch"]["success_requests"] == 4


@pytest.mark.asyncio
async def test_staggered_parallel_batch_still_serializes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parallelism 小于 request_count 的错峰批次同样只序列化一次。

    信号量错峰下后启动的请求在先完成者之后进入构建阶段，共享计划在批次 gather
    结束前不释放，不得退化为重复构建与序列化。
    """
    serialize_calls = _install_serialize_spy(monkeypatch)
    build_calls = _install_build_spy(monkeypatch)
    sent_bodies: List[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    result = await handle_text_to_image(
        TextToImageInput(prompt="staggered", request_count=4, parallelism=2),
        _build_config(),
    )

    assert result.is_error is False
    assert len(sent_bodies) == 4
    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1
    assert all(body is sent_bodies[0] for body in sent_bodies)


@pytest.mark.asyncio
async def test_single_request_path_behavior_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单请求路径行为不变：构建一次、序列化一次、发送一份 body。"""
    serialize_calls = _install_serialize_spy(monkeypatch)
    build_calls = _install_build_spy(monkeypatch)
    sent_bodies: List[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    result = await handle_text_to_image(TextToImageInput(prompt="single"), _build_config())

    assert result.is_error is False
    assert len(sent_bodies) == 1
    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1


@pytest.mark.asyncio
async def test_consecutive_calls_do_not_share_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批次结束即释放计划：两次独立调用各自序列化，不命中上一批次的陈旧 body。"""
    serialize_calls = _install_serialize_spy(monkeypatch)
    sent_bodies: List[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    first = await handle_text_to_image(TextToImageInput(prompt="first"), _build_config())
    second = await handle_text_to_image(TextToImageInput(prompt="second"), _build_config())

    assert first.is_error is False
    assert second.is_error is False
    assert serialize_calls["serialize"] == 2
    assert len(sent_bodies) == 2
    # 不同批次的 body 是各自序列化的独立对象，内容亦随 prompt 不同
    assert sent_bodies[0] is not sent_bodies[1]
    assert sent_bodies[0] != sent_bodies[1]


@pytest.mark.asyncio
async def test_direct_client_call_without_plan_serializes_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未绑定共享计划的直连 client 调用走独立构建与序列化路径，公共 API 行为不变。"""
    serialize_calls = _install_serialize_spy(monkeypatch)
    build_calls = _install_build_spy(monkeypatch)
    sent_bodies: List[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    async with SeedreamClient(_build_config()) as client:
        await client.text_to_image(prompt="direct")

    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1
    assert len(sent_bodies) == 1


# ==================== 共享计划失败路径 ====================


@pytest.mark.asyncio
async def test_shared_plan_builder_failure_propagates_independently() -> None:
    """builder 抛错时各请求独立收到原异常，计划不写入，后续请求可重试构建。

    锁定 get_or_build 契约：构建失败不污染计划，锁释放后每个调用方在锁内自行
    重试构建，异常原样传播，不被吞掉或合并为共享的一份失败。
    """
    plan = SharedRequestPlan()
    builder_failure = RuntimeError("prepare failed")
    attempts = {"count": 0}

    async def failing_builder() -> dict[str, Any]:
        attempts["count"] += 1
        await asyncio.sleep(0)
        raise builder_failure

    outcomes = await asyncio.gather(
        *(plan.get_or_build(failing_builder) for _ in range(3)),
        return_exceptions=True,
    )

    # 各请求独立收到同一原异常对象，不被包装为其他类型
    assert all(outcome is builder_failure for outcome in outcomes)
    # 每个调用方都在锁内自行重试构建，无一被短路跳过
    assert attempts["count"] == 3
    # 计划未写入失败产物
    assert plan.request_data is None

    # 后续请求可重试构建并成功写入计划
    async def working_builder() -> dict[str, Any]:
        return {"model": "m"}

    built = await plan.get_or_build(working_builder)

    assert built == {"model": "m"}
    assert plan.request_data == {"model": "m"}


@pytest.mark.asyncio
async def test_prevalidate_failure_matches_single_request_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批次级预校验失败经 handle_text_to_image 降级的类型与消息与单请求路径一致。

    预校验在批次分发前上抛，整批以 isError 结果统一降级，不进入逐请求错误聚合；
    错误类型与消息与单请求路径首请求校验失败完全一致。
    """
    validation_failure = SeedreamValidationError("预校验拒绝", field="size", value="bad")

    def failing_validate(**kwargs: Any) -> Any:
        del kwargs
        raise validation_failure

    monkeypatch.setattr(client_module, "validate_common_generation_params", failing_validate)

    config = _build_config()

    # 单请求路径首请求失败：直连调用在生成方法入口校验公共参数并原样上抛
    single_exc: BaseException | None = None
    try:
        async with SeedreamClient(config) as client:
            await client.text_to_image(prompt="p")
    except Exception as exc:
        single_exc = exc

    assert isinstance(single_exc, SeedreamValidationError)
    assert single_exc is validation_failure

    # 批次路径：预校验失败在分发前上抛，经 handler 统一降级为整批错误结果
    batch_result = await handle_text_to_image(
        TextToImageInput(prompt="p", request_count=3, parallelism=3),
        config,
    )

    assert batch_result.is_error is True
    assert isinstance(batch_result.structured_content, dict)
    batch_error = batch_result.structured_content["error"]
    assert batch_error["message"] == format_error_for_user(single_exc)
    assert batch_error["type"] == resolve_error_profile(single_exc).error_code

    # 单请求经同一 handler 的降级结果与批次完全一致，消费方无需按批次数区分错误形态
    single_result = await handle_text_to_image(TextToImageInput(prompt="p"), config)

    assert single_result.is_error is True
    assert single_result.structured_content == batch_result.structured_content
