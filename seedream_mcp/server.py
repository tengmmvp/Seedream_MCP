"""
Seedream MCP服务器主模块
"""

from __future__ import annotations

# 标准库导入
import argparse
from typing import Any, Literal, cast

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import CallToolResult, ToolAnnotations

# 本地模块导入
from .config import SeedreamConfig, build_config_from_sources, get_global_config
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
from .utils.errors import SeedreamConfigError, format_error_for_user
from .utils.logging import get_logger, setup_logging
from .version import __version__

# ==================== 服务器元数据常量 ====================

# 服务器标识名称
SERVER_NAME = "seedream_mcp"

# 服务器版本号
SERVER_VERSION = __version__

# 服务器功能说明
SERVER_INSTRUCTIONS = "Seedream 图像生成工具，支持文生图、图文生图、多图融合、组图输出与图片浏览。"

# ==================== 工具注解常量 ====================

# 生成类工具的能力标注
# - readOnlyHint: 非只读操作（生成文件）
# - destructiveHint: 非破坏性操作
# - idempotentHint: 非幂等操作（每次生成结果可能不同）
# - openWorldHint: 开放世界操作（需要网络请求）
GENERATION_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

# 浏览类工具的能力标注
# - readOnlyHint: 只读操作（仅读取文件列表）
# - destructiveHint: 非破坏性操作
# - idempotentHint: 幂等操作（相同输入得到相同结果）
# - openWorldHint: 非开放世界操作（本地文件系统）
BROWSE_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

# ==================== MCP 服务器实例 ====================

# 初始化 FastMCP 服务器实例
mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)

# 初始化模块日志记录器
logger = get_logger(__name__)

# 当前服务器运行时配置
_active_config: SeedreamConfig | None = None


def _build_tool_annotations(title: str, base: ToolAnnotations) -> ToolAnnotations:
    """
    基于基础能力标注生成包含 title 的工具注解对象。
    """
    return ToolAnnotations(
        title=title,
        readOnlyHint=base.readOnlyHint,
        destructiveHint=base.destructiveHint,
        idempotentHint=base.idempotentHint,
        openWorldHint=base.openWorldHint,
    )


def _get_active_config() -> SeedreamConfig:
    """
    获取当前活动配置，优先使用 CLI 注入配置。
    """
    if _active_config is not None:
        return _active_config
    return get_global_config()


# ==================== MCP 工具函数定义 ====================


@mcp.tool(
    name="seedream_text_to_image",
    annotations=_build_tool_annotations("Seedream 文生图", GENERATION_TOOL_ANNOTATIONS),
)
async def seedream_text_to_image(
    params: TextToImageInput,
    ctx: Context[Any, Any, Any],
) -> CallToolResult:
    """
    文生图：

    通过给模型提供清晰准确的文字指令，即可快速获得符合描述的高质量单张图片。
    """
    return await run_text_to_image(params, config=_get_active_config(), ctx=ctx)


@mcp.tool(
    name="seedream_image_to_image",
    annotations=_build_tool_annotations("Seedream 图文生图", GENERATION_TOOL_ANNOTATIONS),
)
async def seedream_image_to_image(
    params: ImageToImageInput,
    ctx: Context[Any, Any, Any],
) -> CallToolResult:
    """
    图文生图：

    基于已有图片，结合文字指令进行图像编辑，包括图像元素增删、风格转化、材质替换、色调迁移、改变背景/视角/尺寸等。
    """
    return await run_image_to_image(params, config=_get_active_config(), ctx=ctx)


@mcp.tool(
    name="seedream_multi_image_fusion",
    annotations=_build_tool_annotations("Seedream 多图融合", GENERATION_TOOL_ANNOTATIONS),
)
async def seedream_multi_image_fusion(
    params: MultiImageFusionInput,
    ctx: Context[Any, Any, Any],
) -> CallToolResult:
    """
    多图融合：

    根据输入的文本描述和多张参考图片，融合它们的风格、元素等特征来生成新图像。如衣裤鞋帽与模特图融合成穿搭图，人物与风景融合为人物风景图等。
    """
    return await run_multi_image_fusion(params, config=_get_active_config(), ctx=ctx)


