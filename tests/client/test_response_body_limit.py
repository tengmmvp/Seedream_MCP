"""上游响应体读取上限域守护测试。

覆盖错误体独立上限与状态码重试语义、错误体 JSON 解析阈值分派与 message 截断、
response_body_limit 显式配置与按请求形态投影、成功体 data 条目上限、
SSE 截断阈值边界与计数透传、SSE 渐进帧不计入读体上限、
慢滴流与头部停滞的总预算超时、末块到点打捞的完整交付、
持续就绪流的周期性墙钟复查、错误体读取超时与传输错误的状态码归约。
网络层经 httpx.MockTransport 模拟，不触达真实 API。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

import seedream_mcp.config._model as config_model
from seedream_mcp.client._http import _ERROR_BODY_BYTE_LIMIT as _ERROR_BODY_CAP
from seedream_mcp.client._http import _ERROR_JSON_PARSE_LIMIT
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.definitions.conventions import DEFAULT_LAYER_DECOMPOSITION_MAX_IMAGES
from seedream_mcp.utils.core.errors import SeedreamAPIError, SeedreamTimeoutError

from _lib.client_fakes import _install_mock_transport, _partial_image_event
from _lib.cpu_offload_spy import CpuOffloadSpy


def _patch_single_image_bytes(monkeypatch: pytest.MonkeyPatch, single_image_bytes: int) -> None:
    """把配置模块解析的单图最坏字节数替换为给定值，驱动 SSE 阈值推导到小量级。"""
    monkeypatch.setattr(config_model, "max_single_image_bytes", lambda model_id: single_image_bytes)


def _b64_single_image_limit(config: SeedreamConfig) -> int:
    """取 b64_json 单图形态的读体上限投影，作为客户端用例的期望上限来源。"""
    return config.effective_response_body_limit(response_format="b64_json", max_image_count=1)


async def test_error_body_over_independent_cap_rejected(no_sleep: None) -> None:
    """非 200 的 chunked 错误体超过 4MB 独立上限时在累计读取中拦截。

    请求为 b64_json 形态，本请求总量投影约 25MB（默认 5.5 家族单图派生），
    5MB 错误体低于该投影，命中只能来自错误路径独立上限。
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
            await client._call_api(
                "text_to_image",
                {"prompt": "p", "response_format": "b64_json"},
                per_request_image_count=1,
            )

        assert "已读取" in exc_info.value.message
        assert str(_ERROR_BODY_CAP) in exc_info.value.message
        # 错误路径上限被 4MB 钳制，环境变量调大不生效，消息不提示调整。
        assert "SEEDREAM_RESPONSE_BODY_LIMIT" not in exc_info.value.message


async def test_error_body_declared_length_over_independent_cap(no_sleep: None) -> None:
    """非 200 错误体 Content-Length 声明超过 4MB 时无需读取即拒绝。

    请求为 b64_json 形态，本请求总量投影约 25MB（默认 5.5 家族单图派生），
    声明的 10MB 低于该投影，拒绝只能来自错误路径独立上限。
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
            await client._call_api(
                "text_to_image",
                {"prompt": "p", "response_format": "b64_json"},
                per_request_image_count=1,
            )

        assert str(_ERROR_BODY_CAP) in exc_info.value.message


async def test_error_body_hint_kept_when_explicit_limit_below_cap(
    no_sleep: None,
) -> None:
    """显式总量上限低于 4MB 时错误路径生效上限即环境变量，提示保留。"""
    config = SeedreamConfig(api_key="k", max_retries=3, response_body_limit=2 * 1024 * 1024)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            500,
            headers={"content-length": str(3 * 1024 * 1024)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

        assert str(2 * 1024 * 1024) in exc_info.value.message
        assert "SEEDREAM_RESPONSE_BODY_LIMIT" in exc_info.value.message


async def test_error_body_small_json_parse_runs_outside_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """低于解析上限的错误体 JSON 在事件循环线程内联解析，不付线程往返开销。"""
    spy = CpuOffloadSpy(json.loads)
    monkeypatch.setattr(json, "loads", spy)
    config = SeedreamConfig(api_key="k", max_retries=3)
    body = json.dumps({"message": "bad prompt"}).encode()

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, content=body, headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="请求参数错误"):
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

    spy.assert_ran_outside_cpu_pool()


async def test_error_body_json_parse_at_limit_runs_in_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恰达解析上限的错误体 JSON（上限与卸载阈值同值）经专用线程池解析。

    错误路径仅对不超过 _ERROR_JSON_PARSE_LIMIT 的体做 dict 解析，
    与卸载阈值的交叠仅此边界一点。
    越界即降级 message，截断由 test_non_json_error_body_message_truncated 锁定。
    """
    spy = CpuOffloadSpy(json.loads)
    monkeypatch.setattr(json, "loads", spy)
    config = SeedreamConfig(api_key="k", max_retries=3)
    envelope = json.dumps({"message": ""}).encode()
    body = json.dumps({"message": "x" * (_ERROR_JSON_PARSE_LIMIT - len(envelope))}).encode()
    assert len(body) == _ERROR_JSON_PARSE_LIMIT

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, content=body, headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="请求参数错误"):
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

    spy.assert_ran_in_cpu_pool()


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
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

        message = exc_info.value.message
        assert len(message) < 10 * 1024
        degraded = exc_info.value.response_data["message"]
        assert "截断，原文 2097152 字节" in degraded
        assert len(degraded) < 70 * 1024


