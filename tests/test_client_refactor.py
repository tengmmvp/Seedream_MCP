"""SeedreamClient 重构守护：请求组装、预处理并发与各模型能力差异。"""

from __future__ import annotations

import base64
import asyncio
import json
from collections import UserString, deque
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, overload

import httpx
import pytest
from PIL import Image

from seedream_mcp._client_http import (
    _first_error_detail,
    _has_valid_image_items,
    _outcome_error_note,
)
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import (
    SeedreamAPIError,
    SeedreamConfigError,
    SeedreamMCPError,
    SeedreamValidationError,
    resolve_error_profile,
    response_reports_failure,
)
from seedream_mcp.utils.core.executors import (
    CPU_OFFLOAD_SIZE_THRESHOLD,
    CpuOffloadPoolClosedError,
    shutdown_cpu_offload_executor,
)
from seedream_mcp.utils.images import image_validation as image_validation_module
from seedream_mcp.utils.io.io_stream import escalate_partial_status, item_has_image_payload

from _client_fakes import _install_mock_transport
from _cpu_offload_spy import CpuOffloadSpy, saturate_cpu_offload_pool
from _log_fakes import RecordingLogger


def _capture_call_api(captured: dict[str, Any]) -> Callable[..., Awaitable[dict[str, Any]]]:
    """构造捕获请求数据并返回成功空载荷的 _call_api 替身。"""

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    return fake_call_api


async def test_validate_common_generation_params_synthesizes_defaults() -> None:
    """缺省合成走 resolve_effective_generation_defaults 单源：图层拆分缺省 size
    为 auto、水印缺省取配置默认，与工具上下文构建共享同一规则。"""
    config = SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro", default_watermark=True)
    client = SeedreamClient(config)

    layered = await client._validate_common_generation_params(
        prompt=None,
        optimize_prompt_options=None,
        size=None,
        watermark=None,
        response_format="url",
        output_format=None,
        stream=False,
        tools=None,
        layer_decomposition=True,
    )

    assert layered.size == "auto"
    assert layered.watermark is True

    plain = await client._validate_common_generation_params(
        prompt="p",
        optimize_prompt_options=None,
        size=None,
        watermark=None,
        response_format="url",
        output_format=None,
        stream=False,
        tools=None,
    )

    assert plain.size == config.default_size
    assert plain.watermark is config.default_watermark


def test_build_common_request_assembles_shared_params() -> None:
    """_build_common_request 组装四方法共享参数；None 字段省略，extra 并入。"""
    config = SeedreamConfig(api_key="k", model_id="doubao-seedream-5-0-260128")
    client = SeedreamClient(config)
    request = client._build_common_request(
        prompt="p",
        size="2K",
        watermark=False,
        response_format="url",
        output_format="png",
        stream=True,
        tools=[{"type": "web_search"}],
        validated_opts={"mode": "standard"},
    )
    assert request["model"] == "doubao-seedream-5-0-260128"
    assert request["prompt"] == "p"
    assert request["size"] == "2K"
    assert request["watermark"] is False
    assert request["response_format"] == "url"
    assert request["output_format"] == "png"
    assert request["stream"] is True
    assert request["tools"] == [{"type": "web_search"}]
    assert request["optimize_prompt_options"] == {"mode": "standard"}


def test_build_common_request_merges_extra_and_skips_none() -> None:
    """extra 参数并入请求体，None 值字段省略对应键。"""
    config = SeedreamConfig(api_key="k")
    client = SeedreamClient(config)
    request = client._build_common_request(
        prompt="p",
        size="2K",
        watermark=False,
        response_format="url",
        output_format=None,
        stream=False,
        tools=None,
        validated_opts=None,
        extra={"image": "data"},
    )
    assert request["image"] == "data"
    assert "output_format" not in request
    assert "stream" not in request
    assert "tools" not in request
    assert "optimize_prompt_options" not in request


def _build_config() -> SeedreamConfig:
    return SeedreamConfig(api_key="test_key", max_retries=1)


@asynccontextmanager
async def _client_with_mock_transport(
    handler: Callable[[httpx.Request], Any],
) -> AsyncIterator[SeedreamClient]:
    """构建内部 httpx 客户端挂 MockTransport 的 SeedreamClient，退出时关闭连接。"""
    client = SeedreamClient(_build_config())
    await _install_mock_transport(client, handler)
    try:
        yield client
    finally:
        await client.close()


def test_build_common_request_omits_prompt_key_when_none() -> None:
    """图层拆分场景缺省提示词时请求体不含 prompt 键，由模型自动识别拆分意图。"""
    client = SeedreamClient(_build_config())
    data = client._build_common_request(
        prompt=None,
        size="auto",
        watermark=False,
        response_format="url",
        output_format=None,
        stream=False,
        tools=None,
        validated_opts=None,
    )

    assert "prompt" not in data
    assert data["model"] == client.config.model_id


async def test_text_to_image_log_does_not_include_prompt_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """info 日志只输出 prompt_meta 摘要，提示词明文不进入日志。"""
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint, request_data
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    prompt = "top secret prompt content"
    await client.text_to_image(prompt=prompt, size="2K")

    joined_logs = "\n".join(fake_logger.info_messages)
    assert "prompt_meta=" in joined_logs
    assert prompt not in joined_logs


async def test_generation_methods_synthesize_defaults_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直连调用未传 size/watermark 时按 config 默认值合成，消除签名与配置双源。

    硬性守护：watermark 未显式传入时必须保持 False；default_watermark 默认 False
    与官方默认 true 相悖，为项目有意决策。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1, default_size="4K")
    client = SeedreamClient(config)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(client, "_call_api", _capture_call_api(captured))

    # 设置 default_size="4K" 后不传 size 应得 4K，watermark 不传时恒为 False
    await client.text_to_image(prompt="test")
    assert captured["size"] == "4K"
    assert captured["watermark"] is False

    # 未改配置的默认实例回落 default_size=2K
    default_client = SeedreamClient(_build_config())
    default_captured: dict[str, Any] = {}
    monkeypatch.setattr(default_client, "_call_api", _capture_call_api(default_captured))
    await default_client.text_to_image(prompt="test")
    assert default_captured["size"] == "2K"
    assert default_captured["watermark"] is False


async def test_image_to_image_resolves_relative_path_from_images_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径参考图以图片目录为基准解析并编码为 data URI 发请求。"""
    workspace = tmp_path / "workspace"
    images_root = workspace / ".seedream" / "images"
    image_file = images_root / "ref.png"
    image_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=(255, 0, 0)).save(image_file)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    client = SeedreamClient(_build_config())
    captured_request: dict[str, Any] = {}
    monkeypatch.setattr(client, "_call_api", _capture_call_api(captured_request))

    await client.image_to_image(prompt="test", image="ref.png", size="2K")

    assert isinstance(captured_request["image"], str)
    assert captured_request["image"].startswith("data:image/png;base64,")


async def test_image_to_image_payload_carries_layer_and_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5.0 Pro 的图层拆分与透明背景进入 API 请求载荷，不止校验层放行。"""
    workspace = tmp_path / "workspace"
    images_root = workspace / ".seedream" / "images"
    image_file = images_root / "ref.png"
    image_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=(255, 0, 0)).save(image_file)
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    client = SeedreamClient(
        SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro", max_retries=1)
    )
    captured_request: dict[str, Any] = {}
    monkeypatch.setattr(client, "_call_api", _capture_call_api(captured_request))

    await client.image_to_image(
        prompt="test",
        image="ref.png",
        layer_decomposition=True,
        background="transparent",
    )

    assert captured_request["layer_decomposition"] is True
    assert captured_request["background"] == "transparent"


