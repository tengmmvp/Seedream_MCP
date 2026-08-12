"""生成类工具的执行上下文定义与构建。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ...config import SeedreamConfig
from ...utils.validation import (
    MAX_PARALLEL_REQUEST_COUNT,
    validate_generation_tools,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_parallel_generation_options,
    validate_response_format,
    validate_size_for_model,
    validate_stream,
    validate_watermark,
)


@dataclass(frozen=True)
class GenerationExecutionContext:
    """
    生成类工具执行上下文

    统一封装四类生成工具共享参数，避免在各 handler 中重复提取与校验。
    """

    prompt: str
    optimize_prompt_options: Optional[Dict[str, Any]]
    size: str
    watermark: bool
    response_format: str
    output_format: Optional[str]
    stream: bool
    tools: Optional[List[Dict[str, Any]]]
    request_count: int
    parallelism: int
    enable_auto_save: bool
    save_path: Optional[str]
    custom_name: Optional[str]


def build_generation_context(
    arguments: Dict[str, Any], config: SeedreamConfig
) -> GenerationExecutionContext:
    """
    从工具参数构建统一执行上下文

    Args:
        arguments: 工具原始参数字典。
        config: 当前生效配置。

    Returns:
        统一执行上下文对象。
    """
    prompt = arguments.get("prompt", "")
    optimize_prompt_options = arguments.get("optimize_prompt_options")
    raw_size = arguments.get("size") if "size" in arguments else None
    watermark_value = arguments.get("watermark")
    response_format = arguments.get("response_format", "url")
    output_format = arguments.get("output_format")
    stream = bool(arguments.get("stream", False))
    tools = arguments.get("tools")
    request_count = arguments.get("request_count", 1)
    parallelism_value = arguments.get("parallelism")
    auto_save = arguments.get("auto_save")
    save_path = arguments.get("save_path")
    custom_name = arguments.get("custom_name")

    validated_optimize_options = validate_optimize_prompt_options(
        optimize_prompt_options, config.model_id
    )

    size_value = config.default_size if raw_size is None else raw_size
    validated_size = validate_size_for_model(size_value, config.model_id)

    watermark = (
        validate_watermark(watermark_value)
        if watermark_value is not None
        else config.default_watermark
    )

    validated_response_format = validate_response_format(response_format)
    validated_output_format = validate_output_format(output_format, config.model_id)
    validated_stream = validate_stream(stream, config.model_id)
    validated_tools = validate_generation_tools(tools, config.model_id)

    request_count, parallelism = validate_parallel_generation_options(
        request_count=request_count,
        parallelism=parallelism_value,
        stream=validated_stream,
        max_request_count=MAX_PARALLEL_REQUEST_COUNT,
    )

    enable_auto_save = auto_save if auto_save is not None else config.auto_save_enabled

    return GenerationExecutionContext(
        prompt=prompt,
        optimize_prompt_options=validated_optimize_options,
        size=validated_size,
        watermark=watermark,
        response_format=validated_response_format,
        output_format=validated_output_format,
        stream=validated_stream,
        tools=validated_tools,
        request_count=request_count,
        parallelism=parallelism,
        enable_auto_save=enable_auto_save,
        save_path=save_path,
        custom_name=custom_name,
    )
