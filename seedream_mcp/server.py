"""Seedream MCP 服务器主模块。

注册文生图、图生图、多图融合、组图生成、图片浏览五种 MCP 工具，以及风格预设
Prompt 与工作区、服务器信息、模型信息三个资源。负责配置注入、cli_main 入口与
传输分派。
MCPServer 实例与共享资源生命周期管理由 resources 模块承担，本模块导入 mcp 完成注册
并重导出 resources 符号，保持 server 既有导入 surface 与 tests 访问路径不变。CLI
参数解析由 cli 模块承担，streamable-http 中间件与传输配置由 transport 模块承担，
二者经本模块重导出。

outputSchema 声明契约：五个 @mcp.tool 工具函数的返回类型注解为 SDK 官方惯用形
``Annotated[CallToolResult, GenerationStructuredOutput / BrowseImagesStructuredOutput]``。
mcp 2.0 的 FuncMetadata 对返回 CallToolResult 的工具取注解元数据首项的 pydantic
model 生成 outputSchema，运行时返回的 CallToolResult 原样透传，同时携带面向模型的
文本与 structuredContent，且经该 model 校验 structuredContent 后才送达客户端。注解与
运行时返回类型一致，函数体无需 type: ignore。声明与运行时两侧由 test_output_schema
与 test_output_schema_consistency 守护不漂移。

inputSchema 平铺契约：五个工具函数以逐字段平铺参数声明（prompt 居首），而非单一
params 嵌套模型。MCPServer 的 FuncMetadata 不支持单参数 BaseModel 自动展开，
嵌套声明会把 inputSchema 收敛为一个 params 对象字段，客户端以平铺键名调用会被拒绝。
平铺字段的名称、类型、默认值、约束与描述镜像自 tools.core.schemas 的对应输入模型，
字段规则的单一来源仍是该模块；模型层的 str_strip_whitespace 与各字段校验器的非空
语义经签名层 ``_NON_BLANK_PATTERN`` 等价镜像，纯空白输入在协议层即被拒绝而非进入
工具体后才失败。函数体内过滤值为 None 的可选字段后组装输入模型并
委托既有 run_* 处理器，跨字段校验在组装时照常触发。test_tool_parameter_order 以
inputSchema 与模型 schema 的等价性断言锁定两侧不漂移。
"""

from __future__ import annotations

# 标准库导入
import json
import sys
from typing import Annotated, Any

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

# 本地模块导入
from .cli import _build_arg_parser, _build_config_from_args, _build_run_options
from .config import (
    LIFESPAN_KEY_CONFIG,
    MODEL_ALIASES,
    SeedreamConfig,
    get_active_config,
    set_active_config,
)
from .tools import (
    BrowseImagesInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
    run_browse_images,
    run_image_to_image,
    run_multi_image_fusion,
    run_sequential_generation,
    run_text_to_image,
)
from .tools.core.common import get_lifespan_resource
from .tools.core.schemas import (
    BackgroundMode,
    GenerationTool,
    OptimizePromptOptions,
    OutputFormat,
    PROMPT_MAX_LENGTH,
    PROMPT_MIN_LENGTH,
    ResponseFormat,
)
from .tools.core.outputs import (
    BrowseImagesStructuredOutput,
    GenerationStructuredOutput,
)
from .transport import (
    _LOOPBACK_HOSTS,
    _resolve_http_auth_token,
    _run_streamable_http,
    _warn_remote_exposure,
)
from .utils.core.errors import SeedreamConfigError, format_error_for_user
from .utils.core.logs import get_logger, setup_logging
from .utils.core.validators import (
    MAX_PARALLEL_REQUEST_COUNT,
    MAX_SEQUENTIAL_TOTAL_IMAGES,
)
from .utils.io.io_path import (
    get_workspace_roots,
    is_boundary_from_session_roots,
    workspace_roots_scope,
)
from .utils.model.model_capabilities import SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES

# resources 符号重导出：mcp、SERVER_NAME、SERVER_VERSION、_sync_cleanup 为本模块直接
# 使用，其余供 tests 与既有 import 路径经 server 模块访问。
from .resources import (  # noqa: F401
    SERVER_NAME,
    SERVER_VERSION,
    _cleanup_shared_resources,
    _reset_lifespan_state,
    _sync_cleanup,
    app_lifespan,
    mcp,
)

# ASGI 中间件与请求体上限常量重导出，供 tests 经 server 模块访问。
from .transport import (  # noqa: F401
    _BearerTokenAuthMiddleware,
    _HealthCheckMiddleware,
    _LimitRequestBodyMiddleware,
)

# ==================== 工具注解常量 ====================

# 生成类工具的能力标注：会生成文件，非只读；不破坏既有数据；每次生成结果可能不同，
# 非幂等；需联网调用 API，属开放世界操作。
GENERATION_TOOL_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

