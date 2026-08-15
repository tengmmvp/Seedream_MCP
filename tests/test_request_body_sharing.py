"""并行批次请求体共享语义测试。

同一工具调用的多个并行请求经共享请求计划只构建一次 request_data、只序列化一次
body，各请求复用同一 bytes 对象；批次结束后计划释放，跨调用互不共享。网络层经
monkeypatch 注入，不触达真实 API。

被替换的类属性在测试运行时经 import_module 动态解析：test_package_lazy_import
会重载 client 模块，模块级类引用会成为过期对象导致补丁失效。
"""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any, Dict, List

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.schemas import TextToImageInput
from seedream_mcp.tools.impl.text_to_image import handle_text_to_image


def _client_cls() -> Any:
    """取当前 sys.modules 中的 SeedreamClient 类，规避 client 模块被重载后的过期引用。"""
    return getattr(import_module("seedream_mcp.client"), "SeedreamClient")


def _build_config() -> SeedreamConfig:
    # 关闭自动保存：测试 mock 返回占位 URL，避免对 example.com 发起真实下载拖慢 CI
    return SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False)


def _install_serialize_spy(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    """在类上替换 _serialize_request 为计数替身，返回计数器字典。"""
    calls = {"serialize": 0}
    original = _client_cls()._serialize_request

    def _spy(request_data: dict[str, Any]) -> bytes:
        calls["serialize"] += 1
        return original(request_data)

    monkeypatch.setattr(_client_cls(), "_serialize_request", staticmethod(_spy))
    return calls


def _install_build_spy(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    """在类上替换 _build_common_request 为计数替身，返回计数器字典。"""
    calls = {"build": 0}
    original = _client_cls()._build_common_request

    def _spy(self: Any, **kwargs: Any) -> dict[str, Any]:
        calls["build"] += 1
        return original(self, **kwargs)

    monkeypatch.setattr(_client_cls(), "_build_common_request", _spy)
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

    monkeypatch.setattr(_client_cls(), "_send_standard_request", fake_send)


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

    assert result.isError is False
    # 4 个请求都真实发出，共享 body 不代表去重 API 调用
    assert len(sent_bodies) == 4
    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1
    # 各请求复用同一 bytes 对象，而非各持一份等大拷贝
    assert all(body is sent_bodies[0] for body in sent_bodies)
    assert isinstance(result.structuredContent, dict)
    assert result.structuredContent["batch"]["success_requests"] == 4


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

    assert result.isError is False
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

    assert result.isError is False
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

    assert first.isError is False
    assert second.isError is False
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

    async with _client_cls()(_build_config()) as client:
        await client.text_to_image(prompt="direct")

    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1
    assert len(sent_bodies) == 1