async def test_response_body_limit_explicit_config_overrides_derivation(no_sleep: None) -> None:
    """显式 response_body_limit 直接生效，优先于按请求形态投影。"""
    config = SeedreamConfig(api_key="k", max_retries=3, response_body_limit=8192)

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
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

        # 16KB 超过显式上限 8192 但低于缺省 url 投影 4MB，命中说明显式配置生效。
        assert str(8192) in exc_info.value.message


async def test_response_body_limit_derived_from_family_pixels(no_sleep: None) -> None:
    """未配置 response_body_limit 时 b64_json 按单图最坏展开加每图信封余量与总信封余量投影生效。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    limit = _b64_single_image_limit(config)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": str(limit + 1)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api(
                "text_to_image",
                {"prompt": "p", "response_format": "b64_json"},
                per_request_image_count=1,
            )

        assert str(limit) in exc_info.value.message


async def test_b64_json_body_over_auto_save_max_file_size_accepted(no_sleep: None) -> None:
    """auto_save_max_file_size 取 1MiB 时约 2MiB 的 b64_json 成功体照常接受。

    读体上限以模型家族像素为单图基准，保存上限只约束落盘。
    两者耦合时该体被以「响应体过大」拒绝，已计费响应随之销毁。
    """
    config = SeedreamConfig(api_key="k", max_retries=3, auto_save_max_file_size=1024 * 1024)
    b64_chars = 2 * 1024 * 1024
    payload = json.dumps({"data": [{"b64_json": "A" * b64_chars}], "usage": {}}).encode()

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)
        result = await client._call_api(
            "text_to_image",
            {"prompt": "p", "response_format": "b64_json"},
            per_request_image_count=1,
        )

    assert result["success"] is True
    assert len(result["data"][0]["b64_json"]) == b64_chars


async def test_response_body_limit_b64_scales_with_request_image_count(no_sleep: None) -> None:
    """b64_json 投影按装配产图数放大：产图数 2 时为两图最坏展开加每图信封余量与总信封余量。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    limit = config.effective_response_body_limit(response_format="b64_json", max_image_count=2)
    assert limit > _b64_single_image_limit(config)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": str(limit + 1)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api(
                "sequential_generation",
                {
                    "prompt": "p",
                    "response_format": "b64_json",
                    "sequential_image_generation_options": {"max_images": 2},
                },
                per_request_image_count=2,
            )

        assert str(limit) in exc_info.value.message


