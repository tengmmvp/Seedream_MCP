"""SeedreamClient 重构守护：请求组装、参数顺序、预处理并发与各模型能力差异。"""

from __future__ import annotations

import base64
import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
import pytest
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import (
    SeedreamAPIError,
    SeedreamConfigError,
    SeedreamMCPError,
    SeedreamValidationError,
    resolve_error_profile,
)
from seedream_mcp.utils.images import image_validation as image_validation_module

from _log_fakes import RecordingLogger


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
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
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


def test_public_generation_methods_keep_expected_parameter_order() -> None:
    """四个公开生成方法的参数顺序与既定契约一致，prompt 恒居首。"""
    signature_expectations = {
        "text_to_image": {
            "ordered_parameters": [
                "self",
                "prompt",
                "optimize_prompt_options",
                "size",
                "watermark",
                "response_format",
                "output_format",
                "stream",
                "tools",
            ],
        },
        "image_to_image": {
            "ordered_parameters": [
                "self",
                "prompt",
                "optimize_prompt_options",
                "image",
                "layer_decomposition",
                "background",
                "size",
                "watermark",
                "response_format",
                "output_format",
                "stream",
                "tools",
            ],
        },
        "multi_image_fusion": {
            "ordered_parameters": [
                "self",
                "prompt",
                "optimize_prompt_options",
                "image",
                "size",
                "watermark",
                "response_format",
                "output_format",
                "stream",
                "tools",
            ],
        },
        "sequential_generation": {
            "ordered_parameters": [
                "self",
                "prompt",
                "optimize_prompt_options",
                "image",
                "size",
                "watermark",
                "max_images",
                "response_format",
                "output_format",
                "stream",
                "tools",
            ],
        },
    }

    for method_name, expectation in signature_expectations.items():
        signature = inspect.signature(getattr(SeedreamClient, method_name))
        assert list(signature.parameters.keys()) == expectation["ordered_parameters"]


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

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    # 设置 default_size="4K" 后不传 size 应得 4K，watermark 不传时恒为 False
    await client.text_to_image(prompt="test")
    assert captured["size"] == "4K"
    assert captured["watermark"] is False

    # 未改配置的默认实例回落 default_size=2K
    default_client = SeedreamClient(_build_config())
    default_captured: dict[str, Any] = {}

    async def fake_call_api_default(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        default_captured.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(default_client, "_call_api", fake_call_api_default)
    await default_client.text_to_image(prompt="test")
    assert default_captured["size"] == "2K"
    assert default_captured["watermark"] is False


async def test_image_to_image_resolves_relative_path_from_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径参考图经工作区根解析并编码为 data URI 发请求。"""
    workspace = tmp_path / "workspace"
    image_file = workspace / "images" / "ref.png"
    image_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=(255, 0, 0)).save(image_file)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    client = SeedreamClient(_build_config())
    captured_request: dict[str, Any] = {}

    async def fake_call_api(endpoint: str, request_data: dict[str, Any]) -> dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.image_to_image(prompt="test", image="images/ref.png", size="2K")

    assert isinstance(captured_request["image"], str)
    assert captured_request["image"].startswith("data:image/png;base64,")


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

    with pytest.raises(SeedreamValidationError, match="仅 doubao-seedream-5.0 系列"):
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
    captured_request: dict[str, Any] = {}

    async def fake_prepare_image_input(
        image: str, _roots_key: Any = None, _slot: Any = None
    ) -> str:
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.01)
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


def test_normalize_image_sequence_rejects_non_list_input() -> None:
    """image 传入非列表形态时抛出参数校验错误。"""
    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表"):
        SeedreamClient._normalize_image_sequence(
            images="not-a-list",  # type: ignore[arg-type]
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
    """status 缺省且 data 含 error 项同样须标记 partial。"""
    client = SeedreamClient(_build_config())
    result = client._build_api_result({"data": [{"error": "boom"}]})
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


async def test_sequential_generation_invalid_image_type_raises_validation_error() -> None:
    """image 传入非字符串形态时抛出参数校验错误。"""
    client = SeedreamClient(_build_config())

    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串或字符串列表"):
        await client.sequential_generation(
            prompt="test",
            max_images=2,
            image=123,  # type: ignore[arg-type]
            size="2K",
        )


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


async def test_multi_image_fusion_passes_disabled_for_seedream_50_pro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5.0 Pro 不支持组图，多图融合强制 sequential_image_generation=disabled 保持单图输出。"""
    client = SeedreamClient(_build_pro_config())
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

    assert captured_request["sequential_image_generation"] == "disabled"


async def test_multi_image_fusion_disables_sequential_for_sequential_capable_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """支持组图的默认模型在多图融合中同样恒传 disabled。"""
    client = SeedreamClient(_build_config())
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

    # 默认模型本身支持组图，但多图融合对全部模型恒传 disabled 保持单图输出；
    # 与 Pro 用例分别守护「不支持组图的模型」与「支持组图的模型」两条分支
    assert captured_request["sequential_image_generation"] == "disabled"


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
    from seedream_mcp.utils.images import image_input

    client = SeedreamClient(_build_config())
    client._image_preparer._prepare_cache_max = 3

    call_count = 0

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

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
    oversized = json.dumps({"error": {"code": "E", "message": "x" * (70 * 1024)}}).encode("utf-8")

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

    calls = {"validate": 0}
    original = client_module.validate_common_generation_params

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
    assert not any("组图输出任务完成" in message for message in fake_logger.info_messages)
    assert fake_logger.errors == []
