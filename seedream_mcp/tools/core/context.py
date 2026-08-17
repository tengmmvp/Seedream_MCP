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
    validate_background,
    validate_generation_tools,
    validate_layer_decomposition,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_size_for_model,
    validate_stream,
)
from ...utils.model.model_capabilities import get_max_reference_images
from ._helpers import prevalidate_save_path
from .schemas import GenerationInputParams


@dataclass(frozen=True)
class GenerationExecutionContext:
    """生成类工具执行上下文，统一封装四类生成工具共享参数。

    frozen=True 保证构造后不可变，流水线各阶段读取同一份校验后的快照，避免中途误改。

    Attributes:
        prompt: 生成提示词；图文生图的图层拆分场景可缺省，由模型自动识别拆分意图。
        optimize_prompt_options: 经校验的提示词优化选项字典，未启用时为 None。
        size: 经模型能力校验的尺寸规格。
        watermark: 是否添加水印，未显式提供时取 config 默认值。
        response_format: 响应格式，url 或 b64_json。
        output_format: 输出图片格式，未指定时为 None。
        stream: 是否启用流式输出。
        tools: 经校验的模型工具配置列表，未启用时为 None。
        layer_decomposition: 是否开启图层拆分，仅 5.0 Pro 图生图可用，未启用时为 False。
        background: 透明通道取值，transparent 或 opaque，未指定时为 None。
        max_images: 组图单次请求的生成数量上限；非组图工具为 None，未显式传入时
            为按参考图数量推导的生效值，回显供调用方获知实际约束。
        request_count: 请求次数，1 表示单次请求。
        parallelism: 并行度上限，未显式提供时取 request_count 与全局上限的较小值。
        enable_auto_save: 是否启用自动保存，未显式提供时取 config 默认值。
        save_path: 用户指定的保存目录，未指定时为 None。
        custom_name: 自定义文件名前缀，未指定时为 None。
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

    输入模型已保证 prompt 在必填工具非空（图文生图的图层拆分场景可缺省）、布尔与
    枚举字段合法、request_count 与 parallelism 的
    范围及组合约束。本函数仅做 schema 表达不了的校验与合成：尺寸、输出格式、流式、
    联网工具与参考图数量依赖 config.model_id 的能力校验；save_path 在此预检边界
    合法性，使非法路径在计费的生成请求执行前即被拒绝；size、watermark、auto_save、
    parallelism 未显式提供时按 config 默认值合成。全量重校验由 client 各生成方法入口
    承担。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        config: 当前生效配置。

    Returns:
        校验后的统一执行上下文对象。

    Raises:
        SeedreamValidationError: 尺寸、输出格式、流式、联网工具、提示词优化、图层
            拆分、透明通道、参考图数量或 save_path 边界校验未通过。
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
    layer_decomposition = validate_layer_decomposition(
        getattr(params, "layer_decomposition", None), config.model_id
    )
    # 图层拆分场景的官方默认尺寸为 auto（按输入图自适应），未显式提供 size 时不取
    # config.default_size；其余场景未显式提供时沿用全局默认。
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
    # save_path 越界在此预检而非留待自动保存阶段：生成请求已计费执行后才抛校验异常
    # 会被降级为软警告，图片仍落在默认目录之外取回困难；预检与 _resolve_base_dir
    # 共用同一判定入口，自动保存阶段照旧执行完整解析。
    prevalidate_save_path(config, params.save_path)

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
        # 组图工具的 max_images 经 schema 模型推导为生效值，未显式传入时按参考图
        # 数量推导；回显使调用方获知实际生效的生成数量上限，其余工具无此字段为 None。
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
