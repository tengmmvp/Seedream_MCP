import base64
import asyncio
import inspect
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.errors import SeedreamValidationError


class FakeLogger:
    def __init__(self) -> None:
        self.info_messages: List[str] = []

    def opt(self, *args: Any, **kwargs: Any) -> "FakeLogger":
        return self

    def info(self, message: str, *args: Any) -> None:
        self.info_messages.append(message.format(*args) if args else message)

    def debug(self, message: str, *args: Any) -> None:
        del message, args

    def warning(self, message: str, *args: Any) -> None:
        del message, args

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        del message, args, kwargs


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


def test_public_generation_methods_keep_expected_parameter_order() -> None:
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


@pytest.mark.asyncio
async def test_text_to_image_log_does_not_include_prompt_plaintext(monkeypatch) -> None:
    client = SeedreamClient(_build_config())
    fake_logger = FakeLogger()
    monkeypatch.setattr(client, "logger", fake_logger)

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint, request_data
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    prompt = "top secret prompt content"
    await client.text_to_image(prompt=prompt, size="2K")

    joined_logs = "\n".join(fake_logger.info_messages)
    assert "prompt_meta=" in joined_logs
    assert prompt not in joined_logs


@pytest.mark.asyncio
async def test_image_to_image_resolves_relative_path_from_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    image_file = workspace / "images" / "ref.png"
    image_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color=(255, 0, 0)).save(image_file)

    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))

    client = SeedreamClient(_build_config())
    captured_request: Dict[str, Any] = {}

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.image_to_image(prompt="test", image="images/ref.png", size="2K")

    assert isinstance(captured_request["image"], str)
    assert captured_request["image"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_text_to_image_includes_seedream_50_output_format_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(
        SeedreamConfig(
            api_key="test_key",
            model_id="doubao-seedream-5-0-260128",
            max_retries=1,
        )
    )
    captured_request: Dict[str, Any] = {}

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
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


@pytest.mark.asyncio
async def test_text_to_image_normalizes_seedream_50_alias_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(
        SeedreamConfig(
            api_key="test_key",
            model_id="doubao-seedream-5.0",
            max_retries=1,
        )
    )
    captured_request: Dict[str, Any] = {}

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.text_to_image(prompt="test", size="2K")

    assert client.config.model_id == "doubao-seedream-5-0-260128"
    assert captured_request["model"] == "doubao-seedream-5-0-260128"


@pytest.mark.asyncio
async def test_call_api_parses_non_stream_response() -> None:
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

    client = SeedreamClient(_build_config())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        result = await client._call_api("text_to_image", {"prompt": "hello"})
    finally:
        await client.close()

    assert result["success"] is True
    assert result["status"] == "succeeded"
    assert result["data"][0]["url"] == "https://example.com/1.png"


@pytest.mark.asyncio
async def test_text_to_image_rejects_output_format_for_seedream_45_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(
        SeedreamConfig(
            api_key="test_key",
            model_id="doubao-seedream-4-5-251128",
            max_retries=1,
        )
    )
    api_called = False

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal api_called
        del endpoint, request_data
        api_called = True
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    with pytest.raises(SeedreamValidationError, match="仅 doubao-seedream-5.0 系列"):
        await client.text_to_image(prompt="test", size="2K", output_format="png")

    assert api_called is False


@pytest.mark.asyncio
async def test_call_api_parses_sse_response() -> None:
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

    client = SeedreamClient(_build_config())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        result = await client._call_api("text_to_image", {"prompt": "hello", "stream": True})
    finally:
        await client.close()

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["usage"]["generated_images"] == 1
    assert result["data"][0]["url"] == "https://example.com/1.png"


@pytest.mark.asyncio
async def test_call_api_parses_sse_partial_failed_event() -> None:
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

    client = SeedreamClient(_build_config())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    try:
        result = await client._call_api("text_to_image", {"prompt": "hello", "stream": True})
    finally:
        await client.close()

    assert result["success"] is True
    assert result["status"] == "partial"
    assert len(result["data"]) == 1
    assert result["data"][0]["type"] == "image_generation.partial_failed"
    assert result["data"][0]["image_index"] == 2
    assert result["data"][0]["error"]["code"] == "OutputImageSensitiveContentDetected"
    assert result["data"][0]["error"]["message"] == "blocked"


@pytest.mark.asyncio
async def test_multi_image_fusion_prepares_images_with_limited_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_config())
    client._image_prepare_concurrency = 2

    active_count = 0
    max_active_count = 0
    captured_request: Dict[str, Any] = {}

    async def fake_prepare_image_input(image: str) -> str:
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.01)
        active_count -= 1
        return f"prepared:{image}"

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_image_input", fake_prepare_image_input)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.multi_image_fusion(
        prompt="test",
        image=["image-1", "image-2", "image-3"],
        size="2K",
    )

    assert max_active_count > 1
    assert captured_request["image"] == [
        "prepared:image-1",
        "prepared:image-2",
        "prepared:image-3",
    ]


