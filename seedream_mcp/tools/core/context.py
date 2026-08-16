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
from ...utils.core.errors import SeedreamValidationError
from ...utils.core.validators import (
    MAX_PARALLEL_REQUEST_COUNT,
    validate_generation_tools,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_size_for_model,
    validate_stream,
)
from ...utils.model.model_capabilities import get_max_reference_images
from .schemas import GenerationInputParams


@dataclass(frozen=True)
class GenerationExecutionContext:
    """生成类工具执行上下文，统一封装四类生成工具共享参数。

    frozen=True 保证构造后不可变，流水线各阶段读取同一份校验后的快照，避免中途误改。

    Attributes:
        prompt: 生成提示词。
        optimize_prompt_options: 经校验的提示词优化选项字典，未启用时为 None。
        size: 经模型能力校验的尺寸规格。
        watermark: 是否添加水印，未显式提供时取 config 默认值。
        response_format: 响应格式，url 或 b64_json。
        output_format: 输出图片格式，未指定时为 None。
        stream: 是否启用流式输出。
        tools: 经校验的模型工具配置列表，未启用时为 None。
        request_count: 请求次数，1 表示单次请求。
        parallelism: 并行度上限，未显式提供时取 request_count 与全局上限的较小值。
        enable_auto_save: 是否启用自动保存，未显式提供时取 config 默认值。
        save_path: 用户指定的保存目录，未指定时为 None。
        custom_name: 自定义文件名前缀，未指定时为 None。
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
    范围及组合约束。本函数仅做 schema 表达不了的校验与合成：尺寸、输出格式、流式、
    联网工具与参考图数量依赖 config.model_id 的能力校验；size、watermark、auto_save、
    parallelism 未显式提供时按 config 默认值合成。全量重校验由 client 各生成方法入口
    承担。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        config: 当前生效配置。

    Returns:
        校验后的统一执行上下文对象。

    Raises:
        SeedreamValidationError: 尺寸、输出格式、流式、联网工具、提示词优化或参考图
            数量校验未通过。
    """
    # 参考图数量上限依赖 model_id：5.0 Pro 为 10、其余为 14。schema 只能表达全家族
    # 默认上限，须在此按模型即时校验，与尺寸/流式等能力校验同层，避免进度已上报
    # “参数校验完成”后才在请求执行器内报错。单图输入为 str、组图未传参考图为 None，
    # 均不触发。
    images = getattr(params, "image", None)
    if isinstance(images, list):
        max_reference = get_max_reference_images(config.model_id)
        if len(images) > max_reference:
            raise SeedreamValidationError(
                f"image 数量不能超过 {max_reference}",
                field="image",
                value=images,
            )
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