# 浏览类工具的能力标注：仅读取文件列表，只读且幂等；不破坏既有数据；仅访问本地
# 文件系统，非开放世界操作。
BROWSE_TOOL_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

logger = get_logger(__name__)

# 非空语义镜像：输入模型经 str_strip_whitespace 先剥离首尾空白再做长度与校验器判定，
# 纯空白字符串在模型层被拒；平铺签名的参数模型不含 strip 配置，等价约束以 pattern
# 表达——含至少一个非空白字符。应用于声明了非空语义的字段：prompt 的 min_length=1、
# image 与 save_path/custom_name/directory 的非空校验器。带内边距的合法值不受影响，
# strip 仍由函数体内组装输入模型时完成。
_NON_BLANK_PATTERN = r"\S"


def _config_from_context(ctx: Context[Any, Any]) -> SeedreamConfig:
    """从 MCP 请求上下文获取 lifespan 注入的配置，无法获取时回退全局配置并记录告警。

    工具与资源经 ctx.request_context.lifespan_context 取配置，避免直接依赖模块级全局
    状态，消除热重载窗口内活动配置与请求实际使用的配置不一致。复用 parallel 的
    get_lifespan_resource 统一资源探测实现，lifespan 键与 parallel 一致取自 config。
    """
    config = get_lifespan_resource(ctx, LIFESPAN_KEY_CONFIG, SeedreamConfig)
    if config is not None:
        return config
    logger.warning("lifespan 上下文未注入配置，回退全局活动配置")
    return get_active_config()


def _filter_unset_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    """过滤值为 None 的函数签名默认值，仅保留显式提供的字段。

    None 过滤作用于平铺函数签名的默认值：签名字段以 None 统一表示未提供，剔除后
    组装输入模型使 model_fields_set 仅含显式提供的字段。模型中带非 None 默认值的
    字段不受同等过滤——生成工具的 response_format/stream/request_count 与浏览工具
    的 recursive/max_depth/limit/offset/show_details 经签名非 None 默认值取值，恒进入
    model_fields_set；组图 max_images 虽在模型中以总上限为默认值，但其签名为 None
    默认，未提供时被过滤、不进入 fields_set，随后按参考图数量自动推导。当前唯一
    依赖 fields_set 区分的就是 max_images 推导，不受恒进入字段影响；未来若有新逻辑
    依赖 model_fields_set 判定显式传入，须知上述字段不参与该区分。

    Args:
        kwargs: 平铺参数名到函数入参值的映射，必填字段由调用方保证非 None。

    Returns:
        剔除 None 值后的参数字典，用作输入模型构造的关键字参数。
    """
    return {key: value for key, value in kwargs.items() if value is not None}


# ==================== MCP 工具函数定义 ====================


@mcp.tool(
    name="text_to_image",
    title="Seedream 文生图",
    annotations=GENERATION_TOOL_ANNOTATIONS,
)
async def text_to_image(
    prompt: str = Field(
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        pattern=_NON_BLANK_PATTERN,
        description=(
            "用于生成图片的提示词，建议不超过300个汉字或600个英文单词。"
            "例如：一只戴墨镜的猫坐在月球上，写实风格。"
        ),
    ),
    optimize_prompt_options: OptimizePromptOptions | None = Field(
        default=None,
        description="提示词优化配置，仅支持 standard 或 fast。",
    ),
    size: str | None = Field(
        default=None,
        description="生成图片尺寸，可选 1K/1.5K/2K/3K/4K 或 <宽>x<高> 像素值；未提供时使用全局默认值。例如：2K 或 1920x1080。",
    ),
    watermark: bool | None = Field(
        default=None,
        description="是否添加水印；未提供时沿用全局默认值（默认不添加）。",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.URL,
        description="响应格式，url 返回可下载链接，b64_json 返回 base64 数据。",
    ),
    output_format: OutputFormat | None = Field(
        default=None,
        description="输出图片格式，仅 5.0 系列（Pro/标准/Lite）支持 jpeg 或 png。",
    ),
    stream: bool = Field(
        default=False,
        description="是否启用流式输出；开启后将以事件流返回生成进度（5.0 Pro 不支持）。",
    ),
    tools: list[GenerationTool] | None = Field(
        default=None,
        description="模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持联网搜索（web_search）。",
    ),
    request_count: int = Field(
        default=1,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="同一提示并行发起的独立生成次数，每次各产出一张图；适合一次获取多张候选图，与组图工具的 max_images 无关。",
    ),
    parallelism: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="并行度上限（1-10）；未提供时自动取 min(request_count, 10)，一般无需手动指定。",
    ),
    auto_save: bool | None = Field(
        default=None,
        description="是否自动保存到本地；未提供时遵循全局配置（默认开启）。",
    ),
    save_path: str | None = Field(
        default=None,
        max_length=1024,
        pattern=_NON_BLANK_PATTERN,
        description="自定义保存目录，未提供时使用自动保存配置的默认路径。",
    ),
    custom_name: str | None = Field(
        default=None,
        max_length=255,
        pattern=_NON_BLANK_PATTERN,
        description="自定义文件名前缀，未提供时根据提示词自动生成。",
    ),
    ctx: Context[Any, Any] = None,  # type: ignore[assignment]
) -> Annotated[CallToolResult, GenerationStructuredOutput]:
    """文生图：根据文字指令生成单张图片。

    适用：从零开始按文字描述创建图片。示例：生成“赛博朋克风格的城市夜景”。
    不适用：需要基于已有图片修改时改用 image_to_image；需要一次生成多张
    风格一致的图片时改用 sequential_generation。
    """
    config = _config_from_context(ctx)
    return await run_text_to_image(
        TextToImageInput(
            **_filter_unset_params(
                {
                    "prompt": prompt,
                    "optimize_prompt_options": optimize_prompt_options,
                    "size": size,
                    "watermark": watermark,
                    "response_format": response_format,
                    "output_format": output_format,
                    "stream": stream,
                    "tools": tools,
                    "request_count": request_count,
                    "parallelism": parallelism,
                    "auto_save": auto_save,
                    "save_path": save_path,
                    "custom_name": custom_name,
                }
            )
        ),
        config=config,
        ctx=ctx,
    )