@pytest.mark.asyncio
async def test_multi_image_fusion_accepts_up_to_14_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_config())
    input_images = [f"https://example.com/{idx}.png" for idx in range(14)]
    captured_request: Dict[str, Any] = {}

    async def fake_prepare_images_in_parallel(images: List[str]) -> List[str]:
        return [f"prepared:{item}" for item in images]

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_images_in_parallel)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.multi_image_fusion(prompt="test", image=input_images, size="2K")

    assert len(captured_request["image"]) == 14
    assert captured_request["image"][0] == "prepared:https://example.com/0.png"
    assert captured_request["image"][-1] == "prepared:https://example.com/13.png"


@pytest.mark.asyncio
async def test_multi_image_fusion_rejects_more_than_14_images() -> None:
    client = SeedreamClient(_build_config())
    input_images = [f"https://example.com/{idx}.png" for idx in range(15)]

    with pytest.raises(SeedreamValidationError, match="image 数量不能超过 14"):
        await client.multi_image_fusion(prompt="test", image=input_images, size="2K")


@pytest.mark.asyncio
async def test_sequential_generation_prepares_reference_images_with_limited_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_config())
    client._image_prepare_concurrency = 2

    active_count = 0
    max_active_count = 0
    captured_request: Dict[str, Any] = {}

    async def fake_prepare_image_input(image: str) -> str:
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0.01)
        active_count -= 1
        return f"prepared:{image}"

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_image_input", fake_prepare_image_input)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.sequential_generation(
        prompt="test",
        max_images=3,
        image=["image-1", "image-2", "image-3"],
        size="2K",
    )

    assert max_active_count > 1
    assert captured_request["image"] == [
        "prepared:image-1",
        "prepared:image-2",
        "prepared:image-3",
    ]


@pytest.mark.asyncio
async def test_sequential_generation_without_max_images_uses_reference_aware_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_config())
    captured_request: Dict[str, Any] = {}

    async def fake_prepare_image_input(image: str) -> str:
        return f"prepared:{image}"

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
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
    with pytest.raises(SeedreamValidationError, match="image 参数必须是字符串列表"):
        SeedreamClient._normalize_image_sequence(
            images="not-a-list",  # type: ignore[arg-type]
            min_count=1,
            max_count=2,
            field_name="image",
        )


def test_summarize_prompt_does_not_expose_prompt_plaintext() -> None:
    prompt = "sensitive prompt"
    meta = SeedreamClient._summarize_prompt(prompt)

    assert "len=" in meta
    assert "sha256=" in meta
    assert prompt not in meta


@pytest.mark.asyncio
async def test_image_to_image_invalid_data_uri_fails_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_config())
    api_called = False

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
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


@pytest.mark.asyncio
async def test_multi_image_fusion_oversized_data_uri_fails_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_config())
    api_called = False

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal api_called
        del endpoint, request_data
        api_called = True
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_call_api", fake_call_api)

    oversized_raw = b"a" * (30 * 1024 * 1024 + 1)
    oversized_b64 = base64.b64encode(oversized_raw).decode("ascii")
    oversized_data_uri = f"data:image/png;base64,{oversized_b64}"

    with pytest.raises(SeedreamValidationError, match="数据过大"):
        await client.multi_image_fusion(
            prompt="test",
            image=[oversized_data_uri, oversized_data_uri],
            size="2K",
        )

    assert api_called is False


