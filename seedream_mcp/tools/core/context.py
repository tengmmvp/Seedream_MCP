"""生成类工具的执行上下文定义与构建。

值域与组合约束已由 schemas.py 的输入模型保证，本模块只做 schema 表达不了的工作：与
config 默认值合成，及依赖运行时 config.model_id 的模型能力校验，产出不可变的
``GenerationExecutionContext`` 供流水线各阶段读取。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import SeedreamConfig
from ...utils.core.errors import SeedreamValidationError
from ...utils.core.validators import (
    MAX_PARALLEL_REQUEST_COUNT,
    validate_background,
    validate_generation_tools,
    validate_layer_decomposition,
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

    frozen=True 保证不可变，流水线各阶段读取同一份校验后的快照。

    Attributes:
        prompt: 生成提示词；图文生图的图层拆分场景可缺省。
        optimize_prompt_options: 提示词优化配置，未提供时为 None。
        size: 生效的生成尺寸；缺省时按 config 默认值合成，图层拆分场景缺省为 auto。
        watermark: 是否添加水印，缺省时取 config 默认值。
        response_format: 响应格式，url 或 b64_json。
        output_format: 输出图片格式，未提供时为 None。
        stream: 是否启用流式输出。
        tools: 模型工具配置，未提供时为 None。
        layer_decomposition: 是否开启图层拆分，仅 5.0 Pro 图生图可用。
        background: 透明通道取值，未指定时为 None。
        max_images: 组图单次请求的生成数量上限，未显式传入时为按参考图数量推导的
            生效值；非组图工具为 None。
        request_count: 同一提示并行发起的独立生成次数。
        parallelism: 并行度上限，缺省时取 request_count 与全局上限的较小值。
        enable_auto_save: 是否启用自动保存，缺省时取 config 默认值。
        save_path: 用户指定的保存目录，未提供时为 None。
        custom_name: 自定义文件名前缀，未提供时为 None。
    """

    prompt: str | None
    optimize_prompt_options: dict[str, Any] | None
    size: str
    watermark: bool
    response_format: str
    output_format: str | None
    stream: bool
    tools: list[dict[str, Any]] | None
    layer_decomposition: bool
    background: str | None
    max_images: int | None
    request_count: int
    parallelism: int
    enable_auto_save: bool
    save_path: str | None
    custom_name: str | None


def build_generation_context(
    params: GenerationInputParams, config: SeedreamConfig
) -> GenerationExecutionContext:
    """从类型化输入模型构建统一执行上下文。

    只做 schema 表达不了的校验与合成：依赖 config.model_id 的尺寸、输出格式、流式、
    联网工具与参考图数量能力校验；size、watermark、auto_save 缺省时按 config 默认值
    合成，parallelism 缺省取 request_count 与全局上限的较小值。save_path 边界预检由
    调用方流水线在本函数之后执行，全量重校验由 client 各生成方法入口承担。

    Raises:
        SeedreamValidationError: 尺寸、输出格式、流式、联网工具、提示词优化、图层
            拆分、透明通道或参考图数量校验未通过。
    """
    # 数量上限依赖 model_id（5.0 Pro 为 10、其余为 14），须与尺寸/流式等能力校验
    # 同层在此执行，避免进度上报「参数校验完成」后才在请求执行器内报错。
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
    layer_decomposition = validate_layer_decomposition(
        getattr(params, "layer_decomposition", None), config.model_id
    )
    # 图层拆分场景缺省尺寸为 auto（按输入图自适应），不取 config.default_size。
    if layer_decomposition and params.size is None:
        size = "auto"
    else:
        size = validate_size_for_model(
            params.size if params.size is not None else config.default_size,
            config.model_id,
            layer_decomposition=layer_decomposition,
        )
    watermark = config.default_watermark if params.watermark is None else params.watermark
    output_format = (
        validate_output_format(params.output_format.value, config.model_id)
        if params.output_format is not None
        else None
    )
    background = validate_background(
        getattr(params, "background", None), config.model_id, output_format=output_format
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
        layer_decomposition=layer_decomposition,
        background=background,
        # max_images 为 schema 推导后的生效值，回显供调用方获知实际生成上限。
        max_images=getattr(params, "max_images", None),
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