@mcp.tool(
    name="image_to_image",
    title="Seedream 图文生图",
    annotations=GENERATION_TOOL_ANNOTATIONS,
)
async def image_to_image(
    prompt: str | None = Field(
        default=None,
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        pattern=_NON_BLANK_PATTERN,
        description=(
            "图片修改或风格转换的指令，建议不超过300个汉字或600个英文单词；"
            "图层拆分场景可缺省，由模型自动识别拆分意图。"
            "例如：把背景换成雪山、将照片转为水彩画风格。"
        ),
    ),
    optimize_prompt_options: OptimizePromptOptions | None = Field(
        default=None,
        description="提示词优化配置，仅支持 standard 或 fast。",
    ),
    image: str = Field(
        pattern=_NON_BLANK_PATTERN,
        description=(
            "参考图片，支持图像 URL、本地文件路径或 Base64 图片数据。"
            "例如：https://example.com/ref.png 或 ./.seedream/images/portrait.jpg。"
        ),
    ),
    layer_decomposition: bool | None = Field(
        default=None,
        description=(
            "是否开启图层拆分，仅 5.0 Pro 支持；开启后将单张输入图拆解为 1 张底图"
            "与最多 16 个带透明通道的 PNG 图层，可配合 prompt 指定拆分意图。"
        ),
    ),
    background: BackgroundMode | None = Field(
        default=None,
        description=(
            "图片透明通道，仅 5.0 Pro 图生图支持；transparent 生成透明背景图"
            "（需输入单张带透明通道的图片），opaque 生成常规实体背景图。"
        ),
    ),
    size: str | None = Field(
        default=None,
        description=(
            "生成图片尺寸，可选 1K/1.5K/2K/3K/4K 或 <宽>x<高> 像素值；"
            "图层拆分场景仅支持档位与 auto，未提供时默认 auto；"
            "其余场景未提供时使用全局默认值。例如：2K 或 1920x1080。"
        ),
    ),
    watermark: bool | None = Field(
        default=None,
        description="是否添加水印；未提供时沿用全局默认值（默认不添加）。",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.URL,
        description="响应格式，url 返回可下载链接，b64_json 返回 base64 数据。",
    ),
    output_format: OutputFormat | None = Field(
        default=None,
        description="输出图片格式，仅 5.0 系列（Pro/标准/Lite）支持 jpeg 或 png。",
    ),
    stream: bool = Field(
        default=False,
        description="是否启用流式输出；开启后将以事件流返回生成进度（5.0 Pro 不支持）。",
    ),
    tools: list[GenerationTool] | None = Field(
        default=None,
        description="模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持联网搜索（web_search）。",
    ),
    request_count: int = Field(
        default=1,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="同一提示并行发起的独立生成次数，每次各产出一张图；适合一次获取多张候选图，与组图工具的 max_images 无关。",
    ),
    parallelism: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="并行度上限（1-10）；未提供时自动取 min(request_count, 10)，一般无需手动指定。",
    ),
    auto_save: bool | None = Field(
        default=None,
        description="是否自动保存到本地；未提供时遵循全局配置（默认开启）。",
    ),
    save_path: str | None = Field(
        default=None,
        max_length=1024,
        pattern=_NON_BLANK_PATTERN,
        description="自定义保存目录，未提供时使用自动保存配置的默认路径。",
    ),
    custom_name: str | None = Field(
        default=None,
        max_length=255,
        pattern=_NON_BLANK_PATTERN,
        description="自定义文件名前缀，未提供时根据提示词自动生成。",
    ),
    ctx: Context[Any, Any] = None,  # type: ignore[assignment]
) -> Annotated[CallToolResult, GenerationStructuredOutput]:
    """图文生图：基于已有图片进行编辑。

    适用：在保留输入图片主体或构图的前提下做元素增删、风格转化、材质替换、色调
    迁移、改变背景或视角尺寸等。示例：“把人物背景换成海滩”。
    不适用：纯文字生图改用 text_to_image；融合多张图片特征改用
    multi_image_fusion。
    """
    config = _config_from_context(ctx)
    return await run_image_to_image(
        ImageToImageInput(
            **_filter_unset_params(
                {
                    "prompt": prompt,
                    "optimize_prompt_options": optimize_prompt_options,
                    "image": image,
                    "layer_decomposition": layer_decomposition,
                    "background": background,
                    "size": size,
                    "watermark": watermark,
                    "response_format": response_format,
                    "output_format": output_format,
                    "stream": stream,
                    "tools": tools,
                    "request_count": request_count,
                    "parallelism": parallelism,
                    "auto_save": auto_save,
                    "save_path": save_path,
                    "custom_name": custom_name,
                }
            )
        ),
        config=config,
        ctx=ctx,
    )


