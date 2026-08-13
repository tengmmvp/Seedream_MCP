"""
Seedream MCP 服务器主模块。

注册文生图、图生图、多图融合、组图生成、图片浏览五种 MCP 工具，以及风格预设
Prompt 与工作区、服务器信息资源。负责 CLI 参数解析、配置注入、生命周期管理与
streamable-http 的 Bearer 鉴权装配。
"""

from __future__ import annotations

# 标准库导入
import argparse
import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal, Optional, cast

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

# 本地模块导入
from .config import (
    MODEL_ALIASES,
    SeedreamConfig,
    build_config_from_sources,
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
from .utils.errors import SeedreamConfigError, format_error_for_user
from .utils.logging import get_logger, setup_logging
from .utils.path_utils import get_workspace_roots, workspace_roots_scope
from .version import __version__

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

# 共享客户端与下载管理器：模块级单例，跨 lifespan 重入复用。
# stateless_http 模式下 FastMCP 每请求重入 lifespan，模块级单例确保连接池跨请求复用。
# 创建受 _shared_init_lock 保护避免并发重复构造；config 身份变化时自动重建。
_shared_client: Optional[SeedreamClient] = None
_shared_download_manager: Optional[DownloadManager] = None
_shared_init_lock = asyncio.Lock()


def _config_from_context(ctx: Context[Any, Any, Any]) -> SeedreamConfig:
    """
    从 MCP 请求上下文获取 lifespan 注入的配置，无法获取时回退全局配置。

    工具通过 ctx.request_context.lifespan_context 取配置，避免直接依赖模块级全局状态。
    """
    state = ctx.request_context.lifespan_context
    config = state.get("config") if isinstance(state, dict) else None
    return config if isinstance(config, SeedreamConfig) else get_active_config()


async def _safe_close(obj: Any) -> None:
    """关闭共享资源，吞掉异常避免清理路径中断。"""
    if obj is None:
        return
    try:
        await obj.close()
    except Exception as exc:
        logger.warning("关闭共享资源失败: {}", exc)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """
    FastMCP 生命周期管理：注入共享配置、SeedreamClient 与 DownloadManager。

    单例首次创建后跨 lifespan 重入复用。config 身份变化仅在 lifespan 重入时检测并重建：
    stateless_http 每请求重入，运行期 set_config/reload_config 可对后续请求生效；stdio
    与普通 streamable-http 仅单次进入 lifespan，运行期更换配置不会重算已注入的快照与共享
    客户端，热重载仅在 stateless_http 或进程重启后对在用请求生效。stateless_http 不在
    teardown 清理以保留连接复用；其余模式在 teardown 同事件循环清理。重建会立即关闭旧
    单例，调用 set_config/reload_config 应避开在途请求，否则在途请求持有的 HTTP 连接
    会被断开。工具经 ctx.request_context.lifespan_context 取
    ["config"]/["client"]/["download_manager"]。
    """
    global _shared_client, _shared_download_manager
    config = get_active_config()
    if _shared_client is None or _shared_client.config is not config:
        async with _shared_init_lock:
            if _shared_client is None or _shared_client.config is not config:
                old_client = _shared_client
                old_download_manager = _shared_download_manager
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
                    # 部分初始化失败时关闭已创建的资源，避免连接池与文件描述符泄漏
                    await _safe_close(new_client)
                    await _safe_close(new_download_manager)
                    raise
                _shared_client = new_client
                _shared_download_manager = new_download_manager
                await _safe_close(old_client)
                await _safe_close(old_download_manager)
    yield {
        "config": config,
        "client": _shared_client,
        "download_manager": _shared_download_manager,
    }
    if not getattr(server.settings, "stateless_http", False):
        await _cleanup_shared_resources()


async def _cleanup_shared_resources() -> None:
    """关闭并清空 lifespan 单例持有的 HTTP 连接池。"""
    global _shared_client, _shared_download_manager
    client = _shared_client
    download_manager = _shared_download_manager
    _shared_client = None
    _shared_download_manager = None
    await _safe_close(client)
    await _safe_close(download_manager)


def _sync_cleanup() -> None:
    """同步入口的进程级清理。

    stateless_http 模式 lifespan 不在 teardown 清理以保留连接复用，由 cli_main 在
    服务器退出时补清理。先提取并清空全局引用，确保即便后续清理协程抛错也不会泄漏引用。

    uvicorn 退出时其事件循环已停止，共享 HTTP 资源绑定在已关闭的旧循环上，跨循环
    aclose 对底层传输可能无效并被 _safe_close 静默忽略，真正的 socket 回收依赖进程
    退出时由操作系统完成。_safe_close 已吞掉异常，清理路径不中断。
    """
    global _shared_client, _shared_download_manager
    client = _shared_client
    download_manager = _shared_download_manager
    _shared_client = None
    _shared_download_manager = None

    async def _close_held() -> None:
        await _safe_close(client)
        await _safe_close(download_manager)

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
    """
    global _shared_client, _shared_download_manager, _shared_init_lock
    _shared_client = None
    _shared_download_manager = None
    set_active_config(None)
    _shared_init_lock = asyncio.Lock()
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


# ==================== 命令行参数解析 ====================


def _build_config_from_args(args: argparse.Namespace) -> SeedreamConfig:
    """
    从命令行参数构建服务器配置对象

    优先级：命令行参数 > 系统环境变量 > .env 文件 > 默认值。

    Args:
        args: 解析后的命令行参数对象。

    Returns:
        构建完成的 SeedreamConfig 配置实例。

    Raises:
        SeedreamConfigError: 缺少 API 密钥等必需参数时抛出。
    """
    overrides: dict[str, object] = {
        "api_key": args.api_key,
        "base_url": args.base_url,
        "model": args.model,
        "default_size": args.default_size,
        "watermark": args.watermark,
        "log_level": args.log_level,
    }
    return build_config_from_sources(
        overrides=overrides,
        env_file=args.config_file,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    """
    构建命令行参数解析器

    定义所有支持的命令行选项，包括 API 配置、模型选择、日志级别等。

    Returns:
        配置完成的 ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(
        description="Seedream MCP 服务器 - AI 图像生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  seedream-image-mcp --api-key your_key_here
  seedream-image-mcp --api-key your_key_here --model doubao-seedream-4.5
  --default-size 4K --log-level DEBUG
  seedream-image-mcp --api-key your_key_here --config-file ./config.env
        """,
    )

    # API 认证配置
    parser.add_argument(
        "--api-key",
        help="火山引擎 API 密钥；建议优先用 ARK_API_KEY 环境变量，命令行传入会出现在进程列表与 shell 历史中",
    )
    parser.add_argument(
        "--config-file",
        help="可选的 .env 配置文件路径，用于加载额外环境变量",
    )

    # 模型与生成配置
    parser.add_argument(
        "--model",
        choices=list(MODEL_ALIASES.keys()),
        default=None,
        help="模型选择（默认按配置或内置默认值）",
    )
    parser.add_argument(
        "--default-size",
        type=str,
        default=None,
        help='默认生成尺寸（支持 1K/2K/3K/4K 或 "<宽>x<高>"，默认按配置或内置默认值）',
    )
    watermark_group = parser.add_mutually_exclusive_group()
    watermark_group.add_argument(
        "--watermark",
        dest="watermark",
        action="store_true",
        default=None,
        help="启用默认水印（未传入时按配置或内置默认值）",
    )
    watermark_group.add_argument(
        "--no-watermark",
        dest="watermark",
        action="store_false",
        help="关闭默认水印（未传入时按配置或内置默认值）",
    )

    # 日志配置
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="日志级别（默认按配置或内置默认值）",
    )

    # 网络配置
    parser.add_argument(
        "--base-url",
        default=None,
        help="API 基础 URL（默认按配置或内置默认值）",
    )

    # 传输层配置
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP 传输方式（默认 stdio）",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="streamable-http 监听地址（默认 127.0.0.1，仅 streamable-http 生效；"
        "绑定非回环地址将触发安全告警）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="streamable-http 监听端口（默认 8000，仅 streamable-http 生效）",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=False,
        help="streamable-http 启用无状态模式，更适合远程多客户端与负载均衡（默认关闭）",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="streamable-http 的 Bearer 鉴权令牌；建议优先用 SEEDREAM_HTTP_AUTH_TOKEN "
        "环境变量，命令行传入会出现在进程列表与 shell 历史中；绑定非回环地址时必须配置，"
        "否则拒绝启动",
    )
    parser.add_argument(
        "--ssl-certfile",
        default=None,
        help="streamable-http 的 TLS 证书文件路径，绑定非回环地址时必须配置以防令牌明文传输；"
        "受信反向代理终结 TLS 时可用 --insecure-allow-non-tls 豁免",
    )
    parser.add_argument(
        "--ssl-keyfile",
        default=None,
        help="streamable-http 的 TLS 私钥文件路径，与 --ssl-certfile 配合使用",
    )
    parser.add_argument(
        "--insecure-allow-non-tls",
        action="store_true",
        default=False,
        help="显式允许非回环地址以明文运行 streamable-http，仅用于受信反向代理终结 TLS 的场景",
    )

    return parser


def _build_run_options(args: argparse.Namespace) -> Literal["stdio", "streamable-http"]:
    """
    构建 MCP 运行传输方式。

    SSE 传输已被 MCP 2025-03-26 规范弃用并由 Streamable HTTP 取代，
    本服务仅支持 stdio 本地传输与 streamable-http 远程传输两种方式。
    """
    return cast(Literal["stdio", "streamable-http"], args.transport)


# streamable-http 回环地址集合：绑定这些地址视为仅本机信任
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class _BearerTokenAuthMiddleware:
    """
    streamable-http Bearer 令牌鉴权 ASGI 中间件。

    校验请求 Authorization 头中的 Bearer 令牌，匹配则放行，否则 HTTP 流量返回 401。
    启用鉴权时拒绝 websocket 等非 HTTP 流量并以 code 1008 关闭，避免绕过 Bearer 校验。
    使用 hmac.compare_digest 做常数时间比较，避免时序侧信道泄露令牌。
    """

    def __init__(self, app: Any, expected_token: str) -> None:
        self.app = app
        self._expected = expected_token.encode("utf-8")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type != "http":
            # 启用鉴权时拒绝 websocket 等非 http 流量，避免绕过 Bearer 校验
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        if self._request_authorized(scope):
            await self.app(scope, receive, send)
            return

        await self._send_unauthorized(send)

    def _request_authorized(self, scope: Any) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                if value[:7].lower() == b"bearer ":
                    return hmac.compare_digest(value[7:].strip(), self._expected)
                return False
        return False

    async def _send_unauthorized(self, send: Any) -> None:
        body = json.dumps(
            {"error": "invalid_token", "error_description": "Authentication required"}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", b'Bearer error="invalid_token"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


# streamable-http 请求体大小上限：按 Content-Length 早拒超大请求体，防止已认证客户端
# 提交 GB 级 prompt/data URI 导致 OOM。多图融合等大体量场景若超此值应分批或调高。
_MAX_STREAMABLE_HTTP_BODY = 100 * 1024 * 1024


class _LimitRequestBodyMiddleware:
    """streamable-http 请求体大小限制 ASGI 中间件。

    先按 Content-Length 头早拒超过上限的请求作为快速路径；再包装 receive 累计 chunked 实际
    接收字节数，超限即短路返回 413，防止缺失或谎报 Content-Length 的超大请求体在 pydantic
    物料化前撑爆内存。仅作用于 http 请求，websocket 等非 http 流量原样透传。
    """

    def __init__(self, app: Any, max_body_size: int) -> None:
        self.app = app
        self._max_body_size = max_body_size

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_length = 0
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    pass
                break

        # 快速路径：声明长度超限直接 413，避免进入应用读取阶段
        if content_length > self._max_body_size:
            await self._send_too_large(send)
            return

        # chunked 防御：累计实际接收字节，缺失或谎报 Content-Length 时超限短路 413
        total_received = 0
        too_large = False

        async def receive_wrapper() -> Any:
            nonlocal total_received, too_large
            message = await receive()
            if not too_large and message.get("type") == "http.request":
                total_received += len(message.get("body", b""))
                if total_received > self._max_body_size:
                    too_large = True
                    # 返回空终帧，停止向下游投递剩余超大请求体
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def send_wrapper(message: Any) -> None:
            # 一旦判定超限，吞掉下游响应，由本中间件统一回 413 避免双响应
            if too_large:
                return
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)

        if too_large:
            await self._send_too_large(send)

    async def _send_too_large(self, send: Any) -> None:
        body = json.dumps(
            {"error": "request_too_large", "error_description": "Request body exceeds limit"}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _HealthCheckMiddleware:
    """streamable-http 健康检查中间件，短路 GET /health 返回进程存活状态。

    最后装配使其成为最外层，先于 Bearer 鉴权与请求体限制执行，负载均衡与健康探针
    无需令牌即可探活。仅做 liveness 判定，不探测上游 API，避免拖慢探针。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == "/health"
        ):
            body = b'{"status":"ok"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def _resolve_http_auth_token(args: argparse.Namespace) -> str:
    """解析 streamable-http 鉴权令牌：CLI 参数优先，其次活动配置。"""
    token = args.auth_token or get_active_config().http_auth_token
    return (token or "").strip()


def _apply_http_bind_settings(host: str, port: int, stateless: bool, auth_enabled: bool) -> None:
    """
    将 streamable-http 监听配置写入 FastMCP settings，并就暴露风险与鉴权状态告警。

    非回环绑定时，调用方需已在 cli_main 中完成 fail-closed 校验，即必须配置鉴权令牌，
    因此此处非回环分支仅用于确认已启用鉴权。stateless 启用无状态模式，更适合远程
    多客户端与负载均衡场景。
    """
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.stateless_http = stateless
    _warn_remote_exposure(host, auth_enabled)


def _warn_remote_exposure(host: str, auth_enabled: bool) -> None:
    """根据绑定地址与鉴权状态输出风险告警，同时写入日志与控制台。"""
    if host in _LOOPBACK_HOSTS:
        if auth_enabled:
            message = "streamable-http 已启用 Bearer 鉴权，本机访问需在 Authorization 头携带令牌。"
        else:
            message = (
                "streamable-http 未启用应用层认证，仅限本机信任环境使用；"
                "如需远程访问，请使用 --auth-token 配置鉴权或经反向代理增加鉴权。"
            )
    elif auth_enabled:
        message = (
            f"streamable-http 绑定到 {host}（非回环地址）并已启用 Bearer 鉴权；"
            "请确认网络隔离与令牌妥善保管。"
        )
    else:
        message = (
            f"streamable-http 绑定到 {host}（非回环地址）且未启用鉴权，存在未授权访问风险；"
            "请使用 --auth-token 配置鉴权。"
        )
    logger.warning(message)
    print(message)


def _run_streamable_http(
    host: str,
    port: int,
    auth_token: str,
    ssl_certfile: Optional[str] = None,
    ssl_keyfile: Optional[str] = None,
) -> None:
    """
    启动 streamable-http 传输。

    配置鉴权令牌时，在 FastMCP 应用外层包裹 Bearer 校验中间件，未携带有效令牌的
    请求返回 401。配置 TLS 证书时透传给 uvicorn 启用 HTTPS。仅使用 FastMCP 公开接口
    streamable_http_app() 获取 ASGI 应用，避免依赖其私有鉴权装配路径。
    """
    import uvicorn

    app = mcp.streamable_http_app()
    # body 大小限制中间件：按 Content-Length 早拒超大请求体，并对 chunked 流累计字节超限即返回 413，
    # 防 GB 级 payload 物料化导致 OOM。鉴权中间件后添加、因 Starlette insert(0) 成为更外层先执行；
    # 二者均在物料化前阻断超大请求体，故无论鉴权与否都不会被完整读入内存
    app.add_middleware(_LimitRequestBodyMiddleware, max_body_size=_MAX_STREAMABLE_HTTP_BODY)
    if auth_token:
        app.add_middleware(_BearerTokenAuthMiddleware, expected_token=auth_token)
        logger.info("streamable-http 已启用 Bearer 令牌鉴权")
    # 健康检查最后添加，因 Starlette insert(0) 成为最外层，先于鉴权短路 GET /health 供探针探活
    app.add_middleware(_HealthCheckMiddleware)
    ssl_kwargs: dict[str, Any] = {}
    if ssl_certfile:
        ssl_kwargs["ssl_certfile"] = ssl_certfile
        ssl_kwargs["ssl_keyfile"] = ssl_keyfile
        logger.info("streamable-http 已启用 TLS")
    uvicorn.run(app, host=host, port=port, **ssl_kwargs)


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
        print(f"配置错误: {exc.message}")
        return 1

    # 注入活动配置，server 的 client/tools 与 path_utils 经 get_active_config 共用此实例，
    # 避免无 MCP Roots 时 path_utils 重建第二个 config 造成双事实来源
    set_active_config(config)

    # 初始化日志系统并打印启动信息
    setup_logging(
        config.log_level,
        config.log_file,
        force_standard_logging=True,
    )
    logger.info(
        "Seedream MCP 启动: {} (version {})",
        SERVER_NAME,
        SERVER_VERSION,
    )

    try:
        transport = _build_run_options(args)
        if transport == "streamable-http":
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
                    print(message)
                    return 1
                if not has_tls and not args.insecure_allow_non_tls:
                    message = (
                        f"安全错误：streamable-http 绑定到非回环地址 {args.host} 必须配置 TLS，"
                        "请通过 --ssl-certfile/--ssl-keyfile 提供，或在受信反向代理终结 TLS 时"
                        "显式传 --insecure-allow-non-tls，避免 Bearer 令牌明文传输被窃听。"
                    )
                    logger.error(message)
                    print(message)
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
        print(f"服务器运行失败: {format_error_for_user(exc)}")
        return 1
    finally:
        _sync_cleanup()

    return 0


# ==================== 模块执行入口 ====================

if __name__ == "__main__":
    raise SystemExit(cli_main())
