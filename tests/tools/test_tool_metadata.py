"""生成工具元数据的开始日志：图层拆分配置字段进入日志。"""

from __future__ import annotations

from typing import Any

import pytest

from _lib.generation_fixtures import make_generation_context
from seedream_mcp.tools.handlers._metadata import LAYER_DECOMPOSE


@pytest.mark.parametrize(
    ("layer_decomposition_options", "expected_line"),
    [
        (
            {"max_images": 50, "text_recognition": True},
            "图层拆分开始: prompt_len=2, max_images=50, text_recognition=True, size=2K, "
            "stream=False, request_count=1, parallelism=1",
        ),
        (
            None,
            "图层拆分开始: prompt_len=2, max_images=None, text_recognition=None, size=2K, "
            "stream=False, request_count=1, parallelism=1",
        ),
    ],
    ids=["explicit", "absent"],
)
def test_layer_decompose_start_log_carries_options(
    layer_decomposition_options: dict[str, Any] | None, expected_line: str
) -> None:
    """图层拆分开始日志携带显式给定的 max_images 与 text_recognition，未给定时记 None。"""
    context = make_generation_context(
        prompt="拆分", layer_decomposition_options=layer_decomposition_options
    )

    values = LAYER_DECOMPOSE.start_log_values_builder(context)

    assert LAYER_DECOMPOSE.start_log_message.format(*values) == expected_line