@mcp.tool(
    name="multi_image_fusion",
    title="Seedream 多图融合",
    annotations=GENERATION_TOOL_ANNOTATIONS,
)
async def multi_image_fusion(
    prompt: str = Field(
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        pattern=_NON_BLANK_PATTERN,
        description=(
            "融合目标或风格描述，建议不超过300个汉字或600个英文单词。"
            "请使用“图X”指定图像（如：将图1的服装换为图2的服装）。"
        ),
    ),
    optimize_prompt_options: OptimizePromptOptions | None = Field(
        default=None,
        description="提示词优化配置，仅支持 standard 或 fast。",
    ),
    image: list[Annotated[str, Field(pattern=_NON_BLANK_PATTERN)]] = Field(
        min_length=2,
        max_length=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
        description=(
            f"输入图像，数量 2-{SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES} 张（5.0 Pro 最多 10 张），"
            f"每张支持图像 URL、本地文件路径或 Base64 图片数据。"
            '例如：["https://example.com/a.png", "./.seedream/images/b.jpg"]。'
        ),
    ),
    size: str | None = Field(
        default=None,
        description="生成图片尺寸，可选 1K/1.5K/2K/3K/4K 或 <宽>x<高> 像素值；未提供时使用全局默认值。例如：2K 或 1920x1080。",
    ),
    watermark: bool | None = Field(
        default=None,
        description="是否添加水印；未提供时沿用全局默认值（默认不添加）。",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.URL,
        description="响应格式，url 返回可下载链接，b64_json 返回 base64 数据。",
    ),
    output_format: OutputFormat | None = Field(
        default=None,
        description="输出图片格式，仅 5.0 系列（Pro/标准/Lite）支持 jpeg 或 png。",
    ),
    stream: bool = Field(
        default=False,
        description="是否启用流式输出；开启后将以事件流返回生成进度（5.0 Pro 不支持）。",
    ),
    tools: list[GenerationTool] | None = Field(
        default=None,
        description="模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持联网搜索（web_search）。",
    ),
    request_count: int = Field(
        default=1,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="同一提示并行发起的独立生成次数，每次各产出一张图；适合一次获取多张候选图，与组图工具的 max_images 无关。",
    ),
    parallelism: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="并行度上限（1-10）；未提供时自动取 min(request_count, 10)，一般无需手动指定。",
    ),
    auto_save: bool | None = Field(
        default=None,
        description="是否自动保存到本地；未提供时遵循全局配置（默认开启）。",
    ),
    save_path: str | None = Field(
        default=None,
        max_length=1024,
        pattern=_NON_BLANK_PATTERN,
        description="自定义保存目录，未提供时使用自动保存配置的默认路径。",
    ),
    custom_name: str | None = Field(
        default=None,
        max_length=255,
        pattern=_NON_BLANK_PATTERN,
        description="自定义文件名前缀，未提供时根据提示词自动生成。",
    ),
    ctx: Context[Any, Any] = None,  # type: ignore[assignment]
) -> Annotated[CallToolResult, GenerationStructuredOutput]:
    """多图融合：融合多张参考图片的特征生成新图片。

    适用：把多张图片的风格或元素合并到一张新图。示例：“将图1的服装换到图2的模特
    身上”，需用“图1/图2”指代输入图片顺序。
    不适用：仅编辑单张图片改用 image_to_image；生成一组连贯分镜改用
    sequential_generation。
    """
    config = _config_from_context(ctx)
    return await run_multi_image_fusion(
        MultiImageFusionInput(
            **_filter_unset_params(
                {
                    "prompt": prompt,
                    "optimize_prompt_options": optimize_prompt_options,
                    "image": image,
                    "size": size,
                    "watermark": watermark,
                    "response_format": response_format,
                    "output_format": output_format,
                    "stream": stream,
                    "tools": tools,
                    "request_count": request_count,
                    "parallelism": parallelism,
                    "auto_save": auto_save,
                    "save_path": save_path,
                    "custom_name": custom_name,
                }
            )
        ),
        config=config,
        ctx=ctx,
    )


