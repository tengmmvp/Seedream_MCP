"""
Seedream MCP 服务器主模块。

注册文生图、图生图、多图融合、组图生成、图片浏览五种 MCP 工具，以及风格预设
Prompt 与工作区、服务器信息资源。负责配置注入、生命周期管理、main/cli_main 入口
与传输分派。CLI 参数解析由 cli 模块承担，streamable-http 中间件与传输配置由 transport
模块承担，二者经本模块重导出以保持 server 模块既有导入 surface 与 tests 访问路径不变。

outputSchema 声明契约：五个 @mcp.tool 工具函数的返回类型注解为 pydantic model
（GenerationStructuredOutput / BrowseImagesStructuredOutput），仅用于让 FastMCP 据此
生成 outputSchema；运行时实际返回 CallToolResult（含面向模型的文本与 structuredContent）。
故函数体中相应的 ``# type: ignore[return-value]`` 是该方案的必要组成，不可为统一返回
类型而移除，否则 outputSchema 声明会失效。详见 AGENTS.md 的 outputSchema 声明契约一节。
"""

from __future__ import annotations

# 标准库导入
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

# 本地模块导入
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
from .utils.errors import SeedreamConfigError, format_error_for_user
from .utils.logging import get_logger, setup_logging
from .utils.path_utils import get_workspace_roots, workspace_roots_scope
from .version import __version__

# ASGI 中间件与请求体上限常量重导出，供 tests 经 server 模块访问
from .transport import (  # noqa: F401
    _BearerTokenAuthMiddleware,
    _HealthCheckMiddleware,
    _LimitRequestBodyMiddleware,
)

if TYPE_CHECKING:
    from .client import SeedreamClient
    from .utils.download_manager import DownloadManager

# ==================== 服务器元数据常量 ====================

# 服务器标识名称
SERVER_NAME = "seedream_mcp"

# 服务器版本号
SERVER_VERSION = __version__

# 服务器功能说明
SERVER_INSTRUCTIONS = "Seedream 图像生成工具，支持文生图、图文生图、多图融合、组图输出与图片浏览。"

# streamable-http 默认监听配置，与 FastMCP Settings 默认值一致；_reset_lifespan_state 据此复位
_DEFAULT_HTTP_HOST = "127.0.0.1"
_DEFAULT_HTTP_PORT = 8000

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

# ==================== MCP 服务器实例 ====================

# 模块日志记录器。须先于引用它的 lifespan/cleanup 定义，否则函数体内无法解析该名
logger = get_logger(__name__)


# 共享 client/download_manager 对及在途引用计数，跨 lifespan 重入复用。
# stateless_http 模式下 FastMCP 每请求重入 lifespan，模块级单例确保连接池跨请求复用。
# 创建受 _shared_init_lock 保护避免并发重复构造；config 身份变化触发重建时，旧资源可能
# 仍被在途请求持有，引用计数确保仅当所有在途请求释放后才关闭旧实例，避免断开在途请求
# 已持有的 HTTP 连接池。
class _SharedResource:
    """共享 client/download_manager 对及在途引用计数。"""

    def __init__(
        self,
        client: "SeedreamClient",
        download_manager: "DownloadManager",
        config: SeedreamConfig,
    ) -> None:
        self.client = client
        self.download_manager = download_manager
        self.config = config
        self.refcount = 0


# 当前活动资源句柄
_active_resource: _SharedResource | None = None
# 被重建取代但仍有在途引用的资源，引用归零时由 _release_resource 关闭并移出
_retired_resources: list[_SharedResource] = []
# 活动资源的 client/dm 别名，保留供测试直接读取活动实例与 _sync_cleanup 兜底关闭
_shared_client: SeedreamClient | None = None
_shared_download_manager: DownloadManager | None = None
_shared_init_lock = asyncio.Lock()


