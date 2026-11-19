"""handle_image_to_image 对参考图与透明通道字段透传测试。

spy 记录 client.image_to_image 的调用参数，走真实 execute_generation_handler
流水线，断言 handler 对关键 kwargs 的透传行为，防止漏传静默退化为普通编辑；
图层拆分场景的透传由 test_layer_decompose_handler 守护。
"""

import pytest

from _lib.generation_fixtures import _patch_client_method_spy
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.schemas import BackgroundMode, ImageToImageInput
from seedream_mcp.tools.handlers.image_to_image import handle_image_to_image


async def test_handle_image_to_image_passes_background_to_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """background/image 自 params 经 context 完整接线到 client 调用。"""
    calls = _patch_client_method_spy(monkeypatch, "image_to_image")
    config = SeedreamConfig(
        api_key="test_key", model_id="doubao-seedream-5.5", auto_save_enabled=False
    )

    result = await handle_image_to_image(
        ImageToImageInput(
            prompt="透明背景编辑",
            image="image-ref",
            background=BackgroundMode.TRANSPARENT,
        ),
        config,
    )

    assert result.is_error is False
    assert len(calls) == 1
    assert calls[0]["image"] == "image-ref"
    assert calls[0]["background"] == "transparent"
    assert calls[0]["prompt"] == "透明背景编辑"
    assert calls[0]["size"] == config.default_size


async def test_handle_image_to_image_keeps_pure_edit_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """纯编辑工具不携带图层拆分参数，background 缺省以 None 透传。"""
    calls = _patch_client_method_spy(monkeypatch, "image_to_image")
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=False)

    result = await handle_image_to_image(
        ImageToImageInput(prompt="test", image="image-ref"),
        config,
    )

    assert result.is_error is False
    assert len(calls) == 1
    assert "layer_decomposition" not in calls[0]
    assert "layer_decomposition_options" not in calls[0]
    assert calls[0]["background"] is None
    assert calls[0]["size"] == config.default_size