@mcp.tool(
    name="seedream_sequential_generation",
    annotations=_build_tool_annotations("Seedream 组图输出", GENERATION_TOOL_ANNOTATIONS),
)
async def seedream_sequential_generation(
    params: SequentialGenerationInput,
    ctx: Context[Any, Any, Any],
) -> CallToolResult:
    """
    组图输出：

    支持通过一张或者多张图片和文字信息，生成漫画分镜、品牌视觉等一组内容关联的图片。
    """
    return await run_sequential_generation(params, config=_get_active_config(), ctx=ctx)


@mcp.tool(
    name="seedream_browse_images",
    annotations=_build_tool_annotations("Seedream 图片浏览", BROWSE_TOOL_ANNOTATIONS),
)
async def seedream_browse_images(
    params: BrowseImagesInput,
    ctx: Context[Any, Any, Any],
) -> CallToolResult:
    """
    本地图片浏览：

    浏览工作目录中的图片文件，便于用户选择参考图或查看已生成内容。
    """
    return await run_browse_images(params, ctx=ctx)


def _build_config_from_args(args: argparse.Namespace) -> SeedreamConfig:
    """
    从命令行参数构建服务器配置对象

    优先级：命令行参数 > 系统环境变量 > .env 文件 > 默认值。

    Args:
        args: 解析后的命令行参数对象。

    Returns:
        构建完成的 SeedreamConfig 配置实例。

    Raises:
        SeedreamConfigError: 缺少必需参数（如 API 密钥）时抛出。
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
  seedream-mcp --api-key your_key_here
  seedream-mcp --api-key your_key_here --model doubao-seedream-4.5
  --default-size 4K --log-level DEBUG
  seedream-mcp --api-key your_key_here --config-file ./config.env
        """,
    )

    # API 认证配置
    parser.add_argument(
        "--api-key",
        help="火山引擎 API 密钥（也可通过 ARK_API_KEY 环境变量配置）",
    )
    parser.add_argument(
        "--config-file",
        help="可选的 .env 配置文件路径，用于加载额外环境变量",
    )

    # 模型与生成配置
    parser.add_argument(
        "--model",
        choices=[
            "doubao-seedream-5.0",
            "doubao-seedream-5.0-lite",
            "doubao-seedream-4.5",
            "doubao-seedream-4.0",
        ],
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
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
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
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP 传输方式（默认 stdio）",
    )
    parser.add_argument(
        "--mount-path",
        default=None,
        help="SSE 传输的挂载路径（如 /mcp），仅在 --transport=sse 时生效",
    )

    return parser


def _build_run_options(
    args: argparse.Namespace,
) -> tuple[Literal["stdio", "sse", "streamable-http"], str | None]:
    """
    构建 MCP 运行参数。

    当前 SDK 的 `mount_path` 仅在 SSE 传输下生效，其他传输模式统一忽略。
    """
    transport = cast(Literal["stdio", "sse", "streamable-http"], args.transport)
    mount_path = args.mount_path if transport == "sse" else None
    return transport, mount_path


# ==================== 主入口函数 ====================


def cli_main() -> int:
    """命令行主入口函数

    负责参数解析、配置构建、日志初始化与服务器启动。

    Returns:
        进程退出码：
        - 0: 正常退出
        - 1: 配置错误或运行异常
    """
    # 解析命令行参数
    parser = _build_arg_parser()
    args = parser.parse_args()

    global _active_config

    # 构建配置对象
    try:
        config = _build_config_from_args(args)
    except SeedreamConfigError as exc:
        print(f"配置错误: {exc.message}")
        return 1

    _active_config = config

    # 初始化日志系统
    setup_logging(
        config.log_level,
        config.log_file,
        force_standard_logging=True,
    )
    logger.info(
        "Seedream MCP 启动: %s (version %s)",
        SERVER_NAME,
        SERVER_VERSION,
    )

    # 启动 MCP 服务器
    try:
        transport, mount_path = _build_run_options(args)
        mcp.run(transport=transport, mount_path=mount_path)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出。")
        return 0
    except Exception as exc:
        logger.error("服务器运行异常", exc_info=True)
        print(f"服务器运行失败: {format_error_for_user(exc)}")
        return 1

    return 0


# ==================== 模块执行入口 ====================

if __name__ == "__main__":
    raise SystemExit(cli_main())