def _config_from_context(ctx: Context[Any, Any, Any]) -> SeedreamConfig:
    """
    从 MCP 请求上下文获取 lifespan 注入的配置，无法获取时回退全局配置并记录告警。

    工具与资源经 ctx.request_context.lifespan_context 取配置，避免直接依赖模块级全局
    状态，消除热重载窗口内活动配置与请求实际使用的配置不一致。复用 parallel 的
    _get_lifespan_resource 统一资源探测实现。
    """
    from .tools.core.parallel import _get_lifespan_resource

    config = _get_lifespan_resource(ctx, "config", SeedreamConfig)
    if config is not None:
        return config
    logger.warning("lifespan 上下文未注入配置，回退全局活动配置")
    return get_active_config()


async def _safe_close(obj: Any) -> None:
    """关闭共享资源，吞掉异常避免清理路径中断。"""
    if obj is None:
        return
    try:
        await obj.close()
    except Exception as exc:
        logger.warning("关闭共享资源失败: {}", exc)


def _sync_active_aliases(resource: _SharedResource | None) -> None:
    """同步活动资源到 _shared_client/_shared_download_manager 别名。

    别名始终指向活动资源的 client/dm，保留测试直接读取活动实例的既有路径，并供
    _sync_cleanup 兜底关闭。
    """
    global _shared_client, _shared_download_manager
    if resource is None:
        _shared_client = None
        _shared_download_manager = None
    else:
        _shared_client = resource.client
        _shared_download_manager = resource.download_manager


async def _build_active_resource(config: SeedreamConfig) -> _SharedResource:
    """创建并初始化新的共享资源句柄，部分初始化失败时关闭已创建部分避免泄漏。"""
    from .client import SeedreamClient
    from .utils.download_manager import DownloadManager

    new_client = SeedreamClient(config)
    new_download_manager = DownloadManager(
        timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
    )
    try:
        await new_client.__aenter__()
        await new_download_manager.__aenter__()
    except Exception:
        await _safe_close(new_client)
        await _safe_close(new_download_manager)
        raise
    return _SharedResource(new_client, new_download_manager, config)


async def _close_resource(resource: _SharedResource) -> None:
    """关闭资源持有的 client 与 download_manager。"""
    await _safe_close(resource.client)
    await _safe_close(resource.download_manager)


async def _retire_resource(resource: _SharedResource | None) -> None:
    """资源被新资源取代。仍有在途引用时纳入退役追踪，否则立即关闭。"""
    if resource is None:
        return
    if resource.refcount > 0:
        _retired_resources.append(resource)
    else:
        await _close_resource(resource)


async def _release_resource(resource: _SharedResource) -> None:
    """递减在途引用；退役资源引用归零时关闭并移出追踪列表。"""
    resource.refcount -= 1
    if resource.refcount > 0 or resource is _active_resource:
        return
    if resource in _retired_resources:
        _retired_resources.remove(resource)
    await _close_resource(resource)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """
    FastMCP 生命周期管理：注入共享配置、SeedreamClient 与 DownloadManager。

    资源以引用计数的模块级单例持有，跨 lifespan 重入复用。stateless_http 模式下 FastMCP
    每请求重入 lifespan，活动资源仅登记在途引用而不关闭，使连接池跨请求复用。config
    身份变化触发重建：新资源立即取代活动槽位，旧资源若仍有在途引用则纳入退役追踪，待
    最后一个在途请求释放后再关闭，避免运行期 set_config/reload_config 断开在途请求已
    持有的 HTTP 连接池。stdio 与普通 streamable-http 仅单次进入 lifespan，teardown 时
    在同事件循环清理。工具经 ctx.request_context.lifespan_context 取
    ["config"]/["client"]/["download_manager"]。
    """
    global _active_resource
    config = get_active_config()
    if _active_resource is None or _active_resource.config is not config:
        async with _shared_init_lock:
            if _active_resource is None or _active_resource.config is not config:
                old = _active_resource
                _active_resource = await _build_active_resource(config)
                _sync_active_aliases(_active_resource)
                await _retire_resource(old)
            resource = _active_resource
            resource.refcount += 1
    else:
        resource = _active_resource
        resource.refcount += 1
    try:
        yield {
            "config": resource.config,
            "client": resource.client,
            "download_manager": resource.download_manager,
        }
    finally:
        await _release_resource(resource)
    if not getattr(server.settings, "stateless_http", False):
        await _cleanup_shared_resources()