async def test_text_to_image_includes_seedream_50_output_format_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5.0 系列请求体携带 output_format 与 tools 参数。"""
    client = SeedreamClient(
        SeedreamConfig(
            api_key="test_key",
            model_id="doubao-seedream-5-0-260128",
            max_retries=1,
        )
    )
    captured_request: dict[str, Any] = {}

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.text_to_image(
        prompt="test",
        size="2K",
        output_format="png",
        tools=[{"type": "web_search"}],
    )

    assert captured_request["output_format"] == "png"
    assert captured_request["tools"] == [{"type": "web_search"}]


async def test_text_to_image_normalizes_seedream_50_alias_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """别名 model_id 在请求前归一化为具体版本号，请求体携带归一结果。"""
    client = SeedreamClient(
        SeedreamConfig(
            api_key="test_key",
            model_id="doubao-seedream-5.0",
            max_retries=1,
        )
    )
    captured_request: dict[str, Any] = {}

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.text_to_image(prompt="test", size="2K")

    assert client.config.model_id == "doubao-seedream-5-0-260128"
    assert captured_request["model"] == "doubao-seedream-5-0-260128"


async def test_call_api_parses_non_stream_response() -> None:
    """非流式 200 JSON 响应解析为成功结果结构与数据项。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "data": [{"url": "https://example.com/1.png"}],
                "usage": {"generated_images": 1},
                "status": "succeeded",
            },
        )

    async with _client_with_mock_transport(handler) as client:
        result = await client._call_api("text_to_image", {"prompt": "hello"})

    assert result["success"] is True
    assert result["status"] == "succeeded"
    assert result["data"][0]["url"] == "https://example.com/1.png"


async def test_call_api_success_body_parse_runs_in_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超阈值 200 响应体的 JSON 解析经专用线程池执行，不与默认池短任务同池排队。"""
    spy = CpuOffloadSpy(json.loads)

    monkeypatch.setattr(json, "loads", spy)

    oversized_url = "https://example.com/1.png?pad=" + "a" * CPU_OFFLOAD_SIZE_THRESHOLD

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "data": [{"url": oversized_url}],
                "usage": {"generated_images": 1},
                "status": "succeeded",
            },
        )

    async with _client_with_mock_transport(handler) as client:
        result = await client._call_api("text_to_image", {"prompt": "hello"})

    assert result["success"] is True
    spy.assert_ran_in_cpu_pool()


async def test_call_api_small_body_parse_runs_outside_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """低于下沉阈值的 200 响应体在事件循环线程同步解析，不付线程往返开销。"""
    spy = CpuOffloadSpy(json.loads)

    monkeypatch.setattr(json, "loads", spy)

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "data": [{"url": "https://example.com/1.png"}],
                "usage": {"generated_images": 1},
                "status": "succeeded",
            },
        )

    async with _client_with_mock_transport(handler) as client:
        result = await client._call_api("text_to_image", {"prompt": "hello"})

    assert result["success"] is True
    spy.assert_ran_outside_cpu_pool()


async def test_text_to_image_rejects_output_format_for_seedream_45_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4.5 模型传 output_format 在 API 调用前即被校验拒绝。"""
    client = SeedreamClient(
        SeedreamConfig(
            api_key="test_key",
            model_id="doubao-seedream-4-5-251128",
            max_retries=1,
        )
    )
    api_called = False

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        nonlocal api_called
        del endpoint, request_data
        api_called = True
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    with pytest.raises(SeedreamValidationError, match="模型支持 output_format"):
        await client.text_to_image(prompt="test", size="2K", output_format="png")

    assert api_called is False


async def test_call_api_parses_sse_response() -> None:
    """SSE 响应解析出 data 事件、completed 状态与 usage。"""
    sse_payload = (
        'data: {"type":"image_generation.partial_succeeded","url":"https://example.com/1.png"}\n\n'
        'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse_payload,
        )

    async with _client_with_mock_transport(handler) as client:
        result = await client._call_api("text_to_image", {"prompt": "hello", "stream": True})

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["usage"]["generated_images"] == 1
    assert result["data"][0]["url"] == "https://example.com/1.png"


async def test_call_api_parses_sse_partial_failed_event() -> None:
    """SSE partial_failed 事件进入 data 项，status 降级为 partial。"""
    sse_payload = (
        "data: "
        '{"type":"image_generation.partial_failed","image_index":2,'
        '"error":{"code":"OutputImageSensitiveContentDetected","message":"blocked"}}\n\n'
        'data: {"type":"image_generation.completed","usage":{"generated_images":0}}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse_payload,
        )

    async with _client_with_mock_transport(handler) as client:
        result = await client._call_api("text_to_image", {"prompt": "hello", "stream": True})

    assert result["success"] is True
    assert result["status"] == "partial"
    assert len(result["data"]) == 1
    assert result["data"][0]["type"] == "image_generation.partial_failed"
    assert result["data"][0]["image_index"] == 2
    assert result["data"][0]["error"]["code"] == "OutputImageSensitiveContentDetected"
    assert result["data"][0]["error"]["message"] == "blocked"


async def _drive_reference_prepare_with_limited_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    invoke: Callable[[SeedreamClient], Awaitable[None]],
) -> tuple[int, dict[str, Any], int]:
    """以受限并发的替身驱动参考图预处理，返回峰值并发、捕获请求与并发上限。

    对实现体打桩而非替换公共 prepare_image_input 方法：并发信号量位于公共入口内部，
    替换方法会使批量路径绕过信号量，断言的上限不再是真实约束；经实现体打桩信号量
    守卫仍在路径内，真实入口的 to_thread 签名跳转也不进入计时路径。
    """
    client = SeedreamClient(_build_config())
    client._image_preparer._prepare_concurrency = 2

    active_count = 0
    max_active_count = 0
    arrival_count = 0
    release = asyncio.Event()
    captured_request: dict[str, Any] = {}

    async def fake_prepare_image_input(
        image: str, _roots_key: Any = None, _slot: Any = None
    ) -> str:
        nonlocal active_count, max_active_count, arrival_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        # 会合式放行：在途任务数到达并发上限即放行，不依赖 sleep 时序。
        arrival_count += 1
        if arrival_count == client._image_preparer._prepare_concurrency:
            release.set()
        try:
            await asyncio.wait_for(release.wait(), timeout=5)
        finally:
            active_count -= 1
        return f"prepared:{image}"

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(
        client._image_preparer, "_prepare_image_input_locked", fake_prepare_image_input
    )
    monkeypatch.setattr(client, "_call_api", fake_call_api)
    await invoke(client)
    return max_active_count, captured_request, client._image_preparer._prepare_concurrency


async def test_multi_image_fusion_prepares_images_with_limited_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多图融合批量预处理并发不超过配置上限，且实际形成并发。"""

    async def invoke(client: SeedreamClient) -> None:
        await client.multi_image_fusion(
            prompt="test",
            image=["image-1", "image-2", "image-3"],
            size="2K",
        )

    max_active_count, captured_request, concurrency = (
        await _drive_reference_prepare_with_limited_concurrency(monkeypatch, invoke)
    )

    assert 1 < max_active_count <= concurrency
    assert captured_request["image"] == [
        "prepared:image-1",
        "prepared:image-2",
        "prepared:image-3",
    ]


async def test_multi_image_fusion_accepts_up_to_14_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认模型多图融合接受至多 14 张参考图。"""
    client = SeedreamClient(_build_config())
    input_images = [f"https://example.com/{idx}.png" for idx in range(14)]
    captured_request: dict[str, Any] = {}

    async def fake_prepare_images_in_parallel(images: list[str]) -> list[str]:
        return [f"prepared:{item}" for item in images]

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_images_in_parallel)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.multi_image_fusion(prompt="test", image=input_images, size="2K")

    assert len(captured_request["image"]) == 14
    assert captured_request["image"][0] == "prepared:https://example.com/0.png"
    assert captured_request["image"][-1] == "prepared:https://example.com/13.png"