@pytest.mark.asyncio
async def test_sequential_generation_invalid_image_type_raises_validation_error() -> None:
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


@pytest.mark.asyncio
async def test_sequential_generation_rejects_seedream_50_pro() -> None:
    client = SeedreamClient(_build_pro_config())

    with pytest.raises(SeedreamValidationError, match="5.0-pro 不支持组图"):
        await client.sequential_generation(prompt="test", max_images=3, size="2K")


@pytest.mark.asyncio
async def test_text_to_image_rejects_stream_for_seedream_50_pro() -> None:
    client = SeedreamClient(_build_pro_config())

    with pytest.raises(SeedreamValidationError, match="5.0-pro 不支持流式输出"):
        await client.text_to_image(prompt="test", size="2K", stream=True)


@pytest.mark.asyncio
async def test_multi_image_fusion_passes_disabled_for_seedream_50_pro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_pro_config())
    captured_request: Dict[str, Any] = {}

    async def fake_prepare_images_in_parallel(images: List[str]) -> List[str]:
        return [f"prepared:{item}" for item in images]

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_images_in_parallel)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.multi_image_fusion(prompt="test", image=["image-1", "image-2"], size="2K")

    # 5.0 Pro 支持 disabled（关闭组图、单图输出），仅不支持 auto（开启组图）
    assert captured_request["sequential_image_generation"] == "disabled"


@pytest.mark.asyncio
async def test_multi_image_fusion_keeps_sequential_param_for_lite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_config())
    captured_request: Dict[str, Any] = {}

    async def fake_prepare_images_in_parallel(images: List[str]) -> List[str]:
        return [f"prepared:{item}" for item in images]

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_images_in_parallel)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.multi_image_fusion(prompt="test", image=["image-1", "image-2"], size="2K")

    assert captured_request["sequential_image_generation"] == "disabled"


@pytest.mark.asyncio
async def test_multi_image_fusion_rejects_more_than_10_images_for_pro() -> None:
    client = SeedreamClient(_build_pro_config())
    input_images = [f"https://example.com/{idx}.png" for idx in range(11)]

    with pytest.raises(SeedreamValidationError, match="image 数量不能超过 10"):
        await client.multi_image_fusion(prompt="test", image=input_images, size="2K")


@pytest.mark.asyncio
async def test_multi_image_fusion_accepts_up_to_10_images_for_pro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SeedreamClient(_build_pro_config())
    input_images = [f"https://example.com/{idx}.png" for idx in range(10)]
    captured_request: Dict[str, Any] = {}

    async def fake_prepare_images_in_parallel(images: List[str]) -> List[str]:
        return [f"prepared:{item}" for item in images]

    async def fake_call_api(endpoint: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        del endpoint
        captured_request.update(request_data)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_images_in_parallel)
    monkeypatch.setattr(client, "_call_api", fake_call_api)

    await client.multi_image_fusion(prompt="test", image=input_images, size="2K")

    assert len(captured_request["image"]) == 10


@pytest.mark.asyncio
async def test_prepare_image_input_caches_result_and_evicts_fifo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_prepare_image_input 命中缓存不重复调用底层，超限按 FIFO 淘汰最旧条目。"""
    client = SeedreamClient(_build_config())
    client._prepare_cache_max = 3

    call_count = 0

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}"

    monkeypatch.setattr("seedream_mcp.utils.image_input.prepare_image_input", fake_prepare)

    # 同一输入第二次走缓存，底层 prepare_image_input 只调一次
    first = await client._prepare_image_input("img-1")
    second = await client._prepare_image_input("img-1")
    assert first == "prepared:img-1"
    assert second == "prepared:img-1"
    assert call_count == 1

    # 填满缓存（img-1 / img-2 / img-3）
    await client._prepare_image_input("img-2")
    await client._prepare_image_input("img-3")
    assert len(client._prepare_cache) == 3
    assert call_count == 3

    # 再加一条触发 FIFO 淘汰最旧的 img-1，缓存大小不超过 max
    await client._prepare_image_input("img-4")
    assert len(client._prepare_cache) == 3
    assert call_count == 4

    # img-1 已被淘汰，再次请求应重新调用底层
    await client._prepare_image_input("img-1")
    assert call_count == 5
