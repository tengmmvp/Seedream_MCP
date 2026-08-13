"""handle_sequential_generation 对 max_images 透传与省略语义测试。"""

import pytest
from mcp.types import CallToolResult

import seedream_mcp.tools.impl.sequential_generation as sequential_generation_module
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import build_generation_context


@pytest.mark.asyncio
async def test_handle_sequential_generation_omits_max_images_when_not_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SeedreamConfig(api_key="test_key")
    captured_kwargs: dict = {}

    class FakeClient:
        async def sequential_generation(self, **kwargs):
            captured_kwargs.update(kwargs)
            return {"success": True, "data": [], "usage": {}, "status": "ok"}

    async def fake_execute_generation_handler(**kwargs):
        context = build_generation_context(kwargs["arguments"], kwargs["config"])
        await kwargs["request_executor"](FakeClient(), context)
        return CallToolResult(content=[])

    monkeypatch.setattr(
        sequential_generation_module,
        "execute_generation_handler",
        fake_execute_generation_handler,
    )

    await sequential_generation_module.handle_sequential_generation(
        {"prompt": "test", "image": "image-1"},
        config,
    )

    assert "max_images" in captured_kwargs
    assert captured_kwargs["max_images"] is None
    assert captured_kwargs["image"] == "image-1"


@pytest.mark.asyncio
async def test_handle_sequential_generation_keeps_explicit_max_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SeedreamConfig(api_key="test_key")
    captured_kwargs: dict = {}

    class FakeClient:
        async def sequential_generation(self, **kwargs):
            captured_kwargs.update(kwargs)
            return {"success": True, "data": [], "usage": {}, "status": "ok"}

    async def fake_execute_generation_handler(**kwargs):
        context = build_generation_context(kwargs["arguments"], kwargs["config"])
        await kwargs["request_executor"](FakeClient(), context)
        return CallToolResult(content=[])

    monkeypatch.setattr(
        sequential_generation_module,
        "execute_generation_handler",
        fake_execute_generation_handler,
    )

    await sequential_generation_module.handle_sequential_generation(
        {"prompt": "test", "image": "image-1", "max_images": 3},
        config,
    )

    assert captured_kwargs["max_images"] == 3
