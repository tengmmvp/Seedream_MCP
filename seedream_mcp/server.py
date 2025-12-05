"""
Seedream MCP服务器主模块

基于 FastMCP 框架实现的图像生成服务，提供文生图、图生图、多图融合、
组图生成与本地图片浏览等工具能力。
"""

from __future__ import annotations

# 标准库导入
import argparse
import os

# 第三方库导入
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# 本地模块导入
from .config import SeedreamConfig, _parse_bool, _parse_int, set_config
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

# ==================== 服务器元数据常量 ====================

# 服务器标识名称
SERVER_NAME = "seedream_mcp"

# 服务器版本号
SERVER_VERSION = "1.2.0"

# 服务器功能说明
SERVER_INSTRUCTIONS = "Seedream 图像生成工具，支持文生图、图生图、多图融合、组图与图片浏览。"

# ==================== 工具注解常量 ====================

# 生成类工具的能力标注
# - readOnlyHint: 非只读操作（生成文件）
# - destructiveHint: 非破坏性操作
# - idempotentHint: 非幂等操作（每次生成结果可能不同）
# - openWorldHint: 开放世界操作（需要网络请求）
GENERATION_TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}

# 浏览类工具的能力标注
# - readOnlyHint: 只读操作（仅读取文件列表）
# - destructiveHint: 非破坏性操作
# - idempotentHint: 幂等操作（相同输入得到相同结果）
# - openWorldHint: 非开放世界操作（本地文件系统）
BROWSE_TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

# ==================== MCP 服务器实例 ====================

# 初始化 FastMCP 服务器实例
mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)

# 初始化模块日志记录器
logger = get_logger(__name__)


# ==================== MCP 工具函数定义 ====================

@mcp.tool(
    name="seedream_text_to_image",
    annotations={"title": "Seedream 文生图", **GENERATION_TOOL_ANNOTATIONS},
)
async def seedream_text_to_image(params: TextToImageInput):
    """文本生成图像工具
    
    根据用户提供的提示词生成图像，支持多种输出格式与自动保存功能。
    
    Args:
        params: 文生图参数，包含提示词、尺寸、输出格式等配置。
    
    Returns:
        生成结果，包含图像 URL 或 base64 数据，以及任务元信息。
    
    Raises:
        SeedreamAPIError: API 调用失败时抛出。
        SeedreamValidationError: 参数校验失败时抛出。
    """
    return await run_text_to_image(params)


@mcp.tool(
    name="seedream_image_to_image",
    annotations={"title": "Seedream 图生图", **GENERATION_TOOL_ANNOTATIONS},
)
async def seedream_image_to_image(params: ImageToImageInput):
    """图像生成图像工具
    
    基于输入图像与文本指令生成新图像，支持水印控制与流式输出。
    
    Args:
        params: 图生图参数，包含输入图像、提示词、强度等配置。
    
    Returns:
        生成结果，包含新图像的 URL 或 base64 数据。
    
    Raises:
        SeedreamAPIError: API 调用失败时抛出。
        SeedreamValidationError: 参数校验或图像加载失败时抛出。
    """
    return await run_image_to_image(params)


@mcp.tool(
    name="seedream_multi_image_fusion",
    annotations={"title": "Seedream 多图融合", **GENERATION_TOOL_ANNOTATIONS},
)
async def seedream_multi_image_fusion(params: MultiImageFusionInput):
    """多图融合工具
    
    将多张输入图像融合生成新作品，建议使用 2-5 张图像以获得最佳效果。
    
    Args:
        params: 多图融合参数，包含图像列表、融合提示词等配置。
    
    Returns:
        融合后的图像结果。
    
    Raises:
        SeedreamAPIError: API 调用失败时抛出。
        SeedreamValidationError: 图像数量或格式校验失败时抛出。
    """
    return await run_multi_image_fusion(params)


@mcp.tool(
    name="seedream_sequential_generation",
    annotations={"title": "Seedream 组图生成", **GENERATION_TOOL_ANNOTATIONS},
)
async def seedream_sequential_generation(params: SequentialGenerationInput):
    """连续组图生成工具
    
    批量生成多张图像，适用于剧本分镜、连续场景或组图创作任务。
    
    Args:
        params: 组图生成参数，包含提示词列表、生成数量等配置。
    
    Returns:
        包含所有生成图像的结果集合。
    
    Raises:
        SeedreamAPIError: API 调用失败时抛出。
        SeedreamValidationError: 参数校验失败时抛出。
    """
    return await run_sequential_generation(params)