async def test_response_body_limit_url_format_uses_structural_cap(no_sleep: None) -> None:
    """未配置时 url 格式取结构性固定上限，与产图数无关。"""
    config = SeedreamConfig(api_key="k", max_retries=3)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": str(5 * 1024 * 1024)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        # 5MB 超过 url 结构性上限 4MB 但低于 b64 投影（满额 15 图约 375MB），
        # 命中说明 url 格式未走 b64 投影。
        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api(
                "sequential_generation",
                {
                    "prompt": "p",
                    "response_format": "url",
                    "sequential_image_generation_options": {"max_images": 15},
                },
                per_request_image_count=15,
            )

        assert str(4 * 1024 * 1024) in exc_info.value.message


async def test_response_body_limit_layer_default_uses_official_server_default(
    no_sleep: None,
) -> None:
    """拆分缺省档（官方服务端默认 30 图）按装配产图数放大读体上限投影。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    limit = config.effective_response_body_limit(
        response_format="b64_json", max_image_count=DEFAULT_LAYER_DECOMPOSITION_MAX_IMAGES
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-length": str(limit + 1)},
            content=b"{}",
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api(
                "image_to_image",
                {"prompt": "p", "response_format": "b64_json", "layer_decomposition": True},
                per_request_image_count=DEFAULT_LAYER_DECOMPOSITION_MAX_IMAGES,
            )

        assert str(limit) in exc_info.value.message


async def test_stream_event_at_exact_base64_bound_not_truncated(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """解码后恰为单图最坏字节数且 n mod 3 = 1 的边界事件不被截断。

    单事件阈值为单图 base64 精确长度 4⌈n/3⌉ 加信封余量，最坏尺寸的合法图片事件
    连同 SSE 框架须整体落入阈值内。
    """
    n = 100_000
    assert n % 3 == 1
    _patch_single_image_bytes(monkeypatch, n)
    b64_len = 4 * ((n + 2) // 3)  # n 字节负载的 base64 精确长度
    event_bytes = (
        b'data: {"type":"image_generation.partial_succeeded","b64_json":"'
        + b"A" * b64_len
        + b'"}\n\n'
        + b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n'
    )

    config = SeedreamConfig(api_key="k", max_retries=3, stream_buffer_max_size=1024)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=event_bytes,
            headers={"content-type": "text/event-stream"},
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        result = await client._call_api(
            "text_to_image", {"prompt": "p", "stream": True}, per_request_image_count=1
        )

    # 无截断时计数 0 原样透传，results 侧按值归一渲染提示
    assert result["truncated_events"] == 0
    assert len(result["data"]) == 1
    assert len(result["data"][0]["b64_json"]) == b64_len


async def test_stream_truncated_events_passed_through_in_payload(
    monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """SSE 事件因超限被丢弃时，truncated_events 计数透传进 api result payload。

    单个未完成事件超过阈值即被丢弃并计数。单图基准替换为 1024 字节后阈值约 5.4KB，
    首个分块送出 6KB 无分隔符事件超过阈值触发截断，随后的 completed 事件正常解析。
    """
    _patch_single_image_bytes(monkeypatch, 1024)
    config = SeedreamConfig(api_key="k", max_retries=3, stream_buffer_max_size=1024)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream() -> AsyncIterator[bytes]:
            # 不完整事件：无空行分隔，超过阈值后被丢弃并计数
            yield b"data: " + b"A" * 6000
            yield b'data: {"type":"image_generation.completed","usage":{}}\n\n'

        return httpx.Response(200, content=_stream(), headers={"content-type": "text/event-stream"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        result = await client._call_api(
            "text_to_image", {"prompt": "p", "stream": True}, per_request_image_count=1
        )

    assert result["truncated_events"] == 1
    assert isinstance(result["truncated_events"], int)
    # 事件被丢弃使结果不完整，status 须标记 partial
    assert result["status"] == "partial"


async def test_stream_json_over_limit_error_not_wrapped_as_parse_failure(no_sleep: None) -> None:
    """流式 JSON 响应超限时「响应体过大」原文上抛，不被 JSON 解析失败包装。"""
    config = SeedreamConfig(api_key="k", max_retries=3)
    limit = _b64_single_image_limit(config)
    chunk = 64 * 1024
    chunk_count = limit // chunk + 2

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream() -> AsyncIterator[bytes]:
            # 64KiB 分块送出略超 b64 单图投影的体量，确保跨过上限。
            for _ in range(chunk_count):
                yield b"x" * chunk

        return httpx.Response(200, content=_stream(), headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应体过大") as exc_info:
            await client._call_api(
                "text_to_image",
                {"prompt": "p", "stream": True, "response_format": "b64_json"},
                per_request_image_count=1,
            )

        assert "JSON 解析失败" not in exc_info.value.message
        assert "已读取" in exc_info.value.message
        assert exc_info.value.status_code is None


def _sse_event(payload: dict[str, Any]) -> bytes:
    """把事件对象编码为单条 SSE 事件段。"""
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


def _progressive_frame_handler(
    retained_events: list[bytes], attempts: list[int]
) -> Callable[[httpx.Request], httpx.Response]:
    """构造先发 3 个各 1.5MiB 渐进帧、再发 retained_events 与 completed、[DONE] 的 SSE 处理函数。

    每次请求向 attempts 追加一项，供调用方断言尝试次数。
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts.append(1)

        async def _stream() -> AsyncIterator[bytes]:
            for _ in range(3):
                yield _partial_image_event(3 * 512 * 1024)
            for event in retained_events:
                yield event
            yield _sse_event(
                {"type": "image_generation.completed", "usage": {"generated_images": 15}}
            )
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=_stream(), headers={"content-type": "text/event-stream"})

    return _handler


