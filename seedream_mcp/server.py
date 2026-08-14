"""
Seedream MCP 服务器主模块。

注册文生图、图生图、多图融合、组图生成、图片浏览五种 MCP 工具，以及风格预设
Prompt 与工作区、服务器信息资源。负责配置注入、main/cli_main 入口与传输分派。
FastMCP 实例与共享资源生命周期管理由 resources 模块承担，本模块导入 mcp 完成注册
并重导出 resources 符号，保持 server 既有导入 surface 与 tests 访问路径不变。CLI
参数解析由 cli 模块承担，streamable-http 中间件与传输配置由 transport 模块承担，
二者经本模块重导出。

outputSchema 声明契约：五个 @mcp.tool 工具函数的返回类型注解为 pydantic model
（GenerationStructuredOutput / BrowseImagesStructuredOutput），仅用于让 FastMCP 据此
生成 outputSchema；运行时实际返回 CallToolResult（含面向模型的文本与 structuredContent）。
故函数体中相应的 ``# type: ignore[return-value]`` 是该方案的必要组成，不可为统一返回
类型而移除，否则 outputSchema 声明会失效。详见 AGENTS.md 的 outputSchema 声明契约一节。
"""

from __future__ import annotations

# 标准库导入
import json
import sys
from typing import Any

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

