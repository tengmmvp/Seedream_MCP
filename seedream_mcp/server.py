#!/usr/bin/env python3
"""
Seedream MCP工具 - 服务器模块

实现MCP协议服务器，处理工具调用请求。
提供完整的AI图像生成能力，包括文生图、图生图、多图融合等功能。
"""

# ==================== 标准库导入 ====================
import asyncio
import argparse
import logging
import os
from typing import List, Optional

# ==================== 第三方库导入 ====================
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ==================== 本地模块导入 ====================
from .client import SeedreamClient
from .config import (
    SeedreamConfig,
    set_config,
    get_global_config,
    _parse_bool,
    _parse_int,
)
from .tools import (
    browse_images_tool,
    image_to_image_tool,
    multi_image_fusion_tool,
    sequential_generation_tool,
    text_to_image_tool,
)
from .tools.browse_images import handle_browse_images
from .tools.image_to_image import handle_image_to_image
from .tools.multi_image_fusion import handle_multi_image_fusion
from .tools.sequential_generation import handle_sequential_generation
from .tools.text_to_image import handle_text_to_image
from .utils.errors import SeedreamMCPError
from .utils.logging import setup_logging


class NotificationOptions:
    """通知选项配置类
    
    用于配置MCP服务器的通知行为，控制工具、提示和资源变更时是否发送通知。
    
    Attributes:
        tools_changed: 工具列表变更时是否发送通知
        prompts_changed: 提示列表变更时是否发送通知
        resources_changed: 资源列表变更时是否发送通知
    """

    def __init__(self):
        """初始化通知选项
        
        所有通知选项默认关闭，确保服务器仅在必要时发送通知。
        """
        self.tools_changed = False
        self.prompts_changed = False
        self.resources_changed = False