async def test_stream_url_progressive_frames_exempt_from_body_limit(no_sleep: None) -> None:
    """stream=True 落 url 格式时渐进帧不计入读体上限，15 图组图流完整返回且只尝试一次。

    url 投影为 4MB 结构性上限，3 个各 1.5MiB 的渐进帧按线路字节计即越限，
    以无状态码错误丢掉全部已计费图片且不重试。
    """
    config = SeedreamConfig(api_key="k", max_retries=3, model_id="doubao-seedream-5.0")
    attempts: list[int] = []
    urls = [f"http://x/{index}.png" for index in range(15)]
    handler = _progressive_frame_handler(
        [_sse_event({"type": "image_generation.partial_succeeded", "url": url}) for url in urls],
        attempts,
    )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, handler)
        result = await client._call_api(
            "sequential_generation",
            {
                "prompt": "p",
                "stream": True,
                "sequential_image_generation_options": {"max_images": 15},
            },
            per_request_image_count=15,
        )

    assert len(attempts) == 1
    assert result["success"] is True
    assert result["status"] == "completed"
    assert [item["url"] for item in result["data"]] == urls
    assert result["truncated_events"] == 0


async def test_stream_b64_json_progressive_frames_exempt_from_body_limit(no_sleep: None) -> None:
    """stream=True 且 b64_json 时渐进帧同样不计入读体上限，15 张 b64 图全部返回。

    显式 2MB 上限下 3 个各 1.5MiB 的渐进帧按线路字节计即越限，
    保留事件合计约 1MB 低于上限。
    """
    config = SeedreamConfig(api_key="k", max_retries=3, response_body_limit=2 * 1024 * 1024)
    attempts: list[int] = []
    b64_chars = 64 * 1024
    handler = _progressive_frame_handler(
        [_sse_event({"type": "image_generation.partial_succeeded", "b64_json": "A" * b64_chars})]
        * 15,
        attempts,
    )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, handler)
        result = await client._call_api(
            "sequential_generation",
            {
                "prompt": "p",
                "stream": True,
                "response_format": "b64_json",
                "sequential_image_generation_options": {"max_images": 15},
            },
            per_request_image_count=15,
        )

    assert len(attempts) == 1
    assert result["success"] is True
    assert result["status"] == "completed"
    assert [len(item["b64_json"]) for item in result["data"]] == [b64_chars] * 15
    assert result["truncated_events"] == 0


