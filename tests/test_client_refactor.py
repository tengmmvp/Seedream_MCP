import base64
import asyncio
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

    def info(self, message: str, *args: Any) -> None:
        self.info_messages.append(message.format(*args) if args else message)

    def debug(self, message: str, *args: Any) -> None:
        del message, args

    def warning(self, message: str, *args: Any) -> None:
        del message, args

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        del message, args, kwargs


def _build_config() -> SeedreamConfig:
    return SeedreamConfig(api_key="test_key", max_retries=1)


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

    oversized_raw = b"a" * (10 * 1024 * 1024 + 1)
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