class SeedreamMCPServer:
    """Seedream MCP服务器类
    
    提供MCP协议服务器实现，负责处理工具调用请求，管理配置和客户端实例。
    支持文生图、图生图、多图融合、序列生成等AI图像生成功能。
    
    Attributes:
        server: MCP服务器实例
        config: Seedream配置对象
        client: Seedream API客户端实例
        logger: 日志记录器
        tools: 可用工具列表
    """

    def __init__(self):
        """初始化MCP服务器实例
        
        创建Server实例，初始化配置、客户端和日志记录器，并注册协议处理器。
        配置和客户端将在首次工具调用时延迟初始化。
        """
        self.server = Server("seedream-mcp")
        self.config: Optional[SeedreamConfig] = None
        self.client: Optional[SeedreamClient] = None
        self.logger = logging.getLogger(__name__)
        self.tools = self._get_tools()
        self._register_handlers()

    def _get_tools(self) -> List[Tool]:
        """获取可用工具列表
        
        注册所有支持的图像生成工具，包括浏览、文生图、图生图、多图融合和序列生成。
        
        Returns:
            包含所有注册工具定义的列表
        """
        return [
            browse_images_tool,
            text_to_image_tool,
            image_to_image_tool,
            multi_image_fusion_tool,
            sequential_generation_tool,
        ]

    def _register_handlers(self):
        """注册MCP协议处理器
        
        注册list_tools和call_tool处理器，用于响应工具列表查询和工具调用请求。
        处理器采用装饰器模式注册到服务器实例。
        """

        @self.server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            """列出所有可用工具
            
            响应客户端的工具列表查询请求，返回服务器支持的所有工具定义。
            
            Returns:
                工具定义列表，包含每个工具的名称、描述和参数结构
            """
            return self.tools

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: dict) -> list:
            """处理工具调用请求
            
            根据工具名称路由到对应处理器，执行工具调用并返回结果。
            支持自动初始化客户端、参数验证和异常处理。
            
            Args:
                name: 工具名称，用于路由到具体处理器
                arguments: 工具参数字典，包含工具执行所需的所有参数
            
            Returns:
                包含工具执行结果的内容列表，每个元素需包含type属性
            
            Raises:
                SeedreamMCPError: 工具未知或执行失败时抛出
            """
            try:
                # 延迟初始化客户端和配置
                if not self.config or not self.client:
                    await self._initialize_client()

                tool_name = name
                arguments = arguments or {}

                self.logger.info(f"调用工具: {tool_name}, 参数: {arguments}")

                # 根据工具名称路由到对应处理器
                if tool_name == "seedream_browse_images":
                    content = await handle_browse_images(arguments)
                elif tool_name == "seedream_text_to_image":
                    content = await handle_text_to_image(arguments)
                elif tool_name == "seedream_image_to_image":
                    content = await handle_image_to_image(arguments)
                elif tool_name == "seedream_multi_image_fusion":
                    content = await handle_multi_image_fusion(arguments)
                elif tool_name == "seedream_sequential_generation":
                    content = await handle_sequential_generation(arguments)
                else:
                    raise SeedreamMCPError(f"未知的工具: {tool_name}")

                # 验证返回内容格式是否符合MCP协议要求
                self.logger.debug(f"工具返回的原始content: {content}, 类型: {type(content)}")

                if content is None:
                    self.logger.error("工具返回的content为None")
                    raise SeedreamMCPError("工具返回格式错误: content不能为None")

                if not isinstance(content, list):
                    self.logger.error(
                        f"工具返回的content不是列表类型: {type(content)}, 值: {content}"
                    )
                    raise SeedreamMCPError(f"工具返回格式错误: 期望列表，得到 {type(content)}")

                # 验证列表中每个元素必须包含type属性
                for i, item in enumerate(content):
                    if not hasattr(item, "type"):
                        self.logger.error(f"content[{i}]缺少type属性: {item}")
                        raise SeedreamMCPError(f"content[{i}]格式错误: 缺少type属性")

                    if not isinstance(item.type, str):
                        self.logger.error(
                            f"content[{i}]的type属性无效: {getattr(item, 'type', 'MISSING')}"
                        )
                        raise SeedreamMCPError(f"content[{i}]格式错误: type属性必须是字符串")

                self.logger.debug(f"工具返回的content验证通过，直接返回: {content}")

                return content

            except Exception as e:
                self.logger.error(f"工具调用失败: {e}", exc_info=True)
                return [TextContent(type="text", text=f"工具调用失败: {str(e)}")]

    async def _initialize_client(self):
        """初始化配置和客户端
        
        从环境变量和全局配置加载设置，创建Seedream API客户端实例。
        该方法在首次工具调用时自动触发，实现延迟初始化。
        
        Raises:
            SeedreamMCPError: 客户端初始化失败时抛出，可能由于配置错误或网络问题
        """
        try:
            self.config = get_global_config()
            self.client = SeedreamClient(self.config)
            self.logger.info("Seedream客户端初始化成功")
        except Exception as e:
            self.logger.error(f"客户端初始化失败: {e}")
            raise SeedreamMCPError(f"客户端初始化失败: {e}")

    async def run(self):
        """运行MCP服务器
        
        启动服务器主循环，初始化日志系统和客户端，通过stdio协议处理请求。
        支持优雅关闭和异常捕获。
        
        Raises:
            Exception: 服务器运行过程中发生的未捕获异常
        """
        try:
            # 初始化日志系统
            setup_logging(os.getenv("LOG_LEVEL", "INFO"), os.getenv("LOG_FILE"))
            self.logger.info("启动Seedream MCP服务器...")

            # 初始化客户端连接
            await self._initialize_client()

            # 启动stdio服务器并运行主循环
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="seedream-mcp",
                        server_version="1.0.0",
                        capabilities=self.server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                    ),
                )
        except KeyboardInterrupt:
            self.logger.info("收到中断信号，正在关闭服务器...")
        except Exception as e:
            self.logger.error(f"服务器运行错误: {e}", exc_info=True)
            raise


async def main():
    """主入口函数
    
    创建SeedreamMCPServer实例并启动服务器。
    用于程序化调用或异步上下文中启动服务器。
    """
    server = SeedreamMCPServer()
    await server.run()