async def test_multi_image_fusion_rejects_more_than_14_images() -> None:
    """默认模型超过 14 张参考图在请求前拒绝。"""
    client = SeedreamClient(_build_config())
    input_images = [f"https://example.com/{idx}.png" for idx in range(15)]

    with pytest.raises(SeedreamValidationError, match="image 数量不能超过 14"):
        await client.multi_image_fusion(prompt="test", image=input_images, size="2K")


async def test_sequential_generation_prepares_reference_images_with_limited_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组图参考图批量预处理并发同样受配置上限约束。"""

    async def invoke(client: SeedreamClient) -> None:
        await client.sequential_generation(
            prompt="test",
            max_images=3,
            image=["image-1", "image-2", "image-3"],
            size="2K",
        )

    max_active_count, captured_request, concurrency = (
        await _drive_reference_prepare_with_limited_concurrency(monkeypatch, invoke)
    )

    assert 1 < max_active_count <= concurrency
    assert captured_request["image"] == [
        "prepared:image-1",
        "prepared:image-2",
        "prepared:image-3",
    ]


async def test_sequential_generation_without_max_images_uses_reference_aware_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """携带参考图时缺省 max_images 取 14，与无参考图的默认值区分。"""
    client = SeedreamClient(_build_config())
    captured_request: dict[str, Any] = {}

    async def fake_prepare_image_input(image: str, _roots_key: Any = None) -> str:
        return f"prepared:{image}"

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_image_input", fake_prepare_image_input)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.sequential_generation(
        prompt="test",
        image="image-1",
        size="2K",
    )

    assert captured_request["image"] == "prepared:image-1"
    assert captured_request["sequential_image_generation_options"]["max_images"] == 14


def test_normalize_image_sequence_unwraps_str_input() -> None:
    """str 输入视作单元素列表，结果与显式单元素列表一致。"""
    normalized = SeedreamClient._normalize_image_sequence(
        images="http://example.com/a.png",
        min_count=1,
        max_count=2,
        field_name="image",
    )

    assert normalized == ["http://example.com/a.png"]


class _StrSequence(Sequence[str]):
    """委托内部列表承载元素的 Sequence 子类，代表 list/tuple 之外的序列形态。"""

    def __init__(self, items: Iterable[str]) -> None:
        self._items = list(items)

    @overload
    def __getitem__(self, index: int) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[str]: ...

    def __getitem__(self, index: int | slice) -> str | Sequence[str]:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)


def _deque_of(items: list[str]) -> Sequence[str]:
    return deque(items)


def _custom_of(items: list[str]) -> Sequence[str]:
    return _StrSequence(items)


@pytest.mark.parametrize(
    "sequence_factory",
    [_deque_of, _custom_of],
    ids=["deque", "custom-sequence"],
)
def test_normalize_image_sequence_rejects_non_list_sequence(
    sequence_factory: Callable[[list[str]], Sequence[str]],
) -> None:
    """deque 与自定义 Sequence 不属接受的容器形态，按容器级消息整体拒绝。"""
    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表") as excinfo:
        SeedreamClient._normalize_image_sequence(
            images=sequence_factory(["http://example.com/a.png", "http://example.com/b.png"]),
            min_count=1,
            max_count=2,
            field_name="image",
        )

    assert "image[" not in excinfo.value.message


@pytest.mark.parametrize(
    "invalid_input",
    [b"http://example.com/a.png", bytearray(b"http://example.com/a.png"), 123],
    ids=["bytes", "bytearray", "int"],
)
def test_normalize_image_sequence_rejects_bytes_like_and_non_sequence(
    invalid_input: Any,
) -> None:
    """bytes 形态与 int 等非列表形态仍拒绝，异常消息停在容器级不逐元素漂移。"""
    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表"):
        SeedreamClient._normalize_image_sequence(
            images=invalid_input,
            min_count=1,
            max_count=2,
            field_name="image",
        )


def test_normalize_image_sequence_rejects_user_string() -> None:
    """UserString 属 str-like Sequence，按容器级消息拒绝，不产生逐元素消息。"""
    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表") as excinfo:
        SeedreamClient._normalize_image_sequence(
            images=UserString("http://example.com/a.png"),  # type: ignore[arg-type]
            min_count=1,
            max_count=2,
            field_name="image",
        )

    assert "image[" not in excinfo.value.message


@pytest.mark.parametrize(
    "invalid_input",
    [range(3), memoryview(b"http://example.com/a.png")],
    ids=["range", "memoryview"],
)
def test_normalize_image_sequence_rejects_int_sequence_forms(invalid_input: Any) -> None:
    """range 与 memoryview 迭代成 int 序列，按容器级消息拒绝，不产生逐元素消息。"""
    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表") as excinfo:
        SeedreamClient._normalize_image_sequence(
            images=invalid_input,
            min_count=1,
            max_count=2,
            field_name="image",
        )

    assert "image[" not in excinfo.value.message


def test_normalize_image_sequence_rejects_non_str_element_in_sequence() -> None:
    """列表内混入非字符串元素时逐项定位拒绝，下标 1-based。"""
    with pytest.raises(SeedreamValidationError) as excinfo:
        SeedreamClient._normalize_image_sequence(
            images=["http://example.com/a.png", 123],  # type: ignore[list-item]
            min_count=1,
            max_count=2,
            field_name="image",
        )

    assert excinfo.value.message == "image[2] 参数必须是字符串"


def test_normalize_single_image_rejects_unencodable_surrogate() -> None:
    """含未配对代理字符的 image 输入在参数层拒绝，不推迟到请求序列化报编码错误。"""
    with pytest.raises(SeedreamValidationError, match="无法编码的字符"):
        SeedreamClient._normalize_single_image("http://example.com/\ud800.png")


def test_normalize_image_sequence_rejects_unencodable_surrogate() -> None:
    """列表形态逐项经同一编码预检，代理字符同样在参数层拒绝。"""
    with pytest.raises(SeedreamValidationError, match="image\\[2\\]"):
        SeedreamClient._normalize_image_sequence(
            images=["http://example.com/a.png", "http://example.com/\ud800.png"],
            min_count=1,
            max_count=2,
            field_name="image",
        )


def test_summarize_prompt_does_not_expose_prompt_plaintext() -> None:
    """prompt 摘要只含长度与哈希，明文不出现。"""
    prompt = "sensitive prompt"
    meta = SeedreamClient._summarize_prompt(prompt)

    assert "len=" in meta
    assert "sha256=" in meta
    assert prompt not in meta


def test_build_api_result_marks_partial_when_completed_data_has_error() -> None:
    """非 SSE JSON 路径：status=completed 但 data 含 error 项须降级为 partial。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {
            "status": "completed",
            "data": [
                {"url": "http://x/1.png"},
                {"error": {"code": "blocked", "message": "blocked"}},
            ],
            "usage": {"generated_images": 1},
        }
    )
    assert result["success"] is True
    assert result["status"] == "partial"


def test_build_api_result_marks_partial_when_status_missing_data_has_error() -> None:
    """status 缺省且 data 含 error 项同样须标记 partial。

    error 值非 None 即计失败条目。
    """
    client = SeedreamClient(_build_config())
    result = client._build_api_result({"data": [{"error": {"code": "E", "message": "boom"}}]})
    assert result["status"] == "partial"


def test_build_api_result_keeps_completed_when_no_error_in_data() -> None:
    """无 error 项时不误降级，status 保持 completed。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result({"status": "completed", "data": [{"url": "http://x/1.png"}]})
    assert result["status"] == "completed"


@pytest.mark.parametrize("status_value", [0, True, None])
def test_build_api_result_non_str_status_converged_to_none(status_value: Any) -> None:
    """上游 status 异形时收敛为 None，已成功的生成不因类型异形在结构化构造时翻错。

    GenerationStructuredOutput.status 声明 str|None，pydantic v2 拒绝 int/bool；
    未收敛时 200 响应携带 {"status": 0} 会使外层 except 把成功结果整体打翻为错误。
    """
    client = SeedreamClient(_build_config())
    result = client._build_api_result({"status": status_value, "data": [{"url": "http://x/1.png"}]})

    assert result["success"] is True
    assert result["status"] is None


def test_build_api_result_non_str_status_with_error_data_marks_partial() -> None:
    """异形 status 收敛为 None 后仍按缺省口径参与 partial 改写，与 SSE 路径一致。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {"status": 0, "data": [{"error": {"code": "blocked", "message": "blocked"}}]}
    )

    assert result["status"] == "partial"


