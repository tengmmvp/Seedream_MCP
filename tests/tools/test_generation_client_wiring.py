"""五个生成 handler 到 client 方法的公共参数接线守护。

各 _execute 闭包手抄同一组由 GenerationExecutionContext 派生的公共 kwargs，
client 生成方法形参全带默认值，漏传一项即静默退化为默认行为且既有测试不红。期望
键集按 client 方法签名与 GenerationExecutionContext 字段名的交集推导，逐工具
做集合级比对（不比顺序），新增公共参数改漏任一 handler 时在此变红。
client.image_to_image 同时承载纯编辑与图层拆分两个 MCP 工具意图，拆分两参数由
layer_decompose 工具接线，对 image_to_image 的期望键集显式排除；图层拆分不暴露
background（输入模型不含 _BackgroundInput），其期望键集同步排除。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import fields

import pytest
from mcp.types import CallToolResult
from pydantic import BaseModel

from _lib.generation_fixtures import _patch_client_method_spy
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.pipeline.context import GenerationExecutionContext
from seedream_mcp.tools.schemas import (
    ImageToImageInput,
    LayerDecomposeInput,
    MultiImageFusionInput,
    SequentialImageGenerationOptions,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.tools.handlers.image_to_image import handle_image_to_image
from seedream_mcp.tools.handlers.layer_decompose import handle_layer_decompose
from seedream_mcp.tools.handlers.multi_image_fusion import handle_multi_image_fusion
from seedream_mcp.tools.handlers.sequential_generation import handle_sequential_generation
from seedream_mcp.tools.handlers.text_to_image import handle_text_to_image

_HANDLER = Callable[[BaseModel, SeedreamConfig], Awaitable[CallToolResult]]

_CONTEXT_FIELD_NAMES = frozenset(field.name for field in fields(GenerationExecutionContext))

_IMAGE_TO_IMAGE_EXCLUDED_KEYS = frozenset({"layer_decomposition", "layer_decomposition_options"})

_LAYER_DECOMPOSE_EXCLUDED_KEYS = frozenset({"background"})


def _expected_common_kwargs(client_method_name: str, excluded: frozenset[str]) -> set[str]:
    """由 client 方法签名与 context 字段名的交集扣除排除键推导期望键集。"""
    signature = inspect.signature(getattr(SeedreamClient, client_method_name))
    return {name for name in signature.parameters if name in _CONTEXT_FIELD_NAMES} - excluded


@pytest.mark.parametrize(
    ("handler", "client_method_name", "params", "model_id", "excluded_keys"),
    [
        (
            handle_text_to_image,
            "text_to_image",
            TextToImageInput(prompt="test"),
            "doubao-seedream-5.0",
            frozenset(),
        ),
        (
            handle_image_to_image,
            "image_to_image",
            ImageToImageInput(prompt="test", image="image-ref"),
            "doubao-seedream-5.0",
            _IMAGE_TO_IMAGE_EXCLUDED_KEYS,
        ),
        (
            handle_multi_image_fusion,
            "multi_image_fusion",
            MultiImageFusionInput(prompt="test", image=["image-1", "image-2"]),
            "doubao-seedream-5.0",
            frozenset(),
        ),
        (
            handle_sequential_generation,
            "sequential_generation",
            SequentialGenerationInput(
                prompt="test",
                image="image-1",
                sequential_image_generation_options=SequentialImageGenerationOptions(max_images=3),
            ),
            # 默认 5.5 模型不支持组图，门控即拒绝。
            "doubao-seedream-5.0",
            frozenset(),
        ),
        (
            handle_layer_decompose,
            "image_to_image",
            LayerDecomposeInput(image="image-ref"),
            # 5.0 属 lite 家族不支持拆分，此处取 5.5。
            "doubao-seedream-5.5",
            _LAYER_DECOMPOSE_EXCLUDED_KEYS,
        ),
    ],
    ids=[
        "text_to_image",
        "image_to_image",
        "multi_image_fusion",
        "sequential_generation",
        "layer_decompose",
    ],
)
async def test_generation_impl_wires_context_common_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    handler: _HANDLER,
    client_method_name: str,
    params: BaseModel,
    model_id: str,
    excluded_keys: frozenset[str],
) -> None:
    """handler 闭包传入的公共参数键集与「client 签名 ∩ context 字段」扣除显式排除键后推导的期望键集相等。"""
    # 期望键集取自真实签名，须在替身替换类方法之前推导。
    expected_common_keys = _expected_common_kwargs(client_method_name, excluded_keys)
    calls = _patch_client_method_spy(monkeypatch, client_method_name)
    config = SeedreamConfig(api_key="test_key", model_id=model_id, auto_save_enabled=False)

    result = await handler(params, config)

    assert result.is_error is False
    assert len(calls) == 1
    passed_common_keys = set(calls[0]) & _CONTEXT_FIELD_NAMES
    assert passed_common_keys == expected_common_keys, (
        f"{client_method_name} 的公共参数接线漂移: 实传 {sorted(passed_common_keys)},"
        f" 期望 {sorted(expected_common_keys)}"
    )