async def test_success_body_large_json_parses_from_bytearray(
    no_sleep: None,
) -> None:
    """大体积成功 JSON 体经 bytearray 单份缓冲读取并正常解析。

    读体不再有 chunks 列表与 join 合并，大响应体无双重驻留与合并阻塞。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)
    # 9MB 成功 JSON 体，低于 b64 单图投影（默认 5.5 家族单图派生约 25MB）
    payload = b'{"x":"' + b"a" * (9 * 1024 * 1024) + b'"}'

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)
        result = await client._call_api(
            "text_to_image",
            {"prompt": "p", "response_format": "b64_json"},
            per_request_image_count=1,
        )

    assert result["success"] is True


async def test_success_json_data_items_over_cap_rejected(no_sleep: None) -> None:
    """成功 JSON 体的 data 条目数超过按总量限额推导的上限时拒绝该响应。

    大量小条目在字节未超限时仍可放大解析产物内存，与 SSE 条目上限经
    items_limit_for 同口径推导；显式上限 137216 派生 306，307 项即超上限。无状态码
    即不可重试，避免对非幂等生成 API 重复请求。
    """
    config = SeedreamConfig(api_key="k", max_retries=3, response_body_limit=1024 * 134)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx.Response(
            200,
            content=json.dumps({"data": [0] * 307}).encode(),
            headers={"content-type": "application/json"},
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="响应条目数过多") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

        assert exc_info.value.status_code is None
        assert attempts == 1


async def test_success_json_data_items_at_cap_accepted(no_sleep: None) -> None:
    """data 条目数恰好等于推导上限时正常解析，边界不误伤。"""
    config = SeedreamConfig(api_key="k", max_retries=3, response_body_limit=1024 * 134)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=json.dumps({"data": [0] * 306}).encode(),
            headers={"content-type": "application/json"},
        )

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)
        result = await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

    assert result["success"] is True
    assert len(result["data"]) == 306


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
            await client._call_api(
                "text_to_image", {"prompt": "p", "stream": True}, per_request_image_count=1
            )

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

        result = await client._call_api(
            "text_to_image", {"prompt": "p", "stream": True}, per_request_image_count=1
        )

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
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

    assert attempts == config.max_retries + 1


async def test_json_final_chunk_salvaged_at_deadline_completes_without_retry() -> None:
    """末块在截止瞬间入队且结束标记因取消丢失：完整 JSON 体正常解析，不误判超时触发重试。"""
    loop = asyncio.get_running_loop()
    gate = loop.create_future()
    body = json.dumps({"data": [{"url": "http://x/1.png"}]}).encode()

    async def _single_chunk() -> AsyncIterator[bytes]:
        await gate
        yield body

    def _deliver() -> None:
        gate.set_result(None)

    # 交付回调先于截止 timer 排队：末块入队与到点取消同批落地，结束标记停泊在
    # 队列写入处随生产者取消丢弃，复现审查实证的误判超时重试场景。
    loop.call_soon(_deliver)
    response = httpx.Response(
        200, content=_single_chunk(), headers={"content-type": "application/json"}
    )

    config = SeedreamConfig(api_key="k")
    async with SeedreamClient(config) as client:
        result = await client._parse_json_success_response(
            response, time.monotonic() - 1.0, 4 * 1024 * 1024
        )

    assert result["success"] is True
    assert result["data"][0]["url"] == "http://x/1.png"


async def test_read_body_ready_stream_aborts_after_deadline_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """持续就绪的读体流等块从不挂起：周期性墙钟复查越过截止后中止读取。"""
    read_chunks = 0

    def _monotonic() -> float:
        # 前 40 块时钟在预算内，此后越界；复查在第二个边界（第 64 块）命中。
        return 0.0 if read_chunks < 40 else 100.0

    monkeypatch.setattr(time, "monotonic", _monotonic)

    async def _ready_forever() -> AsyncIterator[bytes]:
        nonlocal read_chunks
        while True:
            read_chunks += 1
            yield b"x" * 16

    response = httpx.Response(200, content=_ready_forever())
    # 上限取小：复查缺失时该流会被字节上限以「响应体过大」拦截，测试快速失败。
    config = SeedreamConfig(api_key="k")
    async with SeedreamClient(config) as client:
        with pytest.raises(asyncio.TimeoutError, match="总时长预算"):
            await client._read_response_body_capped(response, max_bytes=64 * 1024, deadline=50.0)

    assert read_chunks < 100


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
            await client._call_api(
                "text_to_image", {"prompt": "p", "stream": True}, per_request_image_count=1
            )

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
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

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
        result = await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

    assert result["success"] is True


# 前四形态若回退成裸 int() 宽松解析，显式上限 64KiB 下预检值 1048576 会误报超限。
# 超长数字串两种口径一致跳过，此处锁定归 None 不抛 ValueError。
_LENIENT_INT_DECLARED_FORMS = [
    pytest.param(" 1048576", id="leading-space"),
    pytest.param("1048576 ", id="trailing-space"),
    pytest.param("+1048576", id="plus-sign"),
    pytest.param("1_048576", id="underscore"),
    pytest.param("9" * 5000, id="overlong-digit-run"),
]


@pytest.mark.parametrize("declared", _LENIENT_INT_DECLARED_FORMS)
async def test_lenient_int_content_length_forms_skip_precheck(
    declared: str, no_sleep: None
) -> None:
    """畸形 Content-Length 声明按未申报跳过预检，流式累计兜底读取，不误报超限。"""
    config = SeedreamConfig(api_key="k", max_retries=3, response_body_limit=64 * 1024)
    body = json.dumps({"data": [], "usage": {}, "status": "completed"}).encode()

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, headers={"content-length": declared}, content=body)

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)
        result = await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

    assert result["success"] is True


async def test_error_body_read_timeout_reduced_with_status_and_retry_after() -> None:
    """错误状态码下读体超时归约为携带状态码与 Retry-After 的 API 错误。

    经 MockTransport 取得真实流式 429 响应后以已过期的 deadline 直调读体路径，
    首块延迟送达使等待挂起，等块超时路径命中截止判定。
    """
    config = SeedreamConfig(api_key="k", max_retries=0)

    def _handler(request: httpx.Request) -> httpx.Response:
        del request

        async def _stream() -> AsyncIterator[bytes]:
            await _delay_outside_patched_sleep(0.05)
            yield b"x" * 32

        return httpx.Response(429, headers={"retry-after": "3"}, content=_stream())

    async with SeedreamClient(config) as client:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as raw:
            request = raw.build_request("POST", "https://example.com")
            response = await raw.send(request, stream=True)
            try:
                with pytest.raises(
                    SeedreamAPIError, match="读取错误响应体超时（状态码 429）"
                ) as exc_info:
                    await client._raise_for_response_status(
                        response, byte_limit=4 * 1024 * 1024, deadline=time.monotonic() - 1.0
                    )
            finally:
                await response.aclose()

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 3.0


async def test_error_body_read_transport_error_keeps_4xx_status_no_retry(
    no_sleep: None,
) -> None:
    """错误体读取遇非超时传输错误时按状态码归约，4xx 不并入网络错误重试。

    生成 API 非幂等，确定 4xx 后按网络错误重试会重复发送请求并丢失状态码语义。
    """
    config = SeedreamConfig(api_key="k", max_retries=3)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1

        async def _stream() -> AsyncIterator[bytes]:
            yield b"partial error body"
            raise httpx.ReadError("peer reset")

        return httpx.Response(400, content=_stream())

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="读取错误响应体失败（状态码 400）") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

        assert exc_info.value.status_code == 400
        assert attempts == 1


async def test_error_body_read_transport_error_keeps_5xx_retry_semantics(
    no_sleep: None,
) -> None:
    """错误体读取的传输错误保留 5xx 状态码，仍按可重试语义走完重试。"""
    config = SeedreamConfig(api_key="k", max_retries=2)
    attempts = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1

        async def _stream() -> AsyncIterator[bytes]:
            yield b"partial error body"
            raise httpx.ReadError("peer reset")

        return httpx.Response(500, content=_stream())

    async with SeedreamClient(config) as client:
        await _install_mock_transport(client, _handler)

        with pytest.raises(SeedreamAPIError, match="读取错误响应体失败（状态码 500）") as exc_info:
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

        assert exc_info.value.status_code == 500
        assert attempts == config.max_retries + 1


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
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

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
            await client._call_api("text_to_image", {"prompt": "p"}, per_request_image_count=1)

        assert exc_info.value.status_code == 400
        # 4xx 非可重试：仅尝试一次
        assert attempts == 1
