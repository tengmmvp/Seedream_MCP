"""上游响应体读取限额域守护测试。

覆盖错误体独立上限、错误体 JSON 线程卸载、message 截断、response_body_limit
显式配置与推导、SSE 截断阈值上界与流式超限错误语义。网络层经 httpx.MockTransport
模拟，不触达真实 API。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

import seedream_mcp._client_http as client_module
from seedream_mcp._client_http import _ERROR_BODY_BYTE_LIMIT as _ERROR_BODY_CAP
from seedream_mcp._client_http import _ERROR_JSON_PARSE_LIMIT
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamAPIError, SeedreamTimeoutError

from _client_fakes import _install_mock_transport


async def test_error_body_over_independent_cap_rejected(no_sleep: None) -> None:
    """非 200 的 chunked 错误体超过 4MB 独立上限时在累计读取中拦截。

    5MB 错误体远低于默认图片级总量上限 1GB，命中只能来自错误路径独立上限。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream() -> AsyncIterator[bytes]:
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

    对含标记的错误体注入 0.15 秒解析延迟，并在延迟前后快照心跳计数：解析若回归
    到事件循环内执行，窗口内心跳增量为零；卸载到工作线程时事件循环持续调度，
    增量为正。全程计数不足以守护：前置 HTTP 收发已使计数越过阈值，阻塞回归时
    总量断言仍通过。错误体控制在 _ERROR_JSON_PARSE_LIMIT 之内，确保走完整 dict
    解析分支而非降级为纯 message；大错误体的 message 截断由
    test_non_json_error_body_message_truncated 锁定。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)
    body = json.dumps({"message": "x" * (_ERROR_JSON_PARSE_LIMIT // 2)}).encode()
    assert len(body) <= _ERROR_JSON_PARSE_LIMIT
    ticks = 0
    parse_window_ticks: dict[str, int] = {}

    class _SlowJsonModule:
        """仅对含标记的错误体注入延迟并快照心跳计数，其余 json 属性原样透传。"""

        def __init__(self, real: Any, marker: bytes) -> None:
            self._real = real
            self._marker = marker

        def loads(self, data: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(data, (bytes, bytearray)) and self._marker in data:
                parse_window_ticks["before"] = ticks
                time.sleep(0.15)
                parse_window_ticks["after"] = ticks
            return self._real.loads(data, *args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    monkeypatch.setattr(client_module, "json", _SlowJsonModule(json, b'"message"'))

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, content=body, headers={"content-type": "application/json"})

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        ticker = asyncio.create_task(_ticker())
        try:
            with pytest.raises(SeedreamAPIError, match="请求参数错误"):
                await client._call_api("text_to_image", {"prompt": "p"})
        finally:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass

        assert "after" in parse_window_ticks, "含标记的错误体未经过被注入延迟的 json.loads"
        window_delta = parse_window_ticks["after"] - parse_window_ticks["before"]
        assert window_delta > 0, "解析延迟期间事件循环被阻塞，json.loads 未卸载到工作线程"


async def test_non_json_error_body_message_truncated(no_sleep: None) -> None:
    """非 JSON 错误体降级为 message 后截断至 64KB，异常 message 总长度受限。"""
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
        assert len(message) < 10 * 1024
        degraded = exc_info.value.response_data["message"]
        assert "截断，原文 2097152 字节" in degraded
        assert len(degraded) < 70 * 1024


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
    """解码后恰好 n 字节且 n mod 3 = 1 的边界事件不被截断。

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
    # 事件总长含信封，超过旧公式阈值，证明本用例落在旧公式的误截断区间
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

    # 无截断时不写 truncated_events 键：跨组契约约定 0 或缺省不携带该键，
    # results/outputs 侧按键存在性渲染截断提示
    assert "truncated_events" not in result
    assert len(result["data"]) == 1
    assert len(result["data"][0]["b64_json"]) == b64_len


async def test_stream_truncated_events_passed_through_in_payload(no_sleep: None) -> None:
    """SSE 事件因超限被丢弃时，truncated_events 计数透传进 api result payload。

    单个未完成事件超过阈值即被丢弃并计数；首个分块送出 6KB 无分隔符事件，超过
    压缩配置后的阈值触发截断，随后的 completed 事件正常解析。
    """
    config = SeedreamConfig(
        api_key="k",
        max_retries=3,
        auto_save_max_file_size=1024,
        stream_buffer_max_size=1024,
        stream_chunk_size=64,
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream() -> AsyncIterator[bytes]:
            # 不完整事件：无空行分隔，超过阈值后被丢弃并计数
            yield b"data: " + b"A" * 6000
            yield b'data: {"type":"image_generation.completed","usage":{}}\n\n'

        return httpx.Response(200, content=_stream(), headers={"content-type": "text/event-stream"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        result = await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert result["truncated_events"] == 1
    assert isinstance(result["truncated_events"], int)
    # 事件被丢弃使结果不完整，status 须标记 partial
    assert result["status"] == "partial"


async def test_stream_json_over_limit_error_not_wrapped_as_parse_failure(no_sleep: None) -> None:
    """流式 JSON 响应超限时「响应体过大」原文上抛，不被 JSON 解析失败包装。"""
    config = SeedreamConfig(api_key="k", max_retries=3, auto_save_max_file_size=1024)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream() -> AsyncIterator[bytes]:
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


async def test_success_body_large_json_parses_from_bytearray(
    no_sleep: None,
) -> None:
    """大体积成功 JSON 体经 bytearray 单份缓冲读取并正常解析。

    读体不再有 chunks 列表与 join 合并，大响应体无双重驻留与合并阻塞。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)
    # 9MB 成功 JSON 体，低于默认总量上限
    payload = b'{"x":"' + b"a" * (9 * 1024 * 1024) + b'"}'

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)
        result = await client._call_api("text_to_image", {"prompt": "p"})

    assert result["success"] is True


async def _delay_outside_patched_sleep(seconds: float) -> None:
    """经未打补丁的定时器实现真实延迟，绕开 no_sleep fixture 对 asyncio.sleep 的屏蔽。

    慢滴流要求块间存在真实时间间隔，重试退避又须被 no_sleep 跳过，两种等待
    不能共用同一个 asyncio.sleep 入口。等待方被取消后定时器仍会触发，set 前查
    完成态避免 InvalidStateError 噪音。
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def _finish(target: asyncio.Future[None]) -> None:
        if not target.done():
            target.set_result(None)

    loop.call_later(seconds, _finish, future)
    await future


async def test_sse_slow_drip_over_total_budget_times_out_and_retries(no_sleep: None) -> None:
    """SSE 慢滴流零产出触发总时长预算并按超时重试，耗尽后归一为超时异常。

    首块即延迟超过总预算，流全程零完整事件，等块限时在约 1 秒处命中总时长
    预算；无该约束时读取会无限等下去。
    """
    config = SeedreamConfig(api_key="k", max_retries=1, api_timeout=1)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        async def _stream() -> AsyncIterator[bytes]:
            await _delay_outside_patched_sleep(1.2)
            yield b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
            while True:
                await _delay_outside_patched_sleep(0.3)
                yield (
                    b'data: {"type":"image_generation.partial_succeeded",'
                    b'"url":"http://x/1.png"}\n\n'
                )

        return httpx.Response(200, content=_stream(), headers={"content-type": "text/event-stream"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamTimeoutError, match="API 调用超时"):
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    # 首次超时被重试而非立即上抛，重试耗尽后归一为超时异常
    assert attempts == config.max_retries + 1


async def test_sse_slow_drip_with_partial_results_terminates_gracefully(
    no_sleep: None,
) -> None:
    """SSE 慢滴流已产出部分结果时按预算优雅终止，不重试不抛错。

    首块为超过读取重组块大小的完整大事件，立即产出一条结果；后续块间隔超过
    剩余预算，等块限时命中后保留已收结果并携带 deadline_exceeded 返回；已计费
    结果不得因超时重试。
    """
    config = SeedreamConfig(api_key="k", max_retries=1, api_timeout=1)
    attempts = 0
    # 首块凑满一个读取重组块：完整小事件随块立即产出，垫脚为未完成事件开头，
    # 终结符滞留重组缓冲不影响已产出事件。
    chunk = 1024 * 1024
    first_event = b'data: {"type":"image_generation.partial_succeeded","url":"http://x/1.png"}\n\n'
    head = first_event + b'data: {"padding":"' + b"x" * (chunk - len(first_event) - 18)

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        async def _stream() -> AsyncIterator[bytes]:
            yield head
            while True:
                await _delay_outside_patched_sleep(0.6)
                yield (
                    b'data: {"type":"image_generation.partial_succeeded",'
                    b'"url":"http://x/2.png"}\n\n'
                )

        return httpx.Response(200, content=_stream(), headers={"content-type": "text/event-stream"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        result = await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert attempts == 1
    assert result["deadline_exceeded"] is True
    assert result["data"][0]["url"] == "http://x/1.png"


async def test_standard_slow_drip_json_over_total_budget_times_out(no_sleep: None) -> None:
    """非流式 JSON 慢滴流由读体截止时间封顶，超预算同样按超时重试。"""
    config = SeedreamConfig(api_key="k", max_retries=1, api_timeout=1)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        async def _stream() -> AsyncIterator[bytes]:
            yield b"{"
            while True:
                await _delay_outside_patched_sleep(0.3)
                yield b"x" * 64

        return httpx.Response(200, content=_stream(), headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamTimeoutError, match="API 调用超时"):
            await client._call_api("text_to_image", {"prompt": "p"})

    assert attempts == config.max_retries + 1


async def test_stream_header_stall_over_api_timeout_times_out(no_sleep: None) -> None:
    """流式路径响应头就绪慢于 api_timeout 时按超时重试，耗尽归一为超时异常。

    头部就绪阶段只受 httpx 单次 read 超时保护，由发送路径的截止时间预算封顶。
    """
    config = SeedreamConfig(api_key="k", max_retries=1, api_timeout=1)
    attempts = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        del request
        nonlocal attempts
        attempts += 1
        await _delay_outside_patched_sleep(1.5)
        return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamTimeoutError, match="API 调用超时"):
            await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert attempts == config.max_retries + 1


async def test_standard_header_stall_over_api_timeout_times_out(no_sleep: None) -> None:
    """非流式路径响应头就绪慢于 api_timeout 时同样按超时重试。"""
    config = SeedreamConfig(api_key="k", max_retries=1, api_timeout=1)
    attempts = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        del request
        nonlocal attempts
        attempts += 1
        await _delay_outside_patched_sleep(1.5)
        return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamTimeoutError, match="API 调用超时"):
            await client._call_api("text_to_image", {"prompt": "p"})

    assert attempts == config.max_retries + 1


async def test_send_with_header_deadline_prefers_ready_response_on_timeout_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """截止时间到点与响应头就绪同刻竞态时采用已就绪响应，不按超时上抛。

    发送协程在响应入 holder 后无等待挂起点，真实 wait_for 无法确定性复现该
    交错，以完成发送后仍判到期的替身锁定竞态分支的取值方向。
    """
    config = SeedreamConfig(api_key="k")

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    async def _racing_wait_for(coro: Any, timeout: Any = None) -> Any:
        del timeout
        await coro
        # 发送已完成、响应已就绪的同一 tick 上到达期判定
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", _racing_wait_for)

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)
        raw_client = client._client
        assert raw_client is not None
        request = raw_client.build_request("POST", "https://example.com")
        response = await client._send_with_header_deadline(raw_client, request, 1.0)
        try:
            assert response.status_code == 200
        finally:
            await response.aclose()


async def test_malformed_content_length_degrades_to_streamed_read(no_sleep: None) -> None:
    """Content-Length 声明非数值时跳过预检，按流式累计读取并正常解析成功体。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    body = json.dumps({"data": [], "usage": {}, "status": "completed"}).encode()

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, headers={"content-length": "abc"}, content=body)

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)
        result = await client._call_api("text_to_image", {"prompt": "p"})

    assert result["success"] is True


