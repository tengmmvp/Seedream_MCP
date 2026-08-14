"""生成类工具的执行上下文定义与构建。

承担 schema 之后的运行时归一化职责。值域与组合约束已由 schemas.py 的输入模型保证，
本模块仅执行 schema 表达不了的两类工作：与 config 默认值的合成，以及依赖运行时
config.model_id 的模型能力校验，产出不可变的 ``GenerationExecutionContext`` 供流水线
各阶段读取，避免各 handler 内重复提取与校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import SeedreamConfig
from ...utils.core.validators import (
    MAX_PARALLEL_REQUEST_COUNT,
    validate_generation_tools,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_size_for_model,
    validate_stream,
)
from .schemas import GenerationInputParams


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
    params: GenerationInputParams, config: SeedreamConfig
) -> GenerationExecutionContext:
    """从类型化输入模型构建统一执行上下文。

    输入模型已保证 prompt 非空、布尔与枚举字段合法、request_count 与 parallelism 的
    范围及组合约束。本函数仅做 schema 表达不了的校验与合成：尺寸、输出格式、流式与
    联网工具依赖 config.model_id 的能力校验；size、watermark、auto_save、parallelism
    未显式提供时按 config 默认值合成。全量重校验由 client 各生成方法入口承担。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        config: 当前生效配置。

    Returns:
        校验后的统一执行上下文对象。
    """
    optimize_prompt_options = (
        validate_optimize_prompt_options(
            params.optimize_prompt_options.model_dump(), config.model_id
        )
        if params.optimize_prompt_options is not None
        else None
    )
    tools = (
        validate_generation_tools([tool.model_dump() for tool in params.tools], config.model_id)
        if params.tools
        else None
    )
    size = validate_size_for_model(
        params.size if params.size is not None else config.default_size,
        config.model_id,
    )
    watermark = config.default_watermark if params.watermark is None else params.watermark
    output_format = (
        validate_output_format(params.output_format.value, config.model_id)
        if params.output_format is not None
        else None
    )
    stream = validate_stream(params.stream, config.model_id)

    return GenerationExecutionContext(
        prompt=params.prompt,
        optimize_prompt_options=optimize_prompt_options,
        size=size,
        watermark=watermark,
        response_format=params.response_format.value,
        output_format=output_format,
        stream=stream,
        tools=tools,
        request_count=params.request_count,
        parallelism=(
            params.parallelism
            if params.parallelism is not None
            else min(params.request_count, MAX_PARALLEL_REQUEST_COUNT)
        ),
        enable_auto_save=(
            config.auto_save_enabled if params.auto_save is None else params.auto_save
        ),
        save_path=params.save_path,
        custom_name=params.custom_name,
    )
