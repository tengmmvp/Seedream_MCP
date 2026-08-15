"""上游响应体读取限额域守护测试。

覆盖错误体独立小上限、错误体 JSON 解析线程卸载、非 JSON 错误体 message 截断、
response_body_limit 显式配置与推导、SSE 事件截断阈值的 base64 严格上界，以及
流式 JSON 超限错误不被解析失败包装的回归锁定。网络层经 httpx.MockTransport 模拟，
不触达真实 API。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

import httpx
import pytest

import seedream_mcp.client as client_module
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamAPIError

# 错误路径读体独立上限，与 client._ERROR_BODY_BYTE_LIMIT 保持一致。
_ERROR_BODY_CAP = 4 * 1024 * 1024


async def _install_mock_transport(client: SeedreamClient, handler: Callable[[Any], Any]) -> None:
    """关闭内部 httpx 客户端并替换为 MockTransport 驱动的实例。"""
    assert client._client is not None
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_error_body_over_independent_cap_rejected(no_sleep: None) -> None:
    """非 200 的 chunked 错误体超过 4MB 独立上限时在累计读取中拦截。

    5MB 错误体远低于默认图片级总量上限 1GB，命中只能来自错误路径独立上限。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream():
            for _ in range(5 * 1024):
                yield b"x" * 1024

        return httpx.Response(400, content=_stream())

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        assert "已读取" in exc_info.value.message
        assert str(_ERROR_BODY_CAP) in exc_info.value.message
        assert "SEEDREAM_RESPONSE_BODY_LIMIT" in exc_info.value.message


async def test_error_body_declared_length_over_independent_cap(no_sleep: None) -> None:
    """非 200 错误体 Content-Length 声明超过 4MB 时无需读取即拒绝。

    声明的 10MB 低于默认图片级总量上限 1GB，拒绝只能来自错误路径独立上限。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            500,
            headers={"content-length": str(10 * 1024 * 1024)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="Content-Length") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        assert str(_ERROR_BODY_CAP) in exc_info.value.message


async def test_error_body_json_parse_offloaded_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """错误体 json.loads 在工作线程执行，解析期间事件循环保持可调度。

    对含标记的错误体注入 0.15 秒解析延迟：若解析回到事件循环，自旋心跳任务在
    解析窗口内将被饿死；线程卸载则心跳持续计数。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)
    body = json.dumps({"message": "x" * (2 * 1024 * 1024)}).encode()

    class _SlowJsonModule:
        """仅对含标记的错误体注入延迟，其余 json 属性原样透传。"""

        def __init__(self, real: Any, marker: bytes) -> None:
            self._real = real
            self._marker = marker

        def loads(self, data: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(data, (bytes, bytearray)) and self._marker in data:
                time.sleep(0.15)
            return self._real.loads(data, *args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    monkeypatch.setattr(client_module, "json", _SlowJsonModule(json, b'"message"'))

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, content=body, headers={"content-type": "application/json"})

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        ticker = asyncio.create_task(_ticker())
        try:
            with pytest.raises(SeedreamAPIError, match="请求参数错误") as exc_info:
                await client._call_api("text_to_image", {"prompt": "p"})
        finally:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass

        assert ticks > 5
        # 2MB 上游 message 片段拼入异常文案前被截断为 8KB
        assert "<truncated:" in exc_info.value.message
        assert len(exc_info.value.message) < 10 * 1024


async def test_non_json_error_body_message_truncated(no_sleep: None) -> None:
    """非 JSON 错误体降级为 message 后截断至 8KB，异常 message 总长度受限。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    body = b"y" * (2 * 1024 * 1024)  # 低于 4MB 读体上限的非 JSON 文本

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, content=body, headers={"content-type": "text/plain"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError) as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        message = exc_info.value.message
        assert "<truncated:2097152 chars>" in message
        assert len(message) < 10 * 1024


async def test_response_body_limit_explicit_config_overrides_derivation(no_sleep: None) -> None:
    """显式 response_body_limit 直接生效，不再按 auto_save_max_file_size × 20 推导。"""
    config = SeedreamConfig(
        api_key="k",
        max_retries=3,
        auto_save_max_file_size=1024,
        response_body_limit=8192,
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": str(16 * 1024)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        # 16KB 超过显式上限 8192 但低于推导值 20480，命中说明显式配置生效
        assert str(8192) in exc_info.value.message


async def test_response_body_limit_derived_from_file_size(no_sleep: None) -> None:
    """未配置 response_body_limit 时按 auto_save_max_file_size × 20 推导生效。"""
    config = SeedreamConfig(api_key="k", max_retries=3, auto_save_max_file_size=1024)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": str(20 * 1024 + 1)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        assert str(20 * 1024) in exc_info.value.message


async def test_stream_event_at_exact_base64_bound_not_truncated(no_sleep: None) -> None:
    """解码后恰好 n 字节的边界事件（n mod 3 = 1）不被截断。

    n mod 3 余 1 时旧近似式 ⌈4n/3⌉ 比真实 base64 长度 4⌈n/3⌉ 小 2 字节，
    叠加 data: 前缀与 JSON 包络后旧阈值会误截断本用例的合法事件。
    """
    n = 100_000
    b64_len = 4 * ((n + 2) // 3)  # n 字节负载的 base64 精确长度
    event_bytes = (
        b'data: {"type":"image_generation.partial_succeeded","b64_json":"'
        + b"A" * b64_len
        + b'"}\n\n'
        + b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n'
    )
    # 事件总长（含信封）超过旧公式阈值，证明本用例落在旧公式的误截断区间
    assert len(event_bytes) > (n * 4 + 2) // 3

    config = SeedreamConfig(
        api_key="k",
        max_retries=3,
        auto_save_max_file_size=n,
        stream_chunk_size=64,
        stream_buffer_max_size=1024,
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=event_bytes,
            headers={"content-type": "text/event-stream"},
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        result = await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert result["truncated_events"] == 0
    assert len(result["data"]) == 1
    assert len(result["data"][0]["b64_json"]) == b64_len


async def test_stream_json_over_limit_error_not_wrapped_as_parse_failure(no_sleep: None) -> None:
    """流式 JSON 响应超限时「响应体过大」原文上抛，不被 JSON 解析失败包装。"""
    config = SeedreamConfig(api_key="k", max_retries=3, auto_save_max_file_size=1024)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream():
            for _ in range(40):  # 40KB，超过推导上限 20480
                yield b"x" * 1024

        return httpx.Response(200, content=_stream(), headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

        assert "JSON 解析失败" not in exc_info.value.message
        assert "已读取" in exc_info.value.message
        assert exc_info.value.status_code is None