def test_build_api_result_marks_partial_for_dict_data_with_error() -> None:
    """dict 形态 data 含 error 键时计为单条目升格 partial，与 list 形态同口径。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {"status": "completed", "data": {"error": {"code": "blocked", "message": "blocked"}}}
    )

    assert result["success"] is True
    assert result["status"] == "partial"


def test_build_api_result_keeps_completed_for_dict_data_without_error() -> None:
    """dict 形态 data 无 error 键时不误降级，status 保持 completed。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result({"status": "completed", "data": {"url": "http://x/1.png"}})

    assert result["status"] == "completed"


def test_build_api_result_keeps_completed_when_item_error_value_none() -> None:
    """条目 error:null 显式声明无错误，不升格 partial。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {"status": "completed", "data": [{"url": "http://x/1.png", "error": None}]}
    )

    assert result["status"] == "completed"


def test_build_api_result_marks_request_failure_when_items_carry_no_payload() -> None:
    """顶层 error 且条目无图像载荷时按请求级失败回显，成功形态不吞顶层错误。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {"error": {"code": "X", "message": "m"}, "data": [{"error": None}]}
    )

    assert result["success"] is False
    assert result["error"] == {"code": "X", "message": "m"}


@pytest.mark.parametrize("error_value", ["boom", 42])
def test_build_api_result_marks_partial_when_item_error_value_not_dict(
    error_value: Any,
) -> None:
    """条目 error 值非 dict 时同样升格 partial，契约外错误形态不静默放行。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {"status": "completed", "data": [{"url": "http://x/1.png", "error": error_value}]}
    )

    assert result["status"] == "partial"


def test_build_api_result_keeps_completed_for_dict_data_with_null_error() -> None:
    """dict 形态 data 的 error:null 不升格 partial，status 保持 completed。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {"status": "completed", "data": {"url": "http://x/1.png", "error": None}}
    )

    assert result["status"] == "completed"


async def test_image_to_image_invalid_data_uri_fails_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非法 base64 的 Data URI 在预处理阶段拒绝，不触达 API。"""
    client = SeedreamClient(_build_config())
    api_called = False

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        nonlocal api_called
        del endpoint, request_data
        api_called = True
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    with pytest.raises(SeedreamValidationError, match="Base64 解码失败|Data URI"):
        await client.image_to_image(
            prompt="test",
            image="data:image/png;base64,not_base64_payload",
            size="2K",
        )

    assert api_called is False


async def test_multi_image_fusion_oversized_data_uri_fails_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超限 Data URI 在预处理阶段拒绝，不触达 API。

    大小上限经 monkeypatch 缩到 KB 级触发同一条超限分支；上限读取的是
    image_validation 命名空间内的 MAX_IMAGE_FILE_SIZE，patch 目标据此确定。
    """
    client = SeedreamClient(_build_config())
    api_called = False

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        nonlocal api_called
        del endpoint, request_data
        api_called = True
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)
    monkeypatch.setattr(image_validation_module, "MAX_IMAGE_FILE_SIZE", 64 * 1024)

    oversized_b64 = base64.b64encode(b"a" * (96 * 1024)).decode("ascii")
    oversized_data_uri = f"data:image/png;base64,{oversized_b64}"

    with pytest.raises(SeedreamValidationError, match="数据过大"):
        await client.multi_image_fusion(
            prompt="test",
            image=[oversized_data_uri, oversized_data_uri],
            size="2K",
        )

    assert api_called is False


async def _invoke_multi_image_fusion(client: SeedreamClient, image: Sequence[str]) -> None:
    await client.multi_image_fusion(prompt="test", image=image, size="2K")


async def _invoke_sequential_generation(client: SeedreamClient, image: Sequence[str]) -> None:
    await client.sequential_generation(prompt="test", image=image, size="2K")


@pytest.mark.parametrize(
    "invoke",
    [_invoke_multi_image_fusion, _invoke_sequential_generation],
    ids=["multi-image-fusion", "sequential-generation"],
)
@pytest.mark.parametrize(
    "sequence_factory",
    [_deque_of, _custom_of],
    ids=["deque", "custom-sequence"],
)
async def test_generation_entries_reject_non_list_sequence(
    sequence_factory: Callable[[list[str]], Sequence[str]],
    invoke: Callable[[SeedreamClient, Sequence[str]], Awaitable[None]],
) -> None:
    """多图融合与组图入口对 list/tuple 之外的序列形态报同一容器级错误。"""
    client = SeedreamClient(_build_config())

    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表") as excinfo:
        await invoke(client, sequence_factory(["image-1", "image-2"]))

    assert "image[" not in excinfo.value.message


@pytest.mark.parametrize(
    "invoke",
    [_invoke_multi_image_fusion, _invoke_sequential_generation],
    ids=["multi-image-fusion", "sequential-generation"],
)
async def test_generation_entries_report_same_item_error_for_mixed_list(
    invoke: Callable[[SeedreamClient, Sequence[str]], Awaitable[None]],
) -> None:
    """image 列表混入非字符串元素时两入口报同一逐项定位消息，错误分类单源。"""
    client = SeedreamClient(_build_config())

    with pytest.raises(SeedreamValidationError) as excinfo:
        await invoke(client, ["a.png", 123])  # type: ignore[list-item]

    assert excinfo.value.message == "image[2] 参数必须是字符串"


@pytest.mark.parametrize(
    "invoke",
    [_invoke_multi_image_fusion, _invoke_sequential_generation],
    ids=["multi-image-fusion", "sequential-generation"],
)
async def test_generation_entries_report_same_container_error_for_non_container(
    invoke: Callable[[SeedreamClient, Sequence[str]], Awaitable[None]],
) -> None:
    """image 传入非容器形态时两入口报同一容器级消息。"""
    client = SeedreamClient(_build_config())

    with pytest.raises(SeedreamValidationError) as excinfo:
        await invoke(client, 123)  # type: ignore[arg-type]

    assert excinfo.value.message == "image 参数必须是字符串列表"


async def test_sequential_generation_invalid_image_type_raises_validation_error() -> None:
    """image 传入非字符串形态时抛出参数校验错误。"""
    client = SeedreamClient(_build_config())

    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表"):
        await client.sequential_generation(
            prompt="test",
            max_images=2,
            image=123,  # type: ignore[arg-type]
            size="2K",
        )


@pytest.mark.parametrize(
    "image_value",
    [b"http://example.com/a.png", bytearray(b"http://example.com/a.png")],
    ids=["bytes", "bytearray"],
)
async def test_sequential_generation_rejects_bytes_like_image(image_value: Any) -> None:
    """bytes 形态属 Sequence 但迭代成 int 序列，按容器级消息拒绝。"""
    client = SeedreamClient(_build_config())

    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表"):
        await client.sequential_generation(
            prompt="test",
            max_images=2,
            image=image_value,
            size="2K",
        )


async def test_sequential_generation_rejects_user_string_image() -> None:
    """UserString 形态的 image 与列表入口一致，按容器级消息拒绝，不逐字符拆分。"""
    client = SeedreamClient(_build_config())

    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表") as excinfo:
        await client.sequential_generation(
            prompt="test",
            max_images=2,
            image=UserString("http://example.com/a.png"),  # type: ignore[arg-type]
            size="2K",
        )

    assert "image[" not in excinfo.value.message