@mcp.tool(
    name="sequential_generation",
    title="Seedream 组图输出",
    annotations=GENERATION_TOOL_ANNOTATIONS,
)
async def sequential_generation(
    prompt: str = Field(
        min_length=PROMPT_MIN_LENGTH,
        max_length=PROMPT_MAX_LENGTH,
        pattern=_NON_BLANK_PATTERN,
        description=(
            "连贯的组图提示，需明确数量与内容，不超过300个汉字或600个英文单词。"
            "例如：生成4格漫画分镜，主角是戴红帽子的女孩，依次出现在咖啡馆、街道、公园、家中。"
        ),
    ),
    optimize_prompt_options: OptimizePromptOptions | None = Field(
        default=None,
        description="提示词优化配置，仅支持 standard 或 fast。",
    ),
    image: (
        Annotated[str, Field(pattern=_NON_BLANK_PATTERN)]
        | Annotated[
            list[Annotated[str, Field(pattern=_NON_BLANK_PATTERN)]],
            Field(max_length=SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES),
        ]
        | None
    ) = Field(
        default=None,
        description=(
            f"可选的参考图片，单张或多张，最多 {SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES} 张，"
            f"每张支持图像 URL、本地文件路径或 Base64 图片数据。"
        ),
    ),
    size: str | None = Field(
        default=None,
        description="生成图片尺寸，可选 1K/1.5K/2K/3K/4K 或 <宽>x<高> 像素值；未提供时使用全局默认值。例如：2K 或 1920x1080。",
    ),
    watermark: bool | None = Field(
        default=None,
        description="是否添加水印；未提供时沿用全局默认值（默认不添加）。",
    ),
    max_images: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SEQUENTIAL_TOTAL_IMAGES,
        description=f"本次请求允许生成的最大图片数量，范围 1-{MAX_SEQUENTIAL_TOTAL_IMAGES}。",
    ),
    response_format: ResponseFormat = Field(
        default=ResponseFormat.URL,
        description="响应格式，url 返回可下载链接，b64_json 返回 base64 数据。",
    ),
    output_format: OutputFormat | None = Field(
        default=None,
        description="输出图片格式，仅 5.0 系列（Pro/标准/Lite）支持 jpeg 或 png。",
    ),
    stream: bool = Field(
        default=False,
        description="是否启用流式输出；开启后将以事件流返回生成进度（5.0 Pro 不支持）。",
    ),
    tools: list[GenerationTool] | None = Field(
        default=None,
        description="模型工具配置，仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持联网搜索（web_search）。",
    ),
    request_count: int = Field(
        default=1,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description=(
            "同一提示并行发起的独立生成次数，每次各产出一组图片，组内图片数量由模型"
            "按提示词决定，最多 max_images 张；适合一次获取多组独立的组图结果。"
        ),
    ),
    parallelism: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PARALLEL_REQUEST_COUNT,
        description="并行度上限（1-10）；未提供时自动取 min(request_count, 10)，一般无需手动指定。",
    ),
    auto_save: bool | None = Field(
        default=None,
        description="是否自动保存到本地；未提供时遵循全局配置（默认开启）。",
    ),
    save_path: str | None = Field(
        default=None,
        max_length=1024,
        pattern=_NON_BLANK_PATTERN,
        description="自定义保存目录，未提供时使用自动保存配置的默认路径。",
    ),
    custom_name: str | None = Field(
        default=None,
        max_length=255,
        pattern=_NON_BLANK_PATTERN,
        description="自定义文件名前缀，未提供时根据提示词自动生成。",
    ),
    ctx: Context[Any, Any] = None,  # type: ignore[assignment]
) -> Annotated[CallToolResult, GenerationStructuredOutput]:
    """组图输出：一次生成多张内容关联的图片。

    适用：漫画分镜、品牌视觉套图等需要一组风格一致、内容连贯图片的场景。示例：
    “生成4格漫画，主角依次出现在4个场景”。注意 5.0 Pro 不支持组图，请改用
    5.0/5.0 Lite/4.5/4.0。
    不适用：融合多张参考图特征改用 multi_image_fusion。
    """
    config = _config_from_context(ctx)
    return await run_sequential_generation(
        SequentialGenerationInput(
            **_filter_unset_params(
                {
                    "prompt": prompt,
                    "optimize_prompt_options": optimize_prompt_options,
                    "image": image,
                    "size": size,
                    "watermark": watermark,
                    "max_images": max_images,
                    "response_format": response_format,
                    "output_format": output_format,
                    "stream": stream,
                    "tools": tools,
                    "request_count": request_count,
                    "parallelism": parallelism,
                    "auto_save": auto_save,
                    "save_path": save_path,
                    "custom_name": custom_name,
                }
            )
        ),
        config=config,
        ctx=ctx,
    )


