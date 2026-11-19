"""handle_sequential_generation 对组图配置透传与省略语义测试。

spy 记录 client.sequential_generation 的调用参数，走真实 execute_generation_handler
流水线，断言 handler 对关键 kwargs 的透传行为；组图配置官方形态另以直连 client
请求体对账。
"""

from collections.abc import Sequence
from typing import Any

import pytest

from _lib.client_fakes import _capture_call_api
from _lib.generation_fixtures import _patch_client_method_spy
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.schemas import (
    SequentialGenerationInput,
    SequentialImageGenerationOptions,
)
from seedream_mcp.tools.handlers.sequential_generation import handle_sequential_generation
from seedream_mcp.tools.pipeline.context import build_generation_context

# 默认 5.5 模型不支持组图，门控即拒绝。
_SEQUENTIAL_MODEL_ID = "doubao-seedream-5.0"


async def test_handle_sequential_generation_passes_derived_max_images_when_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_images 未显式提供时由 schema 按参考图数量推导，handler 以官方形态透传推导值。"""
    calls = _patch_client_method_spy(monkeypatch, "sequential_generation")
    config = SeedreamConfig(
        api_key="test_key", model_id=_SEQUENTIAL_MODEL_ID, auto_save_enabled=False
    )

    result = await handle_sequential_generation(
        SequentialGenerationInput(prompt="test", image="image-1"),
        config,
    )

    assert result.is_error is False
    assert len(calls) == 1
    # schema 推导：总上限 15 减去 1 张参考图。
    assert calls[0]["sequential_image_generation_options"] == {"max_images": 14}
    assert calls[0]["image"] == ["image-1"]
    # prompt 原样透传；size 未显式提供时按 config 默认值合成。
    assert calls[0]["prompt"] == "test"
    assert calls[0]["size"] == config.default_size


async def test_handle_sequential_generation_keeps_explicit_max_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_images 显式提供时原样透传，不被推导值覆盖。"""
    calls = _patch_client_method_spy(monkeypatch, "sequential_generation")
    config = SeedreamConfig(
        api_key="test_key", model_id=_SEQUENTIAL_MODEL_ID, auto_save_enabled=False
    )

    await handle_sequential_generation(
        SequentialGenerationInput(
            prompt="test",
            image="image-1",
            sequential_image_generation_options=SequentialImageGenerationOptions(max_images=3),
        ),
        config,
    )

    assert calls[0]["sequential_image_generation_options"] == {"max_images": 3}


@pytest.mark.parametrize(
    ("explicit_max_images", "reference_count"),
    [(None, 0), (None, 2), (7, 1)],
    ids=["default-text-only", "default-two-references", "explicit-single-reference"],
)
async def test_sequential_options_official_form_binds_context_and_client_paths(
    monkeypatch: pytest.MonkeyPatch, explicit_max_images: int | None, reference_count: int
) -> None:
    """组图配置官方形态：context 路径与直连 client 路径的 sequential_image_generation_options 逐键一致。"""
    config = SeedreamConfig(
        api_key="test_key", model_id=_SEQUENTIAL_MODEL_ID, auto_save_enabled=False
    )
    reference_images = (
        None if reference_count == 0 else [f"image-{i}" for i in range(reference_count)]
    )
    params = SequentialGenerationInput(
        prompt="组图",
        image=reference_images,
        sequential_image_generation_options=(
            None
            if explicit_max_images is None
            else SequentialImageGenerationOptions(max_images=explicit_max_images)
        ),
    )
    context = build_generation_context(params, config)

    captured_request: dict[str, Any] = {}

    async def fake_prepare_one(image: str) -> str:
        return f"prepared:{image}"

    async def fake_prepare_many(images: Sequence[str]) -> list[str]:
        return [f"prepared:{image}" for image in images]

    client = SeedreamClient(config)
    monkeypatch.setattr(client, "_call_api", _capture_call_api(captured_request))
    monkeypatch.setattr(client, "_prepare_image_input", fake_prepare_one)
    monkeypatch.setattr(client, "_prepare_images_in_parallel", fake_prepare_many)

    # 直连路径按用户原始形态入参，工具路径的 context 装配产物与其对账。
    await client.sequential_generation(
        prompt=params.prompt,
        image=reference_images,
        sequential_image_generation_options=(
            None if explicit_max_images is None else {"max_images": explicit_max_images}
        ),
        size=context.size,
        watermark=context.watermark,
    )

    assert captured_request["sequential_image_generation_options"] == (
        context.sequential_image_generation_options
    )