@pytest.mark.parametrize(
    "image_value",
    [range(3), memoryview(b"http://example.com/a.png")],
    ids=["range", "memoryview"],
)
async def test_sequential_generation_rejects_int_sequence_image(image_value: Any) -> None:
    """range 与 memoryview 迭代成 int 序列，与 bytes 形态同按容器级消息拒绝。"""
    client = SeedreamClient(_build_config())

    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表") as excinfo:
        await client.sequential_generation(
            prompt="test",
            max_images=2,
            image=image_value,
            size="2K",
        )

    assert "image[" not in excinfo.value.message


def _build_pro_config() -> SeedreamConfig:
    return SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5-0-pro-260628",
        max_retries=1,
    )


async def test_sequential_generation_rejects_seedream_50_pro() -> None:
    """5.0 Pro 不支持组图，调用即拒绝。"""
    client = SeedreamClient(_build_pro_config())

    with pytest.raises(SeedreamValidationError, match="5.0-pro 不支持组图"):
        await client.sequential_generation(prompt="test", max_images=3, size="2K")


async def test_text_to_image_rejects_stream_for_seedream_50_pro() -> None:
    """5.0 Pro 不支持流式输出，stream=True 即拒绝。"""
    client = SeedreamClient(_build_pro_config())

    with pytest.raises(SeedreamValidationError, match="5.0-pro 不支持流式输出"):
        await client.text_to_image(prompt="test", size="2K", stream=True)


@pytest.mark.parametrize(
    ("build_config", "label"),
    [(_build_pro_config, "pro"), (_build_config, "default")],
)
async def test_multi_image_fusion_omits_sequential_image_generation(
    monkeypatch: pytest.MonkeyPatch, build_config: Any, label: str
) -> None:
    """多图融合不传 sequential_image_generation，依赖服务端缺省 disabled。

    官方口径该参数仅部分模型支持，全模型恒传会向能力表外模型发参；Pro 与默认
    模型两条分支同守护。
    """
    del label
    client = SeedreamClient(build_config())
    captured_request: dict[str, Any] = {}

    async def fake_prepare_images_in_parallel(images: list[str]) -> list[str]:
        return [f"prepared:{item}" for item in images]

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_images_in_parallel)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.multi_image_fusion(prompt="test", image=["image-1", "image-2"], size="2K")

    assert "sequential_image_generation" not in captured_request
    assert captured_request["image"] == ["prepared:image-1", "prepared:image-2"]


async def test_multi_image_fusion_rejects_more_than_10_images_for_pro() -> None:
    """5.0 Pro 超过 10 张参考图在请求前拒绝。"""
    client = SeedreamClient(_build_pro_config())
    input_images = [f"https://example.com/{idx}.png" for idx in range(11)]

    with pytest.raises(SeedreamValidationError, match="image 数量不能超过 10"):
        await client.multi_image_fusion(prompt="test", image=input_images, size="2K")


async def test_multi_image_fusion_accepts_up_to_10_images_for_pro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5.0 Pro 多图融合接受至多 10 张参考图。"""
    client = SeedreamClient(_build_pro_config())
    input_images = [f"https://example.com/{idx}.png" for idx in range(10)]
    captured_request: dict[str, Any] = {}

    async def fake_prepare_images_in_parallel(images: list[str]) -> list[str]:
        return [f"prepared:{item}" for item in images]

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_images_in_parallel)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.multi_image_fusion(prompt="test", image=input_images, size="2K")

    assert len(captured_request["image"]) == 10


async def test_prepare_image_input_caches_result_and_evicts_lru(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_prepare_image_input 命中缓存不重复调用底层，LRU 淘汰最久未用而保留近期命中。"""
    from seedream_mcp.utils.images import image_prepare

    client = SeedreamClient(_build_config())
    client._image_preparer._prepare_cache_max = 3

    call_count = 0

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}"

    monkeypatch.setattr(image_prepare, "prepare_image_input", fake_prepare)

    # 同一输入第二次走缓存，底层 prepare_image_input 只调一次
    first = await client._image_preparer.prepare_image_input("img-1")
    second = await client._image_preparer.prepare_image_input("img-1")
    assert first == "prepared:img-1"
    assert second == "prepared:img-1"
    assert call_count == 1

    # 填满缓存，加入 img-1 / img-2 / img-3
    await client._image_preparer.prepare_image_input("img-2")
    await client._image_preparer.prepare_image_input("img-3")
    assert len(client._image_preparer._prepare_cache) == 3
    assert call_count == 3

    # 重新访问 img-1 使其成为近期使用，img-2 随即成为最久未用
    await client._image_preparer.prepare_image_input("img-1")
    assert call_count == 3

    # 加入 img-4 触发淘汰：LRU 淘汰最久未用的 img-2，保留近期命中的 img-1
    await client._image_preparer.prepare_image_input("img-4")
    assert len(client._image_preparer._prepare_cache) == 3
    assert call_count == 4

    # img-2 已被淘汰，重新请求会再次调用底层；img-1 仍在缓存不再调用
    await client._image_preparer.prepare_image_input("img-2")
    assert call_count == 5
    await client._image_preparer.prepare_image_input("img-1")
    assert call_count == 5


def test_serialize_request_outputs_utf8_without_ascii_escape() -> None:
    """_serialize_request 以 ensure_ascii=False 输出 UTF-8 bytes。

    中文以 UTF-8 字节序列原样出现而非 \\uXXXX 转义，提示词不被转义膨胀；静态
    方法可经类直接调用，无需实例化。
    """
    result = SeedreamClient._serialize_request({"prompt": "中文测试"})

    assert isinstance(result, bytes)
    assert "中文".encode("utf-8") in result
    # 字面反斜杠 u 4 e 2 d 的 ASCII 转义形式不得出现
    assert b"\\u4e2d" not in result


# ==================== 生成端点 URL 尾斜杠归一化 ====================


def test_build_generation_url_strips_trailing_slashes() -> None:
    """base_url 尾斜杠归一化：带尾斜杠不拼出双斜杠路径，无尾斜杠结果一致。"""
    trailing = SeedreamClient(
        SeedreamConfig(api_key="k", base_url="https://ark.example.com/api/v3/")
    )
    plain = SeedreamClient(SeedreamConfig(api_key="k", base_url="https://ark.example.com/api/v3"))

    assert trailing._build_generation_url() == "https://ark.example.com/api/v3/images/generations"
    assert plain._build_generation_url() == "https://ark.example.com/api/v3/images/generations"


# ==================== 200 响应顶层 error 守卫 ====================


def test_build_api_result_top_level_error_without_data_marks_request_failure() -> None:
    """200 顶层 error 为非空 dict 且无 data 时置 success=False 并透传 error。

    不再吞为成功零图。
    """
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {"error": {"code": "ContentTooLarge", "message": "生成内容超限"}, "usage": {}}
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"]["code"] == "ContentTooLarge"
    assert result["error"]["message"] == "生成内容超限"
    assert result["data"] == []
    assert result["usage"] == {}


def test_build_api_result_top_level_error_with_only_error_items_marks_failure() -> None:
    """data 全为 error 占位项时同样无有效图片，顶层 error 判定请求级失败。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {"error": {"code": "E", "message": "boom"}, "data": [{"error": {"code": "E"}}]}
    )

    assert result["success"] is False
    assert result["status"] == "failed"


def test_build_api_result_top_level_error_with_valid_images_keeps_success_and_error() -> None:
    """顶层 error 但 data 含有效图片：维持 success=True，同时附 error 键透传上游部分错误。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result(
        {
            "error": {"code": "PartialServiceDegraded", "message": "降级"},
            "data": [{"url": "https://example.com/1.png"}],
        }
    )

    assert result["success"] is True
    assert result["error"]["code"] == "PartialServiceDegraded"
    assert result["data"][0]["url"] == "https://example.com/1.png"


