"""handle_sequential_generation 对 max_images 透传与省略语义测试。"""

import pytest
from mcp.types import CallToolResult

import seedream_mcp.tools.impl.sequential_generation as sequential_generation_module
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import build_generation_context
from seedream_mcp.tools.core.schemas import SequentialGenerationInput


async def test_handle_sequential_generation_passes_derived_max_images_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_images 未显式提供时由 schema 按参考图数量推导，handler 透传推导值。"""
    config = SeedreamConfig(api_key="test_key")
    captured_kwargs: dict = {}

    class FakeClient:
        async def sequential_generation(self, **kwargs):
            captured_kwargs.update(kwargs)
            return {"success": True, "data": [], "usage": {}, "status": "ok"}

    async def fake_execute_generation_handler(**kwargs):
        context = build_generation_context(kwargs["params"], kwargs["config"])
        await kwargs["request_executor"](FakeClient(), context)
        return CallToolResult(content=[])

    monkeypatch.setattr(
        sequential_generation_module,
        "execute_generation_handler",
        fake_execute_generation_handler,
    )

    await sequential_generation_module.handle_sequential_generation(
        SequentialGenerationInput(prompt="test", image="image-1"),
        config,
    )

    # schema 推导：总上限 15 减去 1 张参考图
    assert captured_kwargs["max_images"] == 14
    assert captured_kwargs["image"] == ["image-1"]
    # prompt 原样透传；size 未显式提供时按 config 默认值合成。
    assert captured_kwargs["prompt"] == "test"
    assert captured_kwargs["size"] == config.default_size


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
        context = build_generation_context(kwargs["params"], kwargs["config"])
        await kwargs["request_executor"](FakeClient(), context)
        return CallToolResult(content=[])

    monkeypatch.setattr(
        sequential_generation_module,
        "execute_generation_handler",
        fake_execute_generation_handler,
    )

    await sequential_generation_module.handle_sequential_generation(
        SequentialGenerationInput(prompt="test", image="image-1", max_images=3),
        config,
    )

    assert captured_kwargs["max_images"] == 3