def cli_main():
    """命令行入口点

    提供同步的命令行入口点，用于console_scripts安装和直接调用。
    支持通过命令行参数传递关键配置，通过环境变量传递扩展配置。
    
    配置优先级:
        1. 命令行参数: API密钥、模型、尺寸等关键配置
        2. 环境变量: 超时、重试、自动保存等扩展配置
        3. 默认值: 所有参数都有合理的默认值
    
    Returns:
        退出码，0表示成功，1表示失败
    """
    # 构建命令行参数解析器
    parser = argparse.ArgumentParser(
        description="Seedream MCP 服务器 - AI 图像生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 使用环境变量
  export ARK_API_KEY=your_key_here
  seedream-mcp

  # 直接传递 API 密钥
  seedream-mcp --api-key your_key_here

  # 指定配置文件
  seedream-mcp --config-file ./config.env

  # 设置图像默认大小
  seedream-mcp --api-key your_key_here --default-size 4K

  # 完整配置
  seedream-mcp \
    --api-key your_key_here \
    --model doubao-seedream-4.5 \
    --default-size 2K \
    --log-level DEBUG
        """
    )

    # API 密钥参数 (必需)
    parser.add_argument(
        "--api-key",
        help="火山引擎 API 密钥 (也可通过 ARK_API_KEY 环境变量设置)"
    )

    # 配置文件参数 (可选，用于加载扩展配置)
    parser.add_argument(
        "--config-file",
        help="配置文件路径 (.env 格式)"
    )

    # 模型选择参数 (可选，默认使用最新版本)
    parser.add_argument(
        "--model",
        choices=["doubao-seedream-4.5", "doubao-seedream-4.0"],
        default="doubao-seedream-4.5",
        help="选择模型 (默认: doubao-seedream-4.5)"
    )

    # 默认图像尺寸参数 (可选)
    parser.add_argument(
        "--default-size",
        choices=["1K", "2K", "4K"],
        default="2K",
        help="默认图像尺寸 (默认: 2K)"
    )

    # 水印参数 (可选，默认关闭)
    parser.add_argument(
        "--watermark",
        action="store_true",
        default=False,
        help="启用默认水印"
    )

    # 日志级别参数 (可选)
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志级别 (默认: INFO)"
    )

    # API 基础 URL 参数 (可选，通常无需修改)
    parser.add_argument(
        "--base-url",
        default="https://ark.cn-beijing.volces.com/api/v3",
        help="API 基础 URL"
    )

    # 解析命令行参数
    args = parser.parse_args()

    # 初始化日志系统，使用CLI日志级别和ENV日志文件
    setup_logging(args.log_level, os.getenv("LOG_FILE"))

    logger = logging.getLogger(__name__)

    # 如果指定了配置文件，加载扩展配置
    if args.config_file:
        from dotenv import load_dotenv
        load_dotenv(args.config_file)

    # 配置模式校验：ENV中的关键CLI键将被忽略，避免配置冲突
    forbidden_env_keys = [
        "ARK_API_KEY",
        "SEEDREAM_MODEL_ID",
        "SEEDREAM_DEFAULT_SIZE",
        "SEEDREAM_DEFAULT_WATERMARK",
        "LOG_LEVEL",
        "ARK_BASE_URL",
    ]
    for k in forbidden_env_keys:
        v = os.getenv(k)
        if v:
            logger.warning(f"环境变量 {k}='{v}' 将被忽略，关键配置仅来自命令行")
    
    # API密钥必须通过命令行提供
    if not args.api_key:
        raise SystemExit("配置错误: 必须通过命令行提供 --api-key")

    # 构建最终配置对象
    if True:
        # 模型名称映射到实际模型ID
        model_map = {
            "doubao-seedream-4.5": "doubao-seedream-4-5-251128",
            "doubao-seedream-4.0": "doubao-seedream-4-0-250828",
        }
        model_id = model_map.get(args.model, "doubao-seedream-4-5-251128")
        
        # 创建完整配置对象
        cfg = SeedreamConfig(
            # 关键配置 (来自CLI参数)
            api_key=args.api_key,
            base_url=args.base_url,
            model_id=model_id,
            default_size=args.default_size,
            default_watermark=bool(args.watermark),
            
            # 扩展配置 (来自ENV环境变量，带默认值)
            timeout=_parse_int(os.getenv("SEEDREAM_TIMEOUT", "60")),
            api_timeout=_parse_int(os.getenv("SEEDREAM_API_TIMEOUT", "60")),
            max_retries=_parse_int(os.getenv("SEEDREAM_MAX_RETRIES", "3")),
            log_level=args.log_level,
            log_file=os.getenv("LOG_FILE"),
            
            # 自动保存配置
            auto_save_enabled=_parse_bool(os.getenv("SEEDREAM_AUTO_SAVE_ENABLED", "true")),
            auto_save_base_dir=os.getenv("SEEDREAM_AUTO_SAVE_BASE_DIR"),
            auto_save_download_timeout=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT", "30")),
            auto_save_max_retries=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_MAX_RETRIES", "3")),
            auto_save_max_file_size=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE", str(50 * 1024 * 1024))),
            auto_save_max_concurrent=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_MAX_CONCURRENT", "5")),
            auto_save_date_folder=_parse_bool(os.getenv("SEEDREAM_AUTO_SAVE_DATE_FOLDER", "true")),
            auto_save_cleanup_days=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_CLEANUP_DAYS", "30")),
        )
        
        # 设置为全局配置
        set_config(cfg)

    # 启动服务器主循环
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ 服务器已停止")
        return 0
    except Exception as e:
        print(f"❌ 服务器启动失败: {e}")
        return 1


if __name__ == "__main__":
    exit(cli_main())