async def _cleanup_shared_resources() -> None:
    """关闭并清空活动与退役资源持有的 HTTP 连接池。"""
    global _active_resource
    retired = list(_retired_resources)
    _retired_resources.clear()
    active = _active_resource
    _active_resource = None
    _sync_active_aliases(None)
    for resource in retired:
        await _close_resource(resource)
    if active is not None:
        await _close_resource(active)


def _sync_cleanup() -> None:
    """同步入口的进程级兜底清理。

    streamable-http 的优雅关闭在 _run_streamable_http 内于服务事件循环上执行，stdio 在
    lifespan teardown 同循环清理；两者完成后活动资源已为 None，本函数为防御性兜底，覆盖
    异常退出未触发上述清理的情形。先提取并清空全局引用，确保即便后续清理协程抛错也不泄漏。
    """
    global _active_resource
    retired = list(_retired_resources)
    _retired_resources.clear()
    _active_resource = None
    client = _shared_client
    download_manager = _shared_download_manager
    _sync_active_aliases(None)

    async def _close_held() -> None:
        await _safe_close(client)
        await _safe_close(download_manager)
        for resource in retired:
            await _close_resource(resource)

    try:
        asyncio.run(_close_held())
    except RuntimeError:
        # 无事件循环或已有循环运行，无法安全 asyncio.run；引用已清空，余量交 GC/OS
        pass
    except Exception as exc:
        logger.warning("同步清理共享资源失败: {}", exc)


def _reset_lifespan_state() -> None:
    """重置 lifespan 单例、活动配置与初始化锁，仅供测试隔离调用。

    一并重建 _shared_init_lock 与自动保存清理锁，避免跨事件循环复用绑定了旧循环的 asyncio.Lock。
    复位 mcp.settings 的 streamable-http 配置，避免上一个用例的 host/port/stateless 泄漏。
    """
    global _shared_client, _shared_download_manager, _shared_init_lock, _active_resource
    _shared_client = None
    _shared_download_manager = None
    _active_resource = None
    _retired_resources.clear()
    set_active_config(None)
    _shared_init_lock = asyncio.Lock()
    mcp.settings.stateless_http = False
    mcp.settings.host = _DEFAULT_HTTP_HOST
    mcp.settings.port = _DEFAULT_HTTP_PORT
    from .utils.auto_save import _reset_cleanup_state

    _reset_cleanup_state()


# 初始化 FastMCP 服务器实例
mcp = FastMCP(
    SERVER_NAME,
    instructions=SERVER_INSTRUCTIONS,
    lifespan=app_lifespan,
)


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
    from .utils.model_capabilities import get_model_capabilities

    models = []
    for alias, model_id in MODEL_ALIASES.items():
        caps = get_model_capabilities(model_id)
        models.append(
            {
                "alias": alias,
                "model_id": model_id,
                "display_name": caps.display_name,
                "allowed_presets": sorted(caps.allowed_presets),
                "min_size_pixels": caps.min_size_pixels,
                "max_size_pixels": caps.max_size_pixels,
                "size_pixel_multiple": caps.size_pixel_multiple,
                "max_reference_images": caps.max_reference_images,
                "supports_output_format": caps.supports_output_format,
                "supports_tools": caps.supports_tools,
                "supports_stream": caps.supports_stream,
                "supports_fast_optimize_prompt": caps.supports_fast_optimize_prompt,
            }
        )
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

    # 注入活动配置，server 的 client/tools 与 path_utils 经 get_active_config 共用此实例，
    # 避免无 MCP Roots 时 path_utils 重建第二个 config 造成双事实来源
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
