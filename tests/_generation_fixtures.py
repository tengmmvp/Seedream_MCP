"""GenerationExecutionContext 测试工厂。

test_results_output_guards、test_core_pipeline_guards 与 test_parallel_cancellation
共用同一份 15 字段默认构造，差异字段经 **overrides 覆盖，字段增删时只改本工厂，
不再三处逐份同步样板。
"""

from __future__ import annotations

from typing import Any

from seedream_mcp.tools.core.context import GenerationExecutionContext


def make_generation_context(**overrides: Any) -> GenerationExecutionContext:
    """构造生成执行上下文，未覆盖字段取与既有各测试一致的默认值。

    Args:
        **overrides: 覆盖默认值的字段，键须为 GenerationExecutionContext 的字段名。

    Returns:
        填充默认值与覆盖值后的生成执行上下文。
    """
    defaults: dict[str, Any] = {
        "prompt": "test",
        "optimize_prompt_options": None,
        "size": "2K",
        "watermark": False,
        "response_format": "url",
        "output_format": None,
        "stream": False,
        "tools": None,
        "layer_decomposition": False,
        "background": None,
        "max_images": None,
        "request_count": 1,
        "parallelism": 1,
        "enable_auto_save": True,
        "save_path": None,
        "custom_name": None,
    }
    defaults.update(overrides)
    return GenerationExecutionContext(**defaults)
