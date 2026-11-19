"""handle_layer_decompose 对拆分意图与配置字段的透传及能力门控测试。

spy 记录 client.image_to_image 的调用参数，走真实 execute_generation_handler
流水线，断言拆分场景的固定 layer_decomposition=true 与 options 归一透传，
防止漏传静默退化为普通编辑；能力门控拒绝路径断言计费请求不分发。
"""

from typing import cast

import pytest
from mcp.types import TextContent

from _lib.generation_fixtures import _patch_client_method_spy
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.schemas import LayerDecomposeInput, LayerDecompositionOptions
from seedream_mcp.tools.handlers.layer_decompose import handle_layer_decompose


async def test_handle_layer_decompose_passes_fixed_intent_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置 options 时拆分意图恒 true、prompt 可缺省、size 按 auto 合成。"""
    calls = _patch_client_method_spy(monkeypatch, "image_to_image")
    config = SeedreamConfig(
        api_key="test_key", model_id="doubao-seedream-5.5", auto_save_enabled=False
    )

    result = await handle_layer_decompose(LayerDecomposeInput(image="image-ref"), config)

    assert result.is_error is False
    assert len(calls) == 1
    assert calls[0]["image"] == "image-ref"
    assert calls[0]["layer_decomposition"] is True
    assert calls[0]["layer_decomposition_options"] is None
    assert calls[0]["prompt"] is None
    # 图层拆分不暴露 background。
    assert "background" not in calls[0]
    assert calls[0]["size"] == "auto"


async def test_handle_layer_decompose_transmits_normalized_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 options 经 context 归一为仅含显式键的字典透传到 client。"""
    calls = _patch_client_method_spy(monkeypatch, "image_to_image")
    config = SeedreamConfig(
        api_key="test_key", model_id="doubao-seedream-5.5", auto_save_enabled=False
    )

    result = await handle_layer_decompose(
        LayerDecomposeInput(
            image="image-ref",
            layer_decomposition_options=LayerDecompositionOptions(
                max_images=50, text_recognition=True
            ),
        ),
        config,
    )

    assert result.is_error is False
    assert calls[0]["layer_decomposition_options"] == {
        "max_images": 50,
        "text_recognition": True,
    }


@pytest.mark.parametrize(
    "model_id",
    ["doubao-seedream-5.0-pro", "doubao-seedream-5.0-flash"],
    ids=["pro", "flash"],
)
async def test_handle_layer_decompose_rejects_options_on_models_without_support(
    monkeypatch: pytest.MonkeyPatch, model_id: str
) -> None:
    """5.0 Pro/Flash 支持拆分但不支持拆分配置，options 在上下文校验期拒绝。"""
    calls = _patch_client_method_spy(monkeypatch, "image_to_image")
    config = SeedreamConfig(api_key="test_key", model_id=model_id, auto_save_enabled=False)

    result = await handle_layer_decompose(
        LayerDecomposeInput(
            image="image-ref",
            layer_decomposition_options=LayerDecompositionOptions(max_images=50),
        ),
        config,
    )

    assert result.is_error is True
    assert calls == []
    text = cast(TextContent, result.content[0]).text
    assert "不支持图层拆分配置与富文本 OCR（layer_decomposition_options）" in text


async def test_handle_layer_decompose_rejects_family_without_layer_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不支持拆分的家族调用拆分工具即拒绝，计费请求不分发。"""
    calls = _patch_client_method_spy(monkeypatch, "image_to_image")
    config = SeedreamConfig(
        api_key="test_key", model_id="doubao-seedream-5.0", auto_save_enabled=False
    )

    result = await handle_layer_decompose(LayerDecomposeInput(image="image-ref"), config)

    assert result.is_error is True
    assert calls == []
    text = cast(TextContent, result.content[0]).text
    assert "不支持图层拆分（layer_decomposition）" in text
