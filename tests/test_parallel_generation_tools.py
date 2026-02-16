from importlib import import_module

import pytest
from pydantic import ValidationError

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.schemas import TextToImageInput
from seedream_mcp.tools.impl.image_to_image import handle_image_to_image
from seedream_mcp.tools.impl.multi_image_fusion import handle_multi_image_fusion
from seedream_mcp.tools.impl.sequential_generation import handle_sequential_generation
from seedream_mcp.tools.impl.text_to_image import handle_text_to_image


def _build_config() -> SeedreamConfig:
    return SeedreamConfig(api_key="test_key", max_retries=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "method_name", "arguments"),
    [
        (handle_text_to_image, "text_to_image", {"prompt": "test"}),
        (
            handle_image_to_image,
            "image_to_image",
            {"prompt": "test", "image": "https://example.com/ref.png"},
        ),
        (
            handle_multi_image_fusion,
            "multi_image_fusion",
            {
                "prompt": "test",
                "image": ["https://example.com/1.png", "https://example.com/2.png"],
            },
        ),
        (
            handle_sequential_generation,
            "sequential_generation",
            {"prompt": "test", "image": "https://example.com/ref.png"},
        ),
    ],
)
async def test_generation_handlers_support_parallel_requests(
    monkeypatch: pytest.MonkeyPatch,
    handler,
    method_name: str,
    arguments: dict,
) -> None:
    call_count = 0

    async def fake_method(self, **kwargs):  # noqa: ANN001
        nonlocal call_count
        del self, kwargs
        call_count += 1
        return {
            "success": True,
            "data": [{"url": f"https://example.com/{call_count}.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }

    client_module = import_module("seedream_mcp.client")
    client_cls = getattr(client_module, "SeedreamClient")
    monkeypatch.setattr(client_cls, method_name, fake_method)

    result = await handler(
        {
            **arguments,
            "request_count": 3,
            "parallelism": 2,
        },
        _build_config(),
    )

    assert call_count == 3
    response_text = result[0].text
    assert "并行请求信息:" in response_text
    assert "请求总数: 3" in response_text
    assert "成功请求: 3" in response_text


def test_parallel_options_reject_request_count_over_limit_in_schema() -> None:
    with pytest.raises(ValidationError, match="request_count"):
        TextToImageInput(prompt="test", request_count=5)


def test_parallel_options_reject_parallelism_greater_than_request_count_in_schema() -> None:
    with pytest.raises(ValidationError, match="parallelism 不能大于 request_count"):
        TextToImageInput(prompt="test", request_count=2, parallelism=3)


def test_parallel_options_reject_stream_with_parallel_requests_in_schema() -> None:
    with pytest.raises(ValidationError, match="stream=true 时 request_count 必须为 1"):
        TextToImageInput(prompt="test", request_count=2, stream=True)
