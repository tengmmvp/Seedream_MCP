"""client 侧单请求产图数投影与 tools 层产图数元数据的孪生口径守护。

client/_http.py 的 _response_image_count 自最终请求体推导产图数上限，供响应体
读取上限按请求形态投影；tools 层 BaseGenerationInput.per_request_image_count
自输入投影键做同口径推导（其签名形态、能力门控与最坏投影域由
tests/tools/test_per_request_image_count.py 锁定）。两处独立演化会静默漂移，
本文件以分支取值直锁与真实请求链逐家族对账双轨守护：任一分支派发、请求体
场景键的序列化映射或推导口径漂移即红。
"""

from __future__ import annotations

from typing import Any

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.client._http import _response_image_count
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.schemas import (
    ImageToImageInput,
    LayerDecomposeInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.utils.model.capabilities import MODEL_ALIASES, get_model_capabilities

from _lib.client_fakes import _capture_call_api

# 组图对账代表档：显式值与按参考图推导档、有无参考图的组合。
_SEQUENTIAL_PARITY_CASES: tuple[tuple[int | None, int], ...] = (
    (None, 0),
    (None, 3),
    (4, 3),
    (1, 1),
)

_REF_IMAGE = "https://example.com/ref.png"
_REF_IMAGE_2 = "https://example.com/ref2.png"


def test_response_image_count_branch_values() -> None:
    """五分支取值锁定：组图显式值、拆分显式值、拆分官方默认、能力表满额、其余单图。"""
    model_55 = "doubao-seedream-5-5-261028"
    assert (
        _response_image_count({"sequential_image_generation_options": {"max_images": 7}}, model_55)
        == 7
    )
    assert (
        _response_image_count(
            {"layer_decomposition": True, "layer_decomposition_options": {"max_images": 5}},
            model_55,
        )
        == 5
    )
    assert _response_image_count({"layer_decomposition": True}, model_55) == 30
    # 5.0 Pro 与 5.0 Flash 不支持拆分配置，请求体不含 options，按能力表满额 17 投影。
    assert (
        _response_image_count({"layer_decomposition": True}, "doubao-seedream-5-0-pro-260628") == 17
    )
    # 不支持拆分的家族同样取能力表值 0；该请求经工具层校验拒绝，形态仅孪生对账可见。
    assert _response_image_count({"layer_decomposition": True}, "doubao-seedream-5-0-260128") == 0
    assert _response_image_count({"prompt": "p"}, model_55) == 1


@pytest.mark.parametrize(
    "model_id",
    # 5.0 与 5.0-lite 别名指向同一 Model ID，家族参数去重。
    tuple(dict.fromkeys(MODEL_ALIASES.values())),
)
async def test_response_image_count_matches_tools_projection_over_chain(
    model_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实请求链的产图数投影与 tools 层元数据逐场景同值。

    请求体经各生成方法真实构建与校验后捕获，同时锁定场景键的序列化契约：组图
    恒写显式生效值，拆分仅支持配置的家族携带 options、未给 max_images 子字段时
    落回官方默认。
    """
    caps = get_model_capabilities(model_id)
    client = SeedreamClient(SeedreamConfig(api_key="k", max_retries=1, model_id=model_id))
    captured: dict[str, Any] = {}
    monkeypatch.setattr(client, "_call_api", _capture_call_api(captured))

    async def drive_and_project(**kwargs: Any) -> int:
        """清空上次捕获后驱动一个生成方法，返回本请求体的产图数投影。"""
        captured.clear()
        method = kwargs.pop("method")
        await getattr(client, method)(**kwargs)
        return _response_image_count(captured, model_id)

    # 其余场景恒单图：链上请求体不含场景键。
    assert await drive_and_project(method="text_to_image", prompt="对账") == (
        TextToImageInput.per_request_image_count(max_images=None, image_count=0, model_id=model_id)
    ), f"{model_id}: 文生图投影与 tools 层元数据漂移"
    assert await drive_and_project(
        method="image_to_image", prompt="对账", image=_REF_IMAGE
    ) == ImageToImageInput.per_request_image_count(
        max_images=None, image_count=0, model_id=model_id
    ), f"{model_id}: 图文生图投影与 tools 层元数据漂移"
    assert await drive_and_project(
        method="multi_image_fusion", prompt="对账", image=[_REF_IMAGE, _REF_IMAGE_2]
    ) == MultiImageFusionInput.per_request_image_count(
        max_images=None, image_count=0, model_id=model_id
    ), f"{model_id}: 多图融合投影与 tools 层元数据漂移"

    if caps.supports_layer_decomposition:
        assert await drive_and_project(
            method="image_to_image",
            prompt="对账",
            image=_REF_IMAGE,
            layer_decomposition=True,
        ) == LayerDecomposeInput.per_request_image_count(
            max_images=None, image_count=0, model_id=model_id
        ), f"{model_id}: 拆分缺省档投影与 tools 层元数据漂移"
        if caps.supports_layer_decomposition_options:
            assert await drive_and_project(
                method="image_to_image",
                prompt="对账",
                image=_REF_IMAGE,
                layer_decomposition=True,
                layer_decomposition_options={"max_images": 5},
            ) == LayerDecomposeInput.per_request_image_count(
                max_images=None, image_count=0, model_id=model_id, layer_max_images=5
            ), f"{model_id}: 拆分显式档投影与 tools 层元数据漂移"
            assert await drive_and_project(
                method="image_to_image",
                prompt="对账",
                image=_REF_IMAGE,
                layer_decomposition=True,
                layer_decomposition_options={"text_recognition": True},
            ) == LayerDecomposeInput.per_request_image_count(
                max_images=None, image_count=0, model_id=model_id
            ), f"{model_id}: 拆分配置缺 max_images 子字段时未落回官方默认档"

    if caps.supports_sequential_generation:
        for max_images, image_count in _SEQUENTIAL_PARITY_CASES:
            assert await drive_and_project(
                method="sequential_generation",
                prompt="对账",
                image=(
                    None
                    if image_count == 0
                    else [f"https://example.com/r{i}.png" for i in range(image_count)]
                ),
                sequential_image_generation_options=(
                    None if max_images is None else {"max_images": max_images}
                ),
            ) == SequentialGenerationInput.per_request_image_count(
                max_images=max_images, image_count=image_count, model_id=model_id
            ), (
                f"{model_id} max_images={max_images} image_count={image_count}: "
                "组图投影与 tools 层元数据漂移"
            )
