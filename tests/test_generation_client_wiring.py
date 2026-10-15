"""四个生成 handler 到 client 方法的公共参数接线守护。

四条 _execute 闭包各自手抄同一组由 GenerationExecutionContext 派生的公共 kwargs，
client 生成方法形参全带默认值，漏传一项即静默退化为默认行为且既有测试不红。期望
键集按 client 方法签名与 GenerationExecutionContext 字段名的交集推导，逐工具
做集合级比对（不比顺序），新增公共参数改漏任一 handler 时在此变红。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import fields

import pytest
from mcp.types import CallToolResult
from pydantic import BaseModel

from _generation_fixtures import _patch_client_method_spy
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.pipeline.context import GenerationExecutionContext
from seedream_mcp.tools.schemas import (
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.tools.handlers.image_to_image import handle_image_to_image
from seedream_mcp.tools.handlers.multi_image_fusion import handle_multi_image_fusion
from seedream_mcp.tools.handlers.sequential_generation import handle_sequential_generation
from seedream_mcp.tools.handlers.text_to_image import handle_text_to_image

_HANDLER = Callable[[BaseModel, SeedreamConfig], Awaitable[CallToolResult]]

_CONTEXT_FIELD_NAMES = frozenset(field.name for field in fields(GenerationExecutionContext))


def _expected_common_kwargs(client_method_name: str) -> set[str]:
    """由 client 方法签名与 context 字段名的交集推导期望的公共参数键集。"""
    signature = inspect.signature(getattr(SeedreamClient, client_method_name))
    return {name for name in signature.parameters if name in _CONTEXT_FIELD_NAMES}


@pytest.mark.parametrize(
    ("handler", "client_method_name", "params"),
    [
        (handle_text_to_image, "text_to_image", TextToImageInput(prompt="test")),
        (
            handle_image_to_image,
            "image_to_image",
            ImageToImageInput(prompt="test", image="image-ref"),
        ),
        (
            handle_multi_image_fusion,
            "multi_image_fusion",
            MultiImageFusionInput(prompt="test", image=["image-1", "image-2"]),
        ),
        (
            handle_sequential_generation,
            "sequential_generation",
            SequentialGenerationInput(prompt="test", image="image-1", max_images=3),
        ),
    ],
    ids=["text_to_image", "image_to_image", "multi_image_fusion", "sequential_generation"],
)
async def test_generation_impl_wires_context_common_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    handler: _HANDLER,
    client_method_name: str,
    params: BaseModel,
) -> None:
    """handler 闭包传入的公共参数键集与「client 签名 ∩ context 字段」推导的期望键集相等。"""
    # 期望键集取自真实签名，须在替身替换类方法之前推导。
    expected_common_keys = _expected_common_kwargs(client_method_name)
    calls = _patch_client_method_spy(monkeypatch, client_method_name)
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=False)

    result = await handler(params, config)

    assert result.is_error is False
    assert len(calls) == 1
    passed_common_keys = set(calls[0]) & _CONTEXT_FIELD_NAMES
    assert passed_common_keys == expected_common_keys, (
        f"{client_method_name} 的公共参数接线漂移: 实传 {sorted(passed_common_keys)},"
        f" 期望 {sorted(expected_common_keys)}"
    )
