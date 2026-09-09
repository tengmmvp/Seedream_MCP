"""handle_image_to_image 对参考图与图层/透明通道字段透传测试。

spy 记录 client.image_to_image 的调用参数，走真实 execute_generation_handler
流水线，断言 handler 对关键 kwargs 的透传行为，防止漏传静默退化为普通编辑。
"""

import pytest

from _generation_fixtures import _patch_client_method_spy
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.schemas import BackgroundMode, ImageToImageInput
from seedream_mcp.tools.impl.image_to_image import handle_image_to_image


async def test_handle_image_to_image_passes_layer_fields_to_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """layer_decomposition/background/image 自 params 经 context 完整接线到 client 调用。"""
    calls = _patch_client_method_spy(monkeypatch, "image_to_image")
    # 图层拆分与透明通道仅 5.0 Pro 支持，模型须随字段一并切换才能通过能力校验
    config = SeedreamConfig(
        api_key="test_key", model_id="doubao-seedream-5.0-pro", auto_save_enabled=False
    )

    result = await handle_image_to_image(
        ImageToImageInput(
            prompt="拆分图层",
            image="image-ref",
            layer_decomposition=True,
            background=BackgroundMode.TRANSPARENT,
        ),
        config,
    )

    assert result.is_error is False
    assert len(calls) == 1
    assert calls[0]["image"] == "image-ref"
    assert calls[0]["layer_decomposition"] is True
    assert calls[0]["background"] == "transparent"
    # prompt 原样透传；图层拆分场景未显式提供 size 时按 auto 合成。
    assert calls[0]["prompt"] == "拆分图层"
    assert calls[0]["size"] == "auto"


async def test_handle_image_to_image_transmits_neutral_layer_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未启用图层能力时 layer_decomposition/background 以 False/None 透传，不缺键。"""
    calls = _patch_client_method_spy(monkeypatch, "image_to_image")
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=False)

    result = await handle_image_to_image(
        ImageToImageInput(prompt="test", image="image-ref"),
        config,
    )

    assert result.is_error is False
    assert len(calls) == 1
    assert calls[0]["layer_decomposition"] is False
    assert calls[0]["background"] is None
    # size 未显式提供时按 config 默认值合成。
    assert calls[0]["size"] == config.default_size