@mcp.tool(
    name="seedream_browse_images",
    annotations={"title": "Seedream 图片浏览", **BROWSE_TOOL_ANNOTATIONS},
)
async def seedream_browse_images(params: BrowseImagesInput):
    """本地图片浏览工具
    
    浏览工作目录中的图片文件，便于用户选择参考图或查看已生成内容。
    
    Args:
        params: 浏览参数，包含目录路径、筛选条件等配置。
    
    Returns:
        图片文件列表及元信息。
    
    Raises:
        SeedreamValidationError: 路径不存在或无访问权限时抛出。
    """
    return await run_browse_images(params)


# ==================== 配置构建函数 ====================

def _build_config_from_args(args: argparse.Namespace) -> SeedreamConfig:
    """从命令行参数构建服务器配置对象
    
    优先级：命令行参数 > 配置文件环境变量 > 系统环境变量 > 默认值。
    
    Args:
        args: 解析后的命令行参数对象。
    
    Returns:
        构建完成的 SeedreamConfig 配置实例。
    
    Raises:
        SeedreamConfigError: 缺少必需参数（如 API 密钥）时抛出。
    """
    # 加载指定的配置文件（如有）
    if args.config_file:
        load_dotenv(args.config_file)

    # 获取 API 密钥（优先使用命令行参数）
    cli_api_key = args.api_key or os.getenv("ARK_API_KEY")
    if not cli_api_key:
        raise SeedreamConfigError("未找到 API 密钥,请使用 --api-key 或设置 ARK_API_KEY 环境变量。")

    # 模型别名映射，将简短名称映射为完整模型 ID
    model_map = {
        "doubao-seedream-4.5": "doubao-seedream-4-5-251128",
        "doubao-seedream-4.0": "doubao-seedream-4-0-250828",
    }
    model_id = model_map.get(args.model, args.model)

    # 构建配置对象
    cfg = SeedreamConfig(
        api_key=cli_api_key,
        base_url=args.base_url,
        model_id=model_id,
        default_size=args.default_size,
        default_watermark=bool(args.watermark),
        timeout=_parse_int(os.getenv("SEEDREAM_TIMEOUT", "60")),
        api_timeout=_parse_int(os.getenv("SEEDREAM_API_TIMEOUT", "60")),
        max_retries=_parse_int(os.getenv("SEEDREAM_MAX_RETRIES", "3")),
        log_level=args.log_level,
        log_file=os.getenv("LOG_FILE"),
        auto_save_enabled=_parse_bool(os.getenv("SEEDREAM_AUTO_SAVE_ENABLED", "true")),
        auto_save_base_dir=os.getenv("SEEDREAM_AUTO_SAVE_BASE_DIR"),
        auto_save_download_timeout=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT", "30")),
        auto_save_max_retries=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_MAX_RETRIES", "3")),
        auto_save_max_file_size=_parse_int(
            os.getenv("SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE", str(50 * 1024 * 1024))
        ),
        auto_save_max_concurrent=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_MAX_CONCURRENT", "5")),
        auto_save_date_folder=_parse_bool(os.getenv("SEEDREAM_AUTO_SAVE_DATE_FOLDER", "true")),
        auto_save_cleanup_days=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_CLEANUP_DAYS", "30")),
    )

    # 设置全局配置实例
    set_config(cfg)
    return cfg


def _build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器
    
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
  seedream-mcp --api-key your_key_here --default-size 4K --log-level DEBUG
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
        choices=["doubao-seedream-4.5", "doubao-seedream-4.0"],
        default="doubao-seedream-4.5",
        help="模型选择（默认 doubao-seedream-4.5）",
    )
    parser.add_argument(
        "--default-size",
        choices=["1K", "2K", "4K"],
        default="2K",
        help="默认生成尺寸（默认 2K）",
    )
    parser.add_argument(
        "--watermark",
        action="store_true",
        default=False,
        help="启用默认水印",
    )
    
    # 日志配置
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志级别",
    )
    
    # 网络配置
    parser.add_argument(
        "--base-url",
        default="https://ark.cn-beijing.volces.com/api/v3",
        help="API 基础 URL",
    )
    
    # 传输层配置
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP 传输方式（默认 stdio）",
    )

    return parser


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

    # 构建配置对象
    try:
        config = _build_config_from_args(args)
    except SeedreamConfigError as exc:
        print(f"配置错误: {exc.message}")
        return 1

    # 初始化日志系统
    setup_logging(config.log_level, config.log_file)
    logger.info(
        "Seedream MCP 启动: %s (version %s)",
        SERVER_NAME,
        SERVER_VERSION,
    )

    # 启动 MCP 服务器
    try:
        mcp.run(transport=args.transport)
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