@pytest.mark.parametrize("error_value", [None, {}, "boom", 42])
def test_build_api_result_non_dict_top_level_error_keeps_success(error_value: Any) -> None:
    """非 dict 或空 dict 形态的顶层 error 不触发请求级失败，维持既有成功口径。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result({"error": error_value})

    assert result["success"] is True
    assert "error" not in result


async def test_stream_request_non_sse_json_error_body_marks_failure() -> None:
    """stream=true 时上游以 200 加非 SSE JSON 错误体响应：结果为失败并透传错误码。

    上游仅收到一次请求；错误经结果结构表达而非异常，调用方可取回真实错误码。
    """
    upstream_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(
            200,
            json={"error": {"code": "StreamRejected", "message": "流式请求被拒绝"}},
        )

    async with _client_with_mock_transport(handler) as client:
        result = await client._call_api("text_to_image", {"prompt": "p", "stream": True})

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"]["code"] == "StreamRejected"
    assert upstream_calls == 1


# ==================== 200 响应非 dict JSON 体守卫 ====================


@pytest.mark.parametrize(
    "raw_payload,expected_type",
    [
        ([1, 2], "list"),
        ("text", "str"),
        (None, "null"),
    ],
)
async def test_call_api_non_dict_json_payload_raises_format_error(
    raw_payload: Any, expected_type: str
) -> None:
    """标准路径 200 响应体为非 dict JSON 时抛出带类型标记的响应格式错误。

    错误消息携带实际 JSON 类型而非 AttributeError。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        # httpx 的 json=None 表示不写 JSON 体，null 场景显式发送字面量文本
        if raw_payload is None:
            return httpx.Response(200, content="null", headers={"content-type": "application/json"})
        return httpx.Response(200, json=raw_payload)

    async with _client_with_mock_transport(handler) as client:
        with pytest.raises(SeedreamAPIError) as excinfo:
            await client._call_api("text_to_image", {"prompt": "hello"})

    assert "响应格式错误" in excinfo.value.message
    assert expected_type in excinfo.value.message
    assert "AttributeError" not in excinfo.value.message


@pytest.mark.parametrize(
    "raw_payload,expected_type",
    [
        ([1, 2], "list"),
        ("text", "str"),
        (None, "null"),
    ],
)
async def test_stream_request_non_dict_json_payload_raises_format_error(
    raw_payload: Any, expected_type: str
) -> None:
    """流式路径 200 响应体为非 dict JSON 时同样抛出明确的响应格式错误。"""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        # httpx 的 json=None 表示不写 JSON 体，null 场景显式发送字面量文本
        if raw_payload is None:
            return httpx.Response(200, content="null", headers={"content-type": "application/json"})
        return httpx.Response(200, json=raw_payload)

    async with _client_with_mock_transport(handler) as client:
        with pytest.raises(SeedreamAPIError) as excinfo:
            await client._call_api("text_to_image", {"prompt": "hello", "stream": True})

    assert "响应格式错误" in excinfo.value.message
    assert expected_type in excinfo.value.message
    assert "AttributeError" not in excinfo.value.message


# ==================== 错误体解析输入界 ====================


async def test_error_data_from_body_oversized_body_degrades_to_message() -> None:
    """超过 _ERROR_JSON_PARSE_LIMIT 的错误体不做完整 dict 解析，降级为 message 形态。"""
    oversized = bytearray(
        json.dumps({"error": {"code": "E", "message": "x" * (70 * 1024)}}).encode("utf-8")
    )

    data = await SeedreamClient._error_data_from_body(oversized)

    assert set(data.keys()) == {"message"}
    assert data["message"].startswith('{"error"')


def test_normalize_api_error_passes_through_mcp_error_hierarchy() -> None:
    """SeedreamMCPError 体系内的异常原样返回，体系外异常包装为 SeedreamAPIError。"""
    client = SeedreamClient(_build_config())
    hierarchy_error = SeedreamMCPError("workspace root missing")
    outside_error = ValueError("boom")

    assert client._normalize_api_error(hierarchy_error) is hierarchy_error
    wrapped = client._normalize_api_error(outside_error)
    assert isinstance(wrapped, SeedreamAPIError)
    assert wrapped.__cause__ is outside_error


# ==================== 空 API Key 归约档 ====================


async def test_empty_api_key_maps_to_config_error_profile() -> None:
    """运行时空 API Key 经生成方法调用归约 config_error 档，不再包装为 api_error。

    SeedreamConfig 构造期已拒绝空密钥，此处经 object.__setattr__ 模拟配置在构造后
    被置空的运行时状态，锁定 _get_headers 抛出的异常类型与归约档。
    """
    config = _build_config()
    object.__setattr__(config, "api_key", "")
    client = SeedreamClient(config)

    with pytest.raises(SeedreamConfigError) as excinfo:
        await client.text_to_image(prompt="p", size="2K")

    assert resolve_error_profile(excinfo.value).error_code == "config_error"
    assert "API 密钥为空" in excinfo.value.message


# ==================== 批次级公共参数校验提升 ====================


def _install_validate_common_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """在 client 模块命名空间替换 validate_common_generation_params 为计数替身，返回记录调用次数的字典。"""
    import seedream_mcp.client as client_module
    from seedream_mcp.utils.core.validators import validate_common_generation_params

    calls = {"validate": 0}
    original = validate_common_generation_params

    def _spy(**kwargs: Any) -> Any:
        calls["validate"] += 1
        return original(**kwargs)

    monkeypatch.setattr(client_module, "validate_common_generation_params", _spy)
    return calls


async def test_parallel_batch_validates_common_params_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 请求批次公共参数全量校验只发生一次，批内各请求经共享计划命中缓存。"""
    from seedream_mcp.tools.core.schemas import TextToImageInput
    from seedream_mcp.tools.impl.text_to_image import handle_text_to_image

    calls = _install_validate_common_spy(monkeypatch)

    async def fake_send(
        self: Any,
        *,
        client: Any,
        url: str,
        request_body: bytes,
        request_timeout: Any,
    ) -> dict[str, Any]:
        del self, client, url, request_body, request_timeout
        return {"success": True, "data": [], "usage": {}, "status": "completed"}

    monkeypatch.setattr(SeedreamClient, "_send_standard_request", fake_send)

    result = await handle_text_to_image(
        TextToImageInput(prompt="parallel", request_count=4, parallelism=4),
        SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False),
    )

    assert result.is_error is False
    assert calls["validate"] == 1


async def test_direct_client_calls_validate_common_params_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未绑定共享计划的直连调用保持逐次校验，公共 API 行为不变。"""
    calls = _install_validate_common_spy(monkeypatch)
    client = SeedreamClient(_build_config())

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint, request_data
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.text_to_image(prompt="first")
    await client.text_to_image(prompt="second")

    assert calls["validate"] == 2


# ==================== 任务结局日志分级 ====================


async def test_text_to_image_logs_info_completion_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功结果落 info 级任务完成日志，错误与告警桶保持为空。"""
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint, request_data
        return {
            "success": True,
            "data": [{"url": "https://example.com/1.png"}],
            "usage": {},
            "status": "completed",
        }

    monkeypatch.setattr(client, "_call_api", fake_call_api)
    await client.text_to_image(prompt="p", size="2K")

    assert any("文生图任务完成" in message for message in fake_logger.info_messages)
    assert fake_logger.errors == []
    assert fake_logger.warnings == []


def test_outcome_log_reports_completion_for_dict_data_with_null_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dict 形态 data 的 error:null 不升格 partial，结局日志落任务完成。"""
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)
    result = client._build_api_result(
        {"status": "completed", "data": {"url": "http://x/1.png", "error": None}}
    )

    client._log_task_outcome("文生图", result)

    assert result["status"] == "completed"
    assert not any("部分完成" in message for message in fake_logger.warnings)
    assert any("文生图任务完成" in message for message in fake_logger.info_messages)