@mcp.tool(
    name="browse_images",
    title="Seedream 图片浏览",
    annotations=BROWSE_TOOL_ANNOTATIONS,
)
async def browse_images(
    directory: str | None = Field(
        default=None,
        max_length=1024,
        pattern=_NON_BLANK_PATTERN,
        description=(
            "要浏览的目录路径，默认浏览全部授权的工作区根目录，多根时合并扫描去重；"
            "无 Roots 时回退 SEEDREAM_WORKSPACE_ROOT 配置的本地工作区根，"
            "均未设置时回退进程当前工作目录。"
        ),
    ),
    recursive: bool = Field(
        default=BrowseImagesInput.DEFAULT_RECURSIVE,
        description="是否递归查找子目录。",
    ),
    max_depth: int = Field(
        default=BrowseImagesInput.DEFAULT_MAX_DEPTH,
        ge=1,
        le=10,
        description="递归查找的最大深度（1-10）。",
    ),
    limit: int = Field(
        default=BrowseImagesInput.DEFAULT_LIMIT,
        ge=1,
        le=200,
        description="返回的最大文件数量（1-200）。",
    ),
    offset: int = Field(
        default=BrowseImagesInput.DEFAULT_OFFSET,
        ge=0,
        le=100000,
        description="分页偏移量（从第几张开始返回，0-100000），默认 0；配合 limit 翻页。",
    ),
    format_filter: list[Annotated[str, Field(max_length=16)]] | None = Field(
        default=None,
        description=(
            "需要过滤的图片后缀列表，如 ['.jpeg', '.png']；仅保留受支持的后缀。"
            "空列表或全部后缀不受支持时视为无有效后缀：跳过扫描返回空结果并回显原始输入。"
        ),
    ),
    show_details: bool = Field(
        default=BrowseImagesInput.DEFAULT_SHOW_DETAILS,
        description="是否展示文件大小、修改时间等详细信息。",
    ),
    ctx: Context[Any, Any] = None,  # type: ignore[assignment]
) -> Annotated[CallToolResult, BrowseImagesStructuredOutput]:
    """本地图片浏览：列出工作区中的图片文件。

    适用：在调用生成工具前查看可用的参考图片，或确认已生成图片的保存情况。支持
    递归、分页、按格式过滤。仅可浏览工作区目录内文件。
    """
    return await run_browse_images(
        BrowseImagesInput(
            **_filter_unset_params(
                {
                    "directory": directory,
                    "recursive": recursive,
                    "max_depth": max_depth,
                    "limit": limit,
                    "offset": offset,
                    "format_filter": format_filter,
                    "show_details": show_details,
                }
            )
        ),
        ctx=ctx,
    )


# ==================== 平铺 inputSchema 收紧 ====================

# 需要收紧 inputSchema 的五个平铺签名工具。
_FLAT_SCHEMA_TOOL_NAMES = (
    "text_to_image",
    "image_to_image",
    "multi_image_fusion",
    "sequential_generation",
    "browse_images",
)


def _tighten_flat_tool_schemas() -> None:
    """对五个平铺签名工具的 inputSchema 顶层补 additionalProperties: false。

    平铺签名丢失模型 extra=forbid 的补偿：输入模型本身以 extra=forbid 拒绝未知键，
    而 MCPServer 依据函数签名生成的参数模型默认忽略未知键，inputSchema 也不含
    additionalProperties 声明，拼错的参数名会被静默丢弃而非拒绝。本函数在注册后
    集中修补两处：inputSchema 顶层声明 additionalProperties: false，客户端本地校验
    即可拒绝拼错参数；参数模型替换为 extra=forbid 的子类，服务端运行时同样拒绝。
    子类化保留全部字段、校验器与序列化行为，仅收紧额外键策略。于 import 期执行，
    先于任何 tools/list 与 tools/call 生效；MCPServer 无公开的工具访问 API，经
    tool manager 取 Tool 对象修补其 parameters dict 与参数模型。

    实现依赖 SDK 私有路径 ``mcp._tool_manager`` 与 ``tool.fn_metadata.arg_model``
    的动态子类化，SDK 未对此作出公开 API 承诺。升级 mcp python-sdk 时须验证该
    私有路径仍然成立，私有结构变更导致的收紧失效由
    test_flat_input_schema_forbids_additional_properties 兜底报警。
    """
    for name in _FLAT_SCHEMA_TOOL_NAMES:
        tool = mcp._tool_manager.get_tool(name)
        if tool is None:
            logger.warning("未找到待收紧 inputSchema 的工具: {}", name)
            continue
        tool.parameters["additionalProperties"] = False
        arg_model = tool.fn_metadata.arg_model
        tool.fn_metadata.arg_model = type(
            arg_model.__name__,
            (arg_model,),
            {"model_config": {**arg_model.model_config, "extra": "forbid"}},
        )


