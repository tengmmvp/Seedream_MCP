"""生成类工具的执行上下文定义与构建。

承担 schema 之后的运行时归一化职责：将工具入参中的共享字段集中做尺寸、水印、响应格式、
并行度等校验，产出不可变的 ``GenerationExecutionContext`` 供流水线各阶段读取，避免在各
handler 内重复提取与校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import SeedreamConfig
from ...utils.errors import SeedreamValidationError
from ...utils.validation import (
    MAX_PARALLEL_REQUEST_COUNT,
    validate_common_generation_params,
    validate_parallel_generation_options,
)


@dataclass(frozen=True)
class GenerationExecutionContext:
    """生成类工具执行上下文，统一封装四类生成工具共享参数。

    frozen=True 保证构造后不可变，流水线各阶段读取同一份校验后的快照，避免中途误改。
    """

    prompt: str
    optimize_prompt_options: dict[str, Any] | None
    size: str
    watermark: bool
    response_format: str
    output_format: str | None
    stream: bool
    tools: list[dict[str, Any]] | None
    request_count: int
    parallelism: int
    enable_auto_save: bool
    save_path: str | None
    custom_name: str | None


def build_generation_context(
    arguments: dict[str, Any], config: SeedreamConfig
) -> GenerationExecutionContext:
    """从工具参数构建统一执行上下文，作为共享字段的集中校验点。

    未显式提供的字段按 config 默认值回退，再交由 utils.validation 的对应校验器做模型
    能力相关的规则检查，最终装配为不可变的执行上下文。

    Args:
        arguments: 工具原始参数字典。
        config: 当前生效配置。

    Returns:
        校验后的统一执行上下文对象。
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

    size_value = config.default_size if raw_size is None else raw_size
    watermark_for_validate = (
        config.default_watermark if watermark_value is None else watermark_value
    )

    validated = validate_common_generation_params(
        prompt=prompt,
        optimize_prompt_options=optimize_prompt_options,
        size=size_value,
        watermark=watermark_for_validate,
        response_format=response_format,
        output_format=output_format,
        stream=stream,
        tools=tools,
        model_id=config.model_id,
    )

    request_count, parallelism = validate_parallel_generation_options(
        request_count=request_count,
        parallelism=parallelism_value,
        stream=validated.stream,
        max_request_count=MAX_PARALLEL_REQUEST_COUNT,
    )

    if auto_save is None:
        enable_auto_save = config.auto_save_enabled
    elif isinstance(auto_save, bool):
        enable_auto_save = auto_save
    else:
        raise SeedreamValidationError("auto_save 必须是布尔值", field="auto_save", value=auto_save)

    return GenerationExecutionContext(
        prompt=validated.prompt,
        optimize_prompt_options=validated.optimize_prompt_options,
        size=validated.size,
        watermark=validated.watermark,
        response_format=validated.response_format,
        output_format=validated.output_format,
        stream=validated.stream,
        tools=validated.tools,
        request_count=request_count,
        parallelism=parallelism,
        enable_auto_save=enable_auto_save,
        save_path=save_path,
        custom_name=custom_name,
    )