async def test_multi_image_fusion_logs_error_on_soft_failure_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 加顶层 error 的软失败经结果结构返回时落 error 级任务失败日志。

    success=False 不抛异常，完成日志曾与失败结果并存。
    """
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    async def fake_prepare_images_in_parallel(images: list[str]) -> list[str]:
        return [f"prepared:{item}" for item in images]

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint, request_data
        return {
            "success": False,
            "data": [],
            "usage": {},
            "status": "failed",
            "error": {"code": "E", "message": "boom"},
        }

    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_images_in_parallel)
    monkeypatch.setattr(client, "_call_api", fake_call_api)
    await client.multi_image_fusion(prompt="p", image=["i1", "i2"], size="2K")

    assert any("多图融合任务失败" in message for message in fake_logger.errors)
    # 失败结局日志携带顶层 error 的码与消息，仅凭日志可定位原因
    assert any("E boom" in message for message in fake_logger.errors)
    assert not any("多图融合任务完成" in message for message in fake_logger.info_messages)


async def test_sequential_generation_logs_warning_on_partial_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status=partial 的部分完成结果落 warning 级日志，不再谎报完成。"""
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint, request_data
        return {
            "success": True,
            "data": [{"error": {"code": "E", "message": "blocked"}}],
            "usage": {},
            "status": "partial",
        }

    monkeypatch.setattr(client, "_call_api", fake_call_api)
    await client.sequential_generation(prompt="p", max_images=2, size="2K")

    assert any("组图输出任务部分完成" in message for message in fake_logger.warnings)
    # 部分完成结局日志携带失败项计数与首条原因
    assert any("1 项失败: E blocked" in message for message in fake_logger.warnings)
    assert not any("组图输出任务完成" in message for message in fake_logger.info_messages)
    assert fake_logger.errors == []


async def test_text_to_image_logs_warning_when_success_carries_top_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """success=True 但携带顶层 error 键的结果降级 warning，不再落纯完成日志。"""
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint, request_data
        return {
            "success": True,
            "data": [{"url": "https://example.com/1.png"}],
            "usage": {},
            "status": "completed",
            "error": {"code": "PartialWarn", "message": "quota near limit"},
        }

    monkeypatch.setattr(client, "_call_api", fake_call_api)
    await client.text_to_image(prompt="p", size="2K")

    assert any("文生图任务完成但携带错误" in message for message in fake_logger.warnings)
    # 结局日志附顶层 error 的码与消息，仅凭日志可定位原因
    assert any("PartialWarn quota near limit" in message for message in fake_logger.warnings)
    assert not any("文生图任务完成" in message for message in fake_logger.info_messages)
    assert fake_logger.errors == []


async def test_text_to_image_logs_error_on_failed_status_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """success=True 但 status=failed 的软失败结果落 error 级任务失败日志。

    该形态被下游 response_reports_failure 判为失败，结局日志不得谎报任务完成。
    """
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint, request_data
        return {
            "success": True,
            "data": [{"error": {"code": "E", "message": "boom"}}],
            "usage": {},
            "status": "failed",
        }

    monkeypatch.setattr(client, "_call_api", fake_call_api)
    await client.text_to_image(prompt="p", size="2K")

    assert any("文生图任务失败" in message for message in fake_logger.errors)
    # 失败结局日志携带失败项计数与首条原因
    assert any("1 项失败: E boom" in message for message in fake_logger.errors)
    assert not any("文生图任务完成" in message for message in fake_logger.info_messages)


async def test_text_to_image_string_error_item_escalates_to_partial_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """条目 error 为契约外字符串形态时经真实读体路径升格 partial，结局日志落 warning。"""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "data": [{"url": "https://example.com/1.png", "error": "boom"}],
                "status": "completed",
            },
        )

    async with _client_with_mock_transport(handler) as client:
        fake_logger = RecordingLogger()
        monkeypatch.setattr(client, "logger", fake_logger)
        result = await client.text_to_image(prompt="p", size="2K")

    assert result["status"] == "partial"
    assert any("文生图任务部分完成" in message for message in fake_logger.warnings)
    # 非 dict 错误形态的详情提取不得抛错，计数与字符串详情照常进入日志。
    assert any("1 项失败: boom" in message for message in fake_logger.warnings)
    assert fake_logger.errors == []


@pytest.mark.parametrize(
    "response",
    [
        {"success": True, "status": "completed"},
        {"success": True, "status": "failed"},
        {"success": True, "status": "partial"},
        {"success": False, "status": "completed"},
        {"success": False},
        {"success": True},
        {"status": "failed"},
        {},
    ],
)
def test_outcome_log_failure_matches_shared_predicate(
    response: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """结局日志的失败分级与 response_reports_failure 单源判定一致。"""
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    client._log_task_outcome("文生图", response)

    expected_failed = response_reports_failure(response)
    assert bool(fake_logger.errors) is expected_failed
    if expected_failed:
        assert fake_logger.info_messages == []


def test_public_generation_methods_keep_prompt_first() -> None:
    """四个公开生成方法的 prompt 恒居首参，锁定外部位置调用方的参数含义。"""
    import inspect

    from seedream_mcp.client import SeedreamClient

    for method in (
        SeedreamClient.text_to_image,
        SeedreamClient.image_to_image,
        SeedreamClient.multi_image_fusion,
        SeedreamClient.sequential_generation,
    ):
        parameter_names = list(inspect.signature(method).parameters)
        assert parameter_names[1] == "prompt"


def test_generation_method_docstrings_follow_capability_table() -> None:
    """四个生成方法 docstring 的家族清单含能力表派生的全部支持家族展示名。"""
    from seedream_mcp.utils.model.model_capabilities import supported_family_display_names

    for method in (
        SeedreamClient.text_to_image,
        SeedreamClient.image_to_image,
        SeedreamClient.multi_image_fusion,
        SeedreamClient.sequential_generation,
    ):
        docstring = method.__doc__ or ""
        assert (
            supported_family_display_names("supports_output_format") in docstring
        ), method.__name__
        assert supported_family_display_names("supports_stream") in docstring, method.__name__
        assert supported_family_display_names("supports_tools") in docstring, method.__name__
    assert supported_family_display_names("supports_sequential_generation") in (
        SeedreamClient.sequential_generation.__doc__ or ""
    )


# ==================== 池关闭窗口的服务关闭错误呈现 ====================


async def _await_task_capturing(task: "asyncio.Task[dict[str, Any]]") -> BaseException | None:
    """等待任务完成并捕获异常，供关池窗口用例断言调用方实际收到的错误形态。"""
    try:
        await task
    except BaseException as exc:
        return exc
    return None


def _assert_shutdown_error_shape(caught: BaseException | None) -> None:
    """断言关池错误呈现服务关闭事实，不伪装成 JSON 解析失败或 API 错误。"""
    assert isinstance(caught, SeedreamMCPError)
    assert not isinstance(caught, SeedreamAPIError)
    assert "服务正在关闭" in caught.message
    assert "JSON 解析失败" not in caught.message
    assert "API 调用失败" not in caught.message
    assert "线程池" not in caught.message
    assert isinstance(caught.__cause__, CpuOffloadPoolClosedError)


async def test_call_api_pool_closed_during_serialization_reports_shutdown() -> None:
    """饱和池上排队的大载荷序列化被关池取消时，调用方收到服务关闭错误且不再重试。"""
    handler_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal handler_calls
        del request
        handler_calls += 1
        return httpx.Response(200, json={"data": [], "usage": {}, "status": "completed"})

    oversized_prompt = "p" * (CPU_OFFLOAD_SIZE_THRESHOLD + 1)

    async with _client_with_mock_transport(handler) as client:
        async with saturate_cpu_offload_pool() as saturated:
            task = asyncio.ensure_future(
                client._call_api("text_to_image", {"prompt": oversized_prompt})
            )
            await saturated.wait_queued()
            shutdown_cpu_offload_executor()

            caught = await _await_task_capturing(task)
        await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5)

    _assert_shutdown_error_shape(caught)
    # 序列化在发送前被取消，上游不收到任何请求。
    assert handler_calls == 0