async def test_error_body_read_timeout_reduced_with_status_and_retry_after() -> None:
    """错误状态码下读体超时归约为携带状态码与 Retry-After 的 API 错误。

    经 MockTransport 取得真实流式 429 响应后以已过期的 deadline 直调读体路径，
    首块到达即命中截止判定。
    """
    config = SeedreamConfig(api_key="k", max_retries=0)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, headers={"retry-after": "3"}, content=b"x" * 32)

    async with SeedreamClient(config) as client:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as raw:
            request = raw.build_request("POST", "https://example.com")
            response = await raw.send(request, stream=True)
            try:
                with pytest.raises(
                    SeedreamAPIError, match="读取错误响应体超时（状态码 429）"
                ) as exc_info:
                    await client._raise_for_response_status(
                        response, deadline=time.monotonic() - 1.0
                    )
            finally:
                await response.aclose()

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 3.0


# ==================== 超大错误体的状态码重试语义 ====================


async def test_5xx_oversized_error_body_retries_by_status_code(no_sleep: None) -> None:
    """5xx 超大错误体的响应体过大异常携带状态码，按 5xx 语义重试至耗尽。

    无状态码时 _call_api 对该异常不重试，5xx 大错误体的可重试语义丢失。
    """
    config = SeedreamConfig(api_key="k", max_retries=2)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            500,
            headers={"content-length": str(10 * 1024 * 1024)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="Content-Length") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        assert exc_info.value.status_code == 500
        # 5xx 可重试语义保留：尝试次数为 max_retries + 1
        assert attempts == config.max_retries + 1


async def test_4xx_oversized_error_body_fails_fast(no_sleep: None) -> None:
    """4xx 超大错误体异常携带 400 状态码但非可重试，单次即失败。"""
    config = SeedreamConfig(api_key="k", max_retries=2)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            headers={"content-length": str(10 * 1024 * 1024)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="Content-Length") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"})

        assert exc_info.value.status_code == 400
        # 4xx 非可重试：仅尝试一次
        assert attempts == 1