# 本地模块导入
from . import resources
from .cli import _build_arg_parser, _build_config_from_args, _build_run_options
from .config import (
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
from .tools.core.outputs import (
    BrowseImagesStructuredOutput,
    GenerationStructuredOutput,
)
from .transport import (
    _LOOPBACK_HOSTS,
    _apply_http_bind_settings,
    _resolve_http_auth_token,
    _run_streamable_http,
)
from .utils.core.errors import SeedreamConfigError, format_error_for_user
from .utils.core.logs import get_logger, setup_logging
from .utils.io.io_path import get_workspace_roots, workspace_roots_scope

# resources 符号重导出：mcp、SERVER_NAME、SERVER_VERSION、_sync_cleanup 为本模块直接
# 使用，其余供 tests 与既有 import 路径经 server 模块访问
from .resources import (  # noqa: F401
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    SERVER_VERSION,
    _cleanup_shared_resources,
    _reset_lifespan_state,
    _retired_resources,
    _sync_cleanup,
    app_lifespan,
    mcp,
)

# ASGI 中间件与请求体上限常量重导出，供 tests 经 server 模块访问
from .transport import (  # noqa: F401
    _BearerTokenAuthMiddleware,
    _HealthCheckMiddleware,
    _LimitRequestBodyMiddleware,
)

# ==================== 工具注解常量 ====================

# 生成类工具的能力标注
# - readOnlyHint=False：会生成文件，非只读
# - destructiveHint=False：不破坏既有数据
# - idempotentHint=False：每次生成结果可能不同，非幂等
# - openWorldHint=True：需联网调用 API，属开放世界操作
GENERATION_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

# 浏览类工具的能力标注
# - readOnlyHint=True：仅读取文件列表，只读
# - destructiveHint=False：不破坏既有数据
# - idempotentHint=True：相同输入得到相同结果，幂等
# - openWorldHint=False：仅访问本地文件系统，非开放世界
BROWSE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

# 模块日志记录器
logger = get_logger(__name__)


def _config_from_context(ctx: Context[Any, Any, Any]) -> SeedreamConfig:
    """
    从 MCP 请求上下文获取 lifespan 注入的配置，无法获取时回退全局配置并记录告警。

    工具与资源经 ctx.request_context.lifespan_context 取配置，避免直接依赖模块级全局
    状态，消除热重载窗口内活动配置与请求实际使用的配置不一致。复用 parallel 的
    _get_lifespan_resource 统一资源探测实现。
    """
    from .tools.core.parallel import _get_lifespan_resource

    config = _get_lifespan_resource(ctx, resources.LIFESPAN_KEY_CONFIG, SeedreamConfig)
    if config is not None:
        return config
    logger.warning("lifespan 上下文未注入配置，回退全局活动配置")
    return get_active_config()


# ==================== MCP 工具函数定义 ====================


@mcp.tool(
    name="seedream_text_to_image",
    title="Seedream 文生图",
    annotations=GENERATION_TOOL_ANNOTATIONS,
)
async def seedream_text_to_image(
    params: TextToImageInput,
    ctx: Context[Any, Any, Any],
) -> GenerationStructuredOutput:
    """
    文生图：根据文字指令生成单张图片。

    适用：从零开始按文字描述创建图片。示例：生成“赛博朋克风格的城市夜景”。
    不适用：需要基于已有图片修改时改用 seedream_image_to_image；需要一次生成多张
    风格一致的图片时改用 seedream_sequential_generation。
    """
    config = _config_from_context(ctx)
    return await run_text_to_image(params, config=config, ctx=ctx)  # type: ignore[return-value]


@mcp.tool(
    name="seedream_image_to_image",
    title="Seedream 图文生图",
    annotations=GENERATION_TOOL_ANNOTATIONS,
)
async def seedream_image_to_image(
    params: ImageToImageInput,
    ctx: Context[Any, Any, Any],
) -> GenerationStructuredOutput:
    """
    图文生图：基于已有图片进行编辑。

    适用：在保留输入图片主体或构图的前提下做元素增删、风格转化、材质替换、色调
    迁移、改变背景或视角尺寸等。示例：“把人物背景换成海滩”。
    不适用：纯文字生图改用 seedream_text_to_image；融合多张图片特征改用
    seedream_multi_image_fusion。
    """
    config = _config_from_context(ctx)
    return await run_image_to_image(params, config=config, ctx=ctx)  # type: ignore[return-value]


@mcp.tool(
    name="seedream_multi_image_fusion",
    title="Seedream 多图融合",
    annotations=GENERATION_TOOL_ANNOTATIONS,
)
async def seedream_multi_image_fusion(
    params: MultiImageFusionInput,
    ctx: Context[Any, Any, Any],
) -> GenerationStructuredOutput:
    """
    多图融合：融合多张参考图片的特征生成新图片。

    适用：把多张图片的风格或元素合并到一张新图。示例：“将图1的服装换到图2的模特
    身上”，需用“图1/图2”指代输入图片顺序。
    不适用：仅编辑单张图片改用 seedream_image_to_image；生成一组连贯分镜改用
    seedream_sequential_generation。
    """
    config = _config_from_context(ctx)
    return await run_multi_image_fusion(  # type: ignore[return-value]
        params, config=config, ctx=ctx
    )


@mcp.tool(
    name="seedream_sequential_generation",
    title="Seedream 组图输出",
    annotations=GENERATION_TOOL_ANNOTATIONS,
)
async def seedream_sequential_generation(
    params: SequentialGenerationInput,
    ctx: Context[Any, Any, Any],
) -> GenerationStructuredOutput:
    """
    组图输出：一次生成多张内容关联的图片。

    适用：漫画分镜、品牌视觉套图等需要一组风格一致、内容连贯图片的场景。示例：
    “生成4格漫画，主角依次出现在4个场景”。注意 5.0 Pro 不支持组图，请改用
    5.0/5.0 Lite/4.5/4.0。
    不适用：融合多张参考图特征改用 seedream_multi_image_fusion。
    """
    config = _config_from_context(ctx)
    return await run_sequential_generation(  # type: ignore[return-value]
        params, config=config, ctx=ctx
    )


@mcp.tool(
    name="seedream_browse_images",
    title="Seedream 图片浏览",
    annotations=BROWSE_TOOL_ANNOTATIONS,
)
async def seedream_browse_images(
    params: BrowseImagesInput,
    ctx: Context[Any, Any, Any],
) -> BrowseImagesStructuredOutput:
    """
    本地图片浏览：列出工作区中的图片文件。

    适用：在调用生成工具前查看可用的参考图片，或确认已生成图片的保存情况。支持
    递归、分页、按格式过滤。仅可浏览工作区目录内文件。
    """
    return await run_browse_images(params, ctx=ctx)  # type: ignore[return-value]


# ==================== MCP 资源定义 ====================


@mcp.resource("seedream://workspace/roots")
async def workspace_roots_resource() -> str:
    """工作区根目录。展示客户端授权的 MCP Roots，未授权时为空，避免暴露服务器本地目录。"""
    ctx = mcp.get_context()
    async with workspace_roots_scope(ctx):
        roots = get_workspace_roots()
    return json.dumps(
        {"roots": [str(root).replace("\\", "/") for root in roots]}, ensure_ascii=False, indent=2
    )


@mcp.resource("seedream://server/info")
async def server_info_resource() -> str:
    """服务器版本与当前生效配置摘要。"""
    ctx = mcp.get_context()
    config = _config_from_context(ctx)
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


@mcp.resource("seedream://models/info")
async def models_info_resource() -> str:
    """各模型别名与能力声明，供客户端按尺寸档位、工具、流式等选择合适模型。"""
    from dataclasses import asdict

    from .utils.model.model_capabilities import get_model_capabilities

    # asdict 派生能力字段，ModelCapabilities 新增字段自动出现在本资源，无需手工同步
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


# 风格预设固定前缀，指引模型调用文生图工具并指明 prompt 参数来源
_STYLE_PROMPT_PREFIX = "请使用 seedream_text_to_image 工具生成图片，将以下内容作为 prompt 参数：\n"


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
    """命令行主入口函数

    负责参数解析、配置构建、日志初始化与服务器启动。

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
    # 避免无 MCP Roots 时 io_path 重建第二个 config 造成双事实来源
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
            _apply_http_bind_settings(
                args.host,
                args.port,
                args.stateless,
                auth_enabled=bool(auth_token),
            )
            _run_streamable_http(
                args.host,
                args.port,
                auth_token,
                ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile,
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
