"""handle_sequential_generation 对 max_images 透传与省略语义测试。

spy 记录 client.sequential_generation 的调用参数，走真实 execute_generation_handler
流水线，断言 handler 对关键 kwargs 的透传行为。
"""

from typing import Any

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.schemas import SequentialGenerationInput
from seedream_mcp.tools.impl.sequential_generation import handle_sequential_generation


def _patch_sequential_generation_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """monkeypatch client.sequential_generation 为记录调用参数并返回成功的替身。"""
    calls: list[dict[str, Any]] = []

    async def fake_sequential_generation(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self
        calls.append(kwargs)
        return {"success": True, "data": [], "usage": {}, "status": "ok"}

    monkeypatch.setattr(SeedreamClient, "sequential_generation", fake_sequential_generation)
    return calls


async def test_handle_sequential_generation_passes_derived_max_images_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_images 未显式提供时由 schema 按参考图数量推导，handler 透传推导值。"""
    calls = _patch_sequential_generation_spy(monkeypatch)
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=False)

    result = await handle_sequential_generation(
        SequentialGenerationInput(prompt="test", image="image-1"),
        config,
    )

    assert result.is_error is False
    assert len(calls) == 1
    # schema 推导：总上限 15 减去 1 张参考图
    assert calls[0]["max_images"] == 14
    assert calls[0]["image"] == ["image-1"]
    # prompt 原样透传；size 未显式提供时按 config 默认值合成。
    assert calls[0]["prompt"] == "test"
    assert calls[0]["size"] == config.default_size


async def test_handle_sequential_generation_keeps_explicit_max_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_images 显式提供时原样透传，不被推导值覆盖。"""
    calls = _patch_sequential_generation_spy(monkeypatch)
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=False)

    await handle_sequential_generation(
        SequentialGenerationInput(prompt="test", image="image-1", max_images=3),
        config,
    )

    assert calls[0]["max_images"] == 3
