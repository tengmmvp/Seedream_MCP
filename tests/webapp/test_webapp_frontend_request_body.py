"""Web 操作台前端请求体装配的行为守护：经 node 导入真实 generate.js 执行 buildRequestBody。

结构锚点守护（test_webapp_static_assets）锁源码形态，本文件以最小 document 替身
运行真实装配逻辑断言返回体：拆分配置只装显式输入，输出格式读值与展示折叠同判定。
场景经 tests/_lib/frontend_harness.mjs 交给 node 执行，本机无 node 时整段跳过，
CI 运行器的 node 可用性由 test_ci_workflow_guards 守护。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from seedream_mcp.utils.model.capabilities import model_payloads
from seedream_mcp.webapp import constants as web_constants

_HARNESS = Path(__file__).resolve().parents[1] / "_lib" / "frontend_harness.mjs"
_NODE_TIMEOUT_SECONDS = 60

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="前端行为测试经 node 执行 tests/_lib/frontend_harness.mjs，本机未安装 node",
)

# 前端工具标识，与 api.js 的 TOOL_* 常量一一对应。
_TOOL_TEXT_TO_IMAGE = "text-to-image"
_TOOL_LAYER_DECOMPOSE = "layer-decompose"

# 能力表真实载荷：config-info 的 models 字段同源，当前模型按能力开关从中选取。
_MODELS = model_payloads()


def _model_id_where(**capabilities: Any) -> str:
    """取模型清单中给定能力字段逐项相等的首个模型 ID。"""
    return next(
        cast("str", entry["model_id"])
        for entry in _MODELS
        if all(entry[key] == value for key, value in capabilities.items())
    )


def _config_info(model_id: str) -> dict[str, Any]:
    """最小 config-info 载荷：model_id 不在模型清单内即 Endpoint ID 部署形态。"""
    return {"model_id": model_id, "models": _MODELS}


def _build_request_body(
    tool: str, config_info: dict[str, Any], controls: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """把场景交给 harness 执行 buildRequestBody，返回请求体。"""
    scenario = {
        "moduleUrl": (web_constants.STATIC_DIR / "js" / "generate.js").resolve().as_uri(),
        "tool": tool,
        "configInfo": config_info,
        "controls": controls or {},
    }
    completed = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(scenario),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_NODE_TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr
    result: dict[str, Any] = json.loads(completed.stdout)
    body: dict[str, Any] = result["body"]
    return body


def test_endpoint_model_layer_decompose_untouched_controls_omit_options() -> None:
    """Endpoint ID 模型下图层拆分未触碰任何拆分控件：返回体不含 layer_decomposition_options。

    未勾选与留空是非动作，不得构造显式默认值进请求体。
    键缺省由 API 按官方默认取值（text_recognition false、max_images 30），
    与 MCP 路径的上游形态一致。
    未知模型（Endpoint ID）经 layerOptionsAllowed 放行，拆分分支确已进入。
    """
    body = _build_request_body(_TOOL_LAYER_DECOMPOSE, _config_info("ep-20261128-abc"))

    assert "layer_decomposition_options" not in body


def test_endpoint_model_layer_decompose_ocr_checked_sends_true() -> None:
    """同场景勾选富文本 OCR：仅 text_recognition 以显式 true 进请求体。"""
    body = _build_request_body(
        _TOOL_LAYER_DECOMPOSE,
        _config_info("ep-20261128-abc"),
        {"text-recognition": {"checked": True}},
    )

    assert body["layer_decomposition_options"] == {"text_recognition": True}


def test_layer_options_attach_only_explicit_inputs() -> None:
    """5.5 家族填拆分数量 40：仅 max_images 进请求体，再勾选 OCR 则两键俱在。"""
    model_id = _model_id_where(supports_layer_decomposition_options=True)
    config_info = _config_info(model_id)

    body = _build_request_body(
        _TOOL_LAYER_DECOMPOSE, config_info, {"layer-max-images": {"value": "40"}}
    )
    assert body["layer_decomposition_options"] == {"max_images": 40}

    body = _build_request_body(
        _TOOL_LAYER_DECOMPOSE,
        config_info,
        {"layer-max-images": {"value": "40"}, "text-recognition": {"checked": True}},
    )
    assert body["layer_decomposition_options"] == {"text_recognition": True, "max_images": 40}


def test_output_format_omitted_when_model_lacks_support() -> None:
    """当前模型 supports_output_format=false 且下拉残留 png：不含 output_format。

    折叠区保留旧值，读值须经 outputFormatAllowed 与展示折叠同判定，
    不携带用户不可见也无法清除的旧值，免遭服务端能力门控拒绝。
    """
    model_id = _model_id_where(supports_output_format=False)

    body = _build_request_body(
        _TOOL_TEXT_TO_IMAGE, _config_info(model_id), {"output-format": {"value": "png"}}
    )

    assert "output_format" not in body


def test_output_format_sent_when_model_supports() -> None:
    """当前模型支持输出格式而不支持拆分配置：读值经 outputFormatAllowed 放行，
    下拉取值 png 原样进请求体。
    """
    model_id = _model_id_where(
        supports_output_format=True, supports_layer_decomposition_options=False
    )

    body = _build_request_body(
        _TOOL_TEXT_TO_IMAGE, _config_info(model_id), {"output-format": {"value": "png"}}
    )

    assert body["output_format"] == "png"


def test_output_format_sent_when_background_field_unavailable() -> None:
    """当前模型支持输出格式而无透明通道取值：output_format 照常进请求体，不随背景字段折叠。

    读值判定串用 backgroundFieldAvailable 会使该模型的输出格式被静默丢弃。
    """
    model_id = _model_id_where(supports_output_format=True, allowed_background_modes=[])

    body = _build_request_body(
        _TOOL_TEXT_TO_IMAGE, _config_info(model_id), {"output-format": {"value": "png"}}
    )

    assert body["output_format"] == "png"


def test_endpoint_model_output_format_sent() -> None:
    """Endpoint ID 模型下拉取值 png：未知模型经 outputFormatAllowed 放行，output_format 为 png。"""
    body = _build_request_body(
        _TOOL_TEXT_TO_IMAGE, _config_info("ep-20261128-abc"), {"output-format": {"value": "png"}}
    )

    assert body["output_format"] == "png"