_tighten_flat_tool_schemas()


# ==================== MCP 资源定义 ====================


@mcp.resource("seedream://workspace/roots{?verbose}", mime_type="application/json")
async def workspace_roots_resource(ctx: Context, verbose: bool = False) -> str:
    """工作区根目录。

    展示客户端授权的 MCP Roots，未授权时为空，避免暴露服务器本地目录。verbose 附
    各根的 resolve 后物理路径。客户端按原 URI seedream://workspace/roots 读取仍匹配，
    query 参数可省略。

    mcp 2.0 的 Context 注入仅接线模板资源，静态 URI 无法取得请求上下文，故以可选
    query 参数构成模板。模板资源处理器经 pydantic validate_call 包装，ctx 参数注解
    必须为裸 Context：参数化形式如 Context[Any, Any] 会被重校验为脱离请求的空实例，
    首次访问 ctx.session 即抛 ValueError，客户端收到 Error creating resource from
    template。裸 Context 注解的实例原样透传，session 与 lifespan_context 均可用。
    """
    async with workspace_roots_scope(ctx):
        # 边界经 SEEDREAM_WORKSPACE_ROOT 或进程 CWD 回退取得时属服务器环境而非客户端
        # 授权声明，其绝对路径不进入面向调用方的输出，按未授权输出空列表。
        if is_boundary_from_session_roots():
            roots = get_workspace_roots()
        else:
            roots = []
    payload: dict[str, Any] = {"roots": [str(root).replace("\\", "/") for root in roots]}
    if verbose:
        payload["resolved"] = [str(root.resolve()) for root in roots]
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.resource("seedream://server/info", mime_type="application/json")
async def server_info_resource() -> str:
    """服务器版本与当前生效配置摘要。

    SDK 2.0 起静态资源无请求上下文可注入；lifespan 注入的配置即进入 lifespan 时的
    活动配置对象，直接读活动配置语义等价。
    """
    config = get_active_config()
    return json.dumps(
        {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "model_id": config.model_id,
            "default_size": config.default_size,
            "auto_save_enabled": config.auto_save_enabled,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.resource("seedream://models/info", mime_type="application/json")
async def models_info_resource() -> str:
    """各模型别名与能力声明，供客户端按尺寸档位、工具、流式等选择合适模型。"""
    from dataclasses import asdict

    from .utils.model.model_capabilities import get_model_capabilities

    # asdict 派生能力字段，ModelCapabilities 新增字段自动出现在本资源，无需手工同步。
    models = []
    for alias, model_id in MODEL_ALIASES.items():
        caps_dict = asdict(get_model_capabilities(model_id))
        if "allowed_presets" in caps_dict and isinstance(
            caps_dict["allowed_presets"], (set, frozenset, list)
        ):
            caps_dict["allowed_presets"] = sorted(caps_dict["allowed_presets"])
        models.append({"alias": alias, "model_id": model_id, **caps_dict})
    return json.dumps({"models": models}, ensure_ascii=False, indent=2)


# ==================== MCP 风格预设 Prompt 定义 ====================


# 风格预设固定前缀，指引模型调用文生图工具并指明 prompt 参数来源。
_STYLE_PROMPT_PREFIX = "请使用 text_to_image 工具生成图片，将以下内容作为 prompt 参数：\n"


def _build_style_prompt(subject: str, style_suffix: str) -> str:
    """组装风格预设提示词：固定前缀后接主题与风格描述后缀。"""
    return f"{_STYLE_PROMPT_PREFIX}{subject}，{style_suffix}"


@mcp.prompt(name="seedream_style_anime", description="动漫风格生图提示词模板")
def style_anime_prompt(subject: str = "一个女孩站在樱花树下") -> str:
    """生成日系动漫风格图片的提示词模板，可作为文生图 prompt 使用。"""
    return _build_style_prompt(
        subject, "日系动漫风格，赛璐珞上色，鲜艳饱和的色彩，精细流畅的线条，柔和光影，高细节"
    )


@mcp.prompt(name="seedream_style_realistic", description="写实摄影风格生图提示词模板")
def style_realistic_prompt(subject: str = "城市夜景") -> str:
    """生成写实摄影风格图片的提示词模板，可作为文生图 prompt 使用。"""
    return _build_style_prompt(subject, "写实摄影风格，高清细节，自然光影，景深效果，专业摄影质感")


@mcp.prompt(name="seedream_style_watercolor", description="水彩画风格生图提示词模板")
def style_watercolor_prompt(subject: str = "山间小屋") -> str:
    """生成水彩画风格图片的提示词模板，可作为文生图 prompt 使用。"""
    return _build_style_prompt(subject, "水彩画风格，柔和晕染，通透色彩，手绘质感，留白")


@mcp.prompt(name="seedream_style_oil_painting", description="油画风格生图提示词模板")
def style_oil_painting_prompt(subject: str = "海边夕阳") -> str:
    """生成油画风格图片的提示词模板，可作为文生图 prompt 使用。"""
    return _build_style_prompt(subject, "油画风格，厚重笔触，丰富层次，经典光影，艺术质感")


# ==================== 主入口函数 ====================


def cli_main() -> int:
    """执行命令行主流程：解析参数、构建配置、初始化日志并按传输方式启动服务器。

    Returns:
        进程退出码：
        - 0: 正常退出
        - 1: 配置错误或运行异常
    """
    parser = _build_arg_parser()
    args = parser.parse_args()

    try:
        config = _build_config_from_args(args)
    except SeedreamConfigError as exc:
        print(f"配置错误: {exc.message}", file=sys.stderr)
        return 1

    # 注入活动配置，共享 client/tools 与 io_path 经 get_active_config 共用此实例，
    # 避免无 MCP Roots 时 io_path 重建第二个 config 造成双事实来源。
    set_active_config(config)

    # 初始化日志系统并打印启动信息。setup_logging 含目录创建等 I/O，只读容器或受限
    # 账号下可能抛 OSError，此处捕获并降级为 stderr 输出与退出码 1，与配置错误的优雅
    # 契约一致，避免直接 traceback 崩溃。OSError 不经 format_error_for_user：其未知错误
    # 档案标签会误导排查方向，且回显的绝对路径会泄露用户目录结构。
    try:
        setup_logging(
            config.log_level,
            config.log_file,
            force_standard_logging=True,
        )
    except OSError:
        print("日志系统初始化失败（请检查日志目录权限或磁盘空间）", file=sys.stderr)
        return 1
    logger.info(
        "Seedream MCP 启动: {} (version {})",
        SERVER_NAME,
        SERVER_VERSION,
    )

    try:
        transport = _build_run_options(args)
        if transport == "streamable-http":
            if (args.ssl_certfile is None) != (args.ssl_keyfile is None):
                message = (
                    "配置错误：--ssl-certfile 与 --ssl-keyfile 必须同时提供或同时省略，"
                    "仅提供其一无法建立 TLS。"
                )
                logger.error(message)
                print(message, file=sys.stderr)
                return 1
            auth_token = _resolve_http_auth_token(args)
            is_loopback = args.host in _LOOPBACK_HOSTS
            has_tls = bool(args.ssl_certfile)
            if not is_loopback:
                if not auth_token:
                    message = (
                        f"安全错误：streamable-http 绑定到非回环地址 {args.host} 必须配置鉴权令牌，"
                        "请通过 --auth-token 或 SEEDREAM_HTTP_AUTH_TOKEN 提供，避免未授权访问。"
                    )
                    logger.error(message)
                    print(message, file=sys.stderr)
                    return 1
                if not has_tls and not args.insecure_allow_non_tls:
                    message = (
                        f"安全错误：streamable-http 绑定到非回环地址 {args.host} 必须配置 TLS，"
                        "请通过 --ssl-certfile/--ssl-keyfile 提供，或在受信反向代理终结 TLS 时"
                        "显式传 --insecure-allow-non-tls，避免 Bearer 令牌明文传输被窃听。"
                    )
                    logger.error(message)
                    print(message, file=sys.stderr)
                    return 1
            _warn_remote_exposure(
                args.host,
                auth_enabled=bool(auth_token),
            )
            _run_streamable_http(
                args.host,
                args.port,
                auth_token,
                ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile,
                stateless=args.stateless,
            )
        else:
            mcp.run(transport=transport)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出。")
        return 0
    except Exception as exc:
        logger.error("服务器运行异常", exc_info=True)
        print(f"服务器运行失败: {format_error_for_user(exc)}", file=sys.stderr)
        return 1
    finally:
        _sync_cleanup()

    return 0


# ==================== 模块执行入口 ====================

if __name__ == "__main__":
    raise SystemExit(cli_main())
