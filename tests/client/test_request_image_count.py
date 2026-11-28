"""client 请求装配点单请求产图数与 tools 层投影的接线守护。

产图数在四个生成方法的装配点单点声明，
经 ``_call_api`` 的 ``per_request_image_count`` 关键字直传响应体读取上限投影。
图层拆分的产图数按显式值 → 官方默认 → 能力表满额推导，
推导规则与 tools 层共用 ``layer_decomposition_image_count`` 单点。
本文件驱动真实生成方法，
捕获的装配产图数与 tools 层 ``per_request_image_count`` 的投影键推导逐场景对账，
任一方法的装配接线漏传或错档即红。
组图输出另有一条经真实 ``_call_api`` 的接线用例，在 HTTP 发送层替换为替身，
装配产图数沿真实调用链送达读体上限投影。
替身形参集与生产方法的对账由 ``test_client_fake_keyword_only_parameters_match_production`` 承担。
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any

import pytest

from seedream_mcp.client import SeedreamClient, _api
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.schemas import (
    ImageToImageInput,
    LayerDecomposeInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.utils.model.capabilities import MODEL_ALIASES, get_model_capabilities

from _lib.client_fakes import _capture_call_api, _make_send_request_fake

# 组图对账代表档：显式值与按参考图推导档、有无参考图的组合。
_SEQUENTIAL_PARITY_CASES: tuple[tuple[int | None, int], ...] = (
    (None, 0),
    (None, 3),
    (4, 3),
    (1, 1),
)

_REF_IMAGE = "https://example.com/ref.png"
_REF_IMAGE_2 = "https://example.com/ref2.png"


@pytest.mark.parametrize(
    "model_id",
    # 5.0 与 5.0-lite 别名指向同一 Model ID，家族参数去重。
    tuple(dict.fromkeys(MODEL_ALIASES.values())),
)
async def test_assembled_request_image_count_matches_tools_projection(
    model_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """各生成方法装配的产图数与 tools 层投影键推导逐场景同值。"""
    caps = get_model_capabilities(model_id)
    client = SeedreamClient(SeedreamConfig(api_key="k", max_retries=1, model_id=model_id))
    counts: list[int] = []
    monkeypatch.setattr(client, "_call_api", _capture_call_api(counts=counts))

    async def drive(**kwargs: Any) -> int:
        """清空上次捕获后驱动一个生成方法，返回其装配产图数。"""
        counts.clear()
        method = kwargs.pop("method")
        await getattr(client, method)(**kwargs)
        return counts[0]

    assert await drive(method="text_to_image", prompt="对账") == (
        TextToImageInput.per_request_image_count(image_count=0, max_images=None, model_id=model_id)
    ), f"{model_id}: 文生图装配产图数与 tools 层投影漂移"
    assert await drive(
        method="image_to_image", prompt="对账", image=_REF_IMAGE
    ) == ImageToImageInput.per_request_image_count(
        image_count=0, max_images=None, model_id=model_id
    ), f"{model_id}: 图文生图装配产图数与 tools 层投影漂移"
    assert await drive(
        method="multi_image_fusion", prompt="对账", image=[_REF_IMAGE, _REF_IMAGE_2]
    ) == MultiImageFusionInput.per_request_image_count(
        image_count=0, max_images=None, model_id=model_id
    ), f"{model_id}: 多图融合装配产图数与 tools 层投影漂移"

    if caps.supports_layer_decomposition:
        assert await drive(
            method="image_to_image",
            prompt="对账",
            image=_REF_IMAGE,
            layer_decomposition=True,
        ) == LayerDecomposeInput.per_request_image_count(
            image_count=0, max_images=None, model_id=model_id
        ), f"{model_id}: 拆分缺省档装配产图数与 tools 层投影漂移"
        if caps.supports_layer_decomposition_options:
            assert await drive(
                method="image_to_image",
                prompt="对账",
                image=_REF_IMAGE,
                layer_decomposition=True,
                layer_decomposition_options={"max_images": 5},
            ) == LayerDecomposeInput.per_request_image_count(
                image_count=0, max_images=None, model_id=model_id, layer_max_images=5
            ), f"{model_id}: 拆分显式档装配产图数与 tools 层投影漂移"
            assert await drive(
                method="image_to_image",
                prompt="对账",
                image=_REF_IMAGE,
                layer_decomposition=True,
                layer_decomposition_options={"text_recognition": True},
            ) == LayerDecomposeInput.per_request_image_count(
                image_count=0, max_images=None, model_id=model_id
            ), f"{model_id}: 拆分配置缺 max_images 子字段时未落回官方默认档"

    if caps.supports_sequential_generation:
        for max_images, image_count in _SEQUENTIAL_PARITY_CASES:
            assert await drive(
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
                image_count=image_count, max_images=max_images, model_id=model_id
            ), (
                f"{model_id} max_images={max_images} image_count={image_count}: "
                "组图装配产图数与 tools 层投影漂移"
            )


def _keyword_only_parameters(func: Callable[..., Any]) -> dict[str, inspect.Parameter]:
    """取可调用对象的仅关键字形参表。"""
    return {
        name: parameter
        for name, parameter in inspect.signature(func).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }


@pytest.mark.parametrize(
    ("fake", "production"),
    [
        (_capture_call_api(), SeedreamClient._call_api),
        (_make_send_request_fake()[0], SeedreamClient._send_request),
    ],
    ids=["_call_api", "_send_request"],
)
def test_client_fake_keyword_only_parameters_match_production(
    fake: Callable[..., Any], production: Callable[..., Any]
) -> None:
    """替身的仅关键字形参集与生产方法相等且全部必填，生产新增必填关键字时替身须同步。"""
    fake_parameters = _keyword_only_parameters(fake)

    assert set(fake_parameters) == set(_keyword_only_parameters(production))
    defaulted = [
        name
        for name, parameter in fake_parameters.items()
        if parameter.default is not inspect.Parameter.empty
    ]
    assert not defaulted, f"替身形参带默认值会吞掉调用点漏传: {defaulted}"


# 接线对账驱动的生成方法集，与 client/_api.py 的生产 _call_api 调用者对账。
_PARITY_DRIVEN_METHODS = frozenset(
    {"text_to_image", "image_to_image", "multi_image_fusion", "sequential_generation"}
)

# 类方法定义行以 4 空格缩进匹配，更深缩进的嵌套闭包不重置当前方法归属。
_METHOD_DEF_PATTERN = re.compile(r"^    (?:async )?def (\w+)\(")


def _production_call_api_callers() -> set[str]:
    """client/_api.py 内直接调用 self._call_api 的方法名集合。"""
    callers: set[str] = set()
    current: str | None = None
    for line in inspect.getsource(_api).splitlines():
        method = _METHOD_DEF_PATTERN.match(line)
        if method:
            current = method.group(1)
        elif current and "self._call_api(" in line:
            callers.add(current)
    return callers


def test_production_call_api_callers_covered_by_parity() -> None:
    """生产 _call_api 调用者全经接线对账驱动：新增生成方法漏登记即红。"""
    assert _production_call_api_callers() == set(_PARITY_DRIVEN_METHODS), (
        "client/_api.py 存在未登记进接线对账的 _call_api 调用方法，"
        "请在 test_assembled_request_image_count_matches_tools_projection 补驱动"
    )


def test_parity_registered_methods_are_actually_driven() -> None:
    """_PARITY_DRIVEN_METHODS 与对账测试 drive(method=...) 字面量集一致：登记未驱动即红。"""
    driven = set(
        re.findall(
            r'method="(\w+)"',
            inspect.getsource(test_assembled_request_image_count_matches_tools_projection),
        )
    )
    assert driven == set(
        _PARITY_DRIVEN_METHODS
    ), "_PARITY_DRIVEN_METHODS 存在对账测试从未驱动的方法，请补 drive 调用或收回登记"


async def test_sequential_generation_wires_image_count_through_real_call_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组图输出经真实 _call_api 把装配产图数送达 HTTP 发送层，漏传关键字或投影点硬编码单图即失败。

    b64_json 的读体上限随产图数线性放大，
    发送层收到的 byte_limit 等于 max_images 张的投影且不等于单图投影，
    显式的不等断言在投影落回单图时转红，防止用例静默失去判别力。
    """
    max_images = 4
    config = SeedreamConfig(api_key="k", max_retries=1, model_id="doubao-seedream-5.0")
    limits: list[int] = []
    fake_send, send_calls = _make_send_request_fake(limits=limits)
    monkeypatch.setattr(SeedreamClient, "_send_request", fake_send)

    async with SeedreamClient(config) as client:
        response = await client.sequential_generation(
            prompt="接线",
            sequential_image_generation_options={"max_images": max_images},
            response_format="b64_json",
        )

    assert send_calls() == 1
    assert response["success"] is True
    assert limits == [
        config.effective_response_body_limit(response_format="b64_json", max_image_count=max_images)
    ]
    assert limits[0] != config.effective_response_body_limit(
        response_format="b64_json", max_image_count=1
    )