async def test_call_api_pool_closed_during_json_parse_reports_shutdown() -> None:
    """大响应体的池内 JSON 解析在关池窗口被取消时，错误呈现服务正在关闭而非解析失败。"""
    handler_entered = asyncio.Event()
    response_gate = asyncio.Event()
    handler_calls = 0
    oversized_url = "https://example.com/1.png?pad=" + "a" * CPU_OFFLOAD_SIZE_THRESHOLD

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal handler_calls
        del request
        handler_calls += 1
        handler_entered.set()
        await response_gate.wait()
        return httpx.Response(
            200,
            json={"data": [{"url": oversized_url}], "status": "completed"},
        )

    async with _client_with_mock_transport(handler) as client:
        task = asyncio.ensure_future(client._call_api("text_to_image", {"prompt": "p"}))
        await asyncio.wait_for(handler_entered.wait(), timeout=5)
        async with saturate_cpu_offload_pool() as saturated:
            response_gate.set()
            await saturated.wait_queued()
            shutdown_cpu_offload_executor()

            caught = await _await_task_capturing(task)
        await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5)

    _assert_shutdown_error_shape(caught)
    # 服务关闭属退出窗口，失败请求不进入重试。
    assert handler_calls == 1


async def test_call_api_pool_closed_during_sse_parse_reports_shutdown() -> None:
    """SSE 大事件解析在关池窗口被取消时，错误同样呈现服务正在关闭而非 API 调用失败。"""
    big_event = json.dumps({"type": "image_generation.partial_succeeded", "b64_json": "A" * 70000})
    sse_payload = (
        "data: " + big_event + "\n\n"
    ).encode() + b'data: {"type":"image_generation.completed","usage":{"generated_images":1}}\n\n'
    handler_entered = asyncio.Event()
    response_gate = asyncio.Event()
    handler_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal handler_calls
        del request
        handler_calls += 1
        handler_entered.set()
        await response_gate.wait()
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse_payload
        )

    async with _client_with_mock_transport(handler) as client:
        task = asyncio.ensure_future(
            client._call_api("text_to_image", {"prompt": "p", "stream": True})
        )
        await asyncio.wait_for(handler_entered.wait(), timeout=5)
        async with saturate_cpu_offload_pool() as saturated:
            response_gate.set()
            await saturated.wait_queued()
            shutdown_cpu_offload_executor()

            caught = await _await_task_capturing(task)
        await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5)

    _assert_shutdown_error_shape(caught)
    assert handler_calls == 1


def test_outcome_error_note_extracts_dict_form_failure_detail() -> None:
    """dict 形态 data 的失败详情与 list 条目同口径提取。"""
    response = {
        "success": True,
        "status": "completed",
        "data": {"url": "https://example.com/a.png", "error": {"code": "X", "message": "m"}},
    }

    assert _outcome_error_note(response) == "1 项失败: X m"


@pytest.mark.parametrize(
    ("data", "expect_partial", "expect_note", "expect_valid"),
    [
        ([{"url": "u"}, {"error": {"code": "E", "message": "m"}}], True, "1 项失败: E m", True),
        ([{"error": {"code": "E", "message": "m"}}], True, "1 项失败: E m", False),
        ({"url": "u", "error": {"code": "E", "message": "m"}}, True, "1 项失败: E m", False),
        ([{"url": "u", "error": None}], False, "无错误详情", True),
        ([{"error": None}], False, "无错误详情", False),
        ([{"url": ""}], False, "无错误详情", False),
        ([{"url": "u", "error": "boom"}], True, "1 项失败: boom", False),
        ([{"url": "u", "error": {"code": "E", "message": " "}}], True, "1 项失败: E", False),
        ([{"url": "u", "error": 42}], True, "1 项失败: 42", False),
        ([{"url": "u", "error": 0}], True, "1 项失败: 0", False),
        ([{"url": "u", "error": ["boom", "x"]}], True, "1 项失败: ['boom', 'x']", False),
        ([{"url": "u", "error": ["p" * 300]}], True, "1 项失败: <truncated:list, 1 items>", False),
        (
            [{"url": "u", "error": {"code": "E", "message": 0}}],
            True,
            "1 项失败: E 0",
            False,
        ),
        (
            [{"url": "u", "error": {"code": "E", "message": ["m" * 300]}}],
            True,
            "1 项失败: E <truncated:list, 1 items>",
            False,
        ),
        ({"url": "u"}, False, "无错误详情", True),
        ("oops", False, "无错误详情", False),
        (None, False, "无错误详情", False),
    ],
    ids=[
        "list-mixed",
        "list-error-only",
        "dict-error",
        "list-null-error",
        "list-null-error-contentless",
        "list-empty-url",
        "list-string-error",
        "list-ws-message",
        "list-int-error",
        "list-zero-error",
        "list-small-container-error",
        "list-oversize-container-error",
        "list-zero-message-part",
        "list-oversize-message-part",
        "dict-plain",
        "scalar",
        "none",
    ],
)
def test_data_shape_consumers_share_single_source(
    data: Any, expect_partial: bool, expect_note: str, expect_valid: bool
) -> None:
    """升格判定、结局日志错误提取与有效图片判定共用 data_items 的形态归一。"""
    expected_status = "partial" if expect_partial else "completed"

    assert escalate_partial_status("completed", data) == expected_status
    assert _outcome_error_note({"data": data}) == expect_note
    assert _has_valid_image_items(data) is expect_valid


def test_first_error_detail_bounds_container_render_without_full_str() -> None:
    """超体量容器错误渲染为元素数摘要，完整 str/repr 物化不被触发，事件循环免 O(载荷) 拼接。"""
    materializations: list[str] = []

    class SentinelList(list[str]):
        def __str__(self) -> str:
            materializations.append("str")
            return super().__str__()

        def __repr__(self) -> str:
            materializations.append("repr")
            return super().__repr__()

    oversize = SentinelList(["p" * 10000])
    assert _first_error_detail(oversize) == "<truncated:list, 1 items>"

    oversize_part = SentinelList(["m" * 10000])
    detail = _first_error_detail({"code": "E", "message": oversize_part})

    assert detail == "E <truncated:list, 1 items>"
    assert materializations == []


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"url": "https://example.com/1.png"}, True),
        ({"b64_json": "aGVsbG8="}, True),
        ({"url": None, "b64_json": None}, False),
        ({"url": "", "b64_json": ""}, False),
        ({"error": {"code": "E"}}, False),
        ("oops", False),
        (None, False),
    ],
    ids=["url", "b64-json", "null-values", "empty-strings", "error-only", "scalar", "none"],
)
def test_item_has_image_payload_judges_non_empty_payload(item: Any, expected: bool) -> None:
    """载荷存在判定单源于 io_stream，url 或 b64_json 任一非空即计，空取值与非 dict 形态计无载荷。"""
    assert item_has_image_payload(item) is expected


def test_has_valid_image_items_consumes_payload_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有效图片判定的载荷判据消费 io_stream 单源谓词，不在 _client_http 内重造。"""
    import seedream_mcp._client_http as client_http_module

    monkeypatch.setattr(client_http_module, "item_has_image_payload", lambda item: False)

    assert client_http_module._has_valid_image_items([{"url": "u"}]) is False


@pytest.mark.parametrize(
    ("data", "expected_count"),
    [
        ([{"url": "u"}, {"url": "v"}], 2),
        ({"url": "u"}, 1),
        ("oops", 0),
        (None, 0),
    ],
    ids=["list", "dict", "scalar", "none"],
)
def test_build_api_result_data_count_follows_data_items(
    data: Any, expected_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """data_count 经 data_items 推导，标量形态条目数归 0 与谓词口径一致。"""
    client = SeedreamClient(_build_config())
    fake_logger = RecordingLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    client._build_api_result({"status": "completed", "data": data})

    assert any(f"data_count={expected_count}" in message for message in fake_logger.debug_messages)
