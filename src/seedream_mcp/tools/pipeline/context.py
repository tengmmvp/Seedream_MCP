"""生成类工具的执行上下文定义与构建。

值域与组合约束已由 schemas.py 的输入模型保证，本模块只做 schema 表达不了的工作：与
config 默认值合成，及依赖运行时 config.model_id 的模型能力校验，产出不可变的
``GenerationExecutionContext`` 供流水线各阶段读取。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import SeedreamConfig
from ...utils.core.validators import (
    ensure_utf8_encodable,
    resolve_default_parallelism,
    resolve_effective_auto_save,
    resolve_effective_generation_defaults,
    validate_background,
    validate_generation_tools,
    validate_layer_decomposition,
    validate_layer_decomposition_options,
    validate_optimize_prompt_options,
    validate_output_format,
    validate_reference_image_count,
    validate_sequential_generation_support,
    validate_size_for_model,
    validate_stream,
)
from ..schemas import (
    _BackgroundInput,
    GenerationInputParams,
    LayerDecomposeInput,
    LayerDecompositionOptions,
    SequentialGenerationInput,
)


def _dump_layer_options(
    options: LayerDecompositionOptions | None,
) -> dict[str, Any] | None:
    """导出图层拆分配置中显式给定的字段，全缺省时为 None。

    显式 null 子键视同未指定的归一规则由 validators 单源承担。
    """
    if options is None:
        return None
    return options.model_dump(exclude_unset=True) or None


@dataclass(frozen=True)
class GenerationExecutionContext:
    """生成类工具执行上下文，统一封装五类生成工具共享参数。

    流水线各阶段读取同一份校验后的不可变快照。

    Attributes:
        prompt: 生成提示词；仅图层拆分工具可缺省。
        optimize_prompt_options: 提示词优化配置，未指定时为 None。
        layer_decomposition: 是否开启图层拆分，需当前模型支持。
        layer_decomposition_options: 图层拆分配置的归一字典，仅含显式给定的
            子字段，未指定时为 None。
        sequential_image_generation_options: 组图配置的官方请求形态字典，仅含
            max_images 生效值（显式声明优先，缺省按参考图数量推导）；非组图
            工具为 None。
        size: 生效的生成尺寸；缺省时按 config 默认值合成，图层拆分场景缺省为 auto。
        background: 透明通道取值，未指定时为 None；图层拆分工具不暴露该参数。
        watermark: 是否添加水印，缺省时取 config 默认值。
        tools: 模型工具配置，未指定时为 None。
        output_format: 输出图片格式，未指定时为 None。
        response_format: 响应格式，url 或 b64_json。
        stream: 是否启用流式输出。
        request_count: 同一提示并行发起的独立生成次数。
        parallelism: 并行度上限，缺省值已由 schema 校验器推导为生效值。
        enable_auto_save: 是否启用自动保存，缺省时取 config 默认值。
        save_path: 用户指定的保存目录，未指定时为 None。
        custom_name: 自定义文件名前缀，未指定时为 None。
    """

    prompt: str | None
    optimize_prompt_options: dict[str, Any] | None
    layer_decomposition: bool
    layer_decomposition_options: dict[str, Any] | None
    sequential_image_generation_options: dict[str, Any] | None
    size: str
    background: str | None
    watermark: bool
    tools: list[dict[str, Any]] | None
    output_format: str | None
    response_format: str
    stream: bool
    request_count: int
    parallelism: int
    enable_auto_save: bool
    save_path: str | None
    custom_name: str | None


def build_generation_context(
    params: GenerationInputParams, config: SeedreamConfig
) -> GenerationExecutionContext:
    """从类型化输入模型构建统一执行上下文。

    依赖 config.model_id 的能力校验，清单见 Raises；
    size、watermark、auto_save 缺省时按 config 默认值合成，parallelism 为 schema
    校验器掐尖后的生效值。save_path 边界预检由调用方流水线在本函数之后
    执行，全量重校验由 client 各生成方法入口承担。

    Raises:
        SeedreamValidationError: 提示词编码或优化、图层拆分、图层拆分配置、尺寸、
            透明通道、联网工具、输出格式、流式、组图能力或参考图数量校验未通过。
    """
    # 组图能力根因先于参考图数量上限报出，避免误导性的「数量超限」修复指引。
    if isinstance(params, SequentialGenerationInput):
        validate_sequential_generation_support(config.model_id)
    if params.prompt is not None:
        ensure_utf8_encodable(params.prompt, "提示词包含无法编码的字符", "prompt")
    # 数量上限依赖 model_id，由能力表统一判定；须与尺寸/流式等能力校验同层
    # 在此执行，避免进度上报「参数校验完成」后才在请求执行器内报错。
    # 参考图形态由输入模型的 reference_images 场景声明单点投影（文生图为 None，
    # 单图恒一张），未声明场景的新工具调用即 AttributeError 而非静默跳过数量校验。
    images: list[str] | None = params.reference_images()
    if images is not None:
        validate_reference_image_count(config.model_id, images)
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
    # 场景按输入类派发：拆分开关与拆分配置共用同一判定结果。
    layer_decomposition = validate_layer_decomposition(
        True if isinstance(params, LayerDecomposeInput) else None, config.model_id
    )
    size, watermark = resolve_effective_generation_defaults(
        size=params.size,
        watermark=params.watermark,
        default_size=config.default_size,
        default_watermark=config.default_watermark,
        layer_decomposition=layer_decomposition,
    )
    size = validate_size_for_model(size, config.model_id, layer_decomposition=layer_decomposition)
    output_format = (
        validate_output_format(params.output_format.value, config.model_id)
        if params.output_format is not None
        else None
    )
    # 可选字段按类声明派发取值，不做字段名嗅探。
    background = validate_background(
        params.background if isinstance(params, _BackgroundInput) else None,
        config.model_id,
        output_format=output_format,
        layer_decomposition=layer_decomposition,
        image_count=len(images) if images else 0,
    )
    layer_decomposition_options = validate_layer_decomposition_options(
        _dump_layer_options(
            params.layer_decomposition_options if isinstance(params, LayerDecomposeInput) else None
        ),
        config.model_id,
        layer_decomposition=layer_decomposition,
    )
    stream = validate_stream(params.stream, config.model_id)
    if isinstance(params, SequentialGenerationInput):
        sequential_image_generation_options = {"max_images": params.effective_max_images}
    else:
        sequential_image_generation_options = None

    return GenerationExecutionContext(
        prompt=params.prompt,
        optimize_prompt_options=optimize_prompt_options,
        layer_decomposition=layer_decomposition,
        layer_decomposition_options=layer_decomposition_options,
        sequential_image_generation_options=sequential_image_generation_options,
        size=size,
        background=background,
        watermark=watermark,
        tools=tools,
        output_format=output_format,
        response_format=params.response_format.value,
        stream=stream,
        request_count=params.request_count,
        # 缺省推导在 schema 校验器完成，此处兜底防未经校验器的输入模型直传 None。
        parallelism=(
            params.parallelism
            if params.parallelism is not None
            else resolve_default_parallelism(params.request_count)
        ),
        enable_auto_save=resolve_effective_auto_save(params.auto_save, config.auto_save_enabled),
        save_path=params.save_path,
        custom_name=params.custom_name,
    )
