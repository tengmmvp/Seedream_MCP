"""单请求产图数元数据的签名形态守护。

per_request_image_count 在 BaseGenerationInput 单点实现，产图数值与流水线真实
形态的逐值对账由 tests/webapp/test_webapp_wave_divisor_parity.py 的 parity 用例
承担；本文件只锁定签名形态：投影键集全为必传 keyword-only，漏接调用点以
TypeError 响亮失败而非静默按缺省档低估。
"""

from __future__ import annotations

import inspect

from seedream_mcp.tools.schemas import BaseGenerationInput


def test_per_request_image_count_signature_is_all_required_kwargs() -> None:
    """投影键集与必传形态锁定：带默认值或键集漂移即红。"""
    parameters = inspect.signature(BaseGenerationInput.per_request_image_count).parameters
    assert set(parameters) == {"layer_decomposition", "max_images", "image_count"}
    for name, parameter in parameters.items():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, f"{name} 须为 keyword-only"
        assert (
            parameter.default is inspect.Parameter.empty
        ), f"{name} 带默认值使漏接调用点静默按缺省档低估预算，须显式传参"
