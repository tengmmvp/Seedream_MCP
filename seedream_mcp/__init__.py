"""
Seedream MCP 工具包

提供 Seedream 图像生成的 MCP 服务器与客户端封装，支持配置管理、
客户端调用及 MCP 服务器命令行接口。
"""

from __future__ import annotations

# 版本号
from .version import __version__

# 包元数据
__author__ = "tengmmvp"
__email__ = "tengmmvp@gmail.com"

# 客户端模块
from .client import SeedreamClient

# 配置管理模块
from .config import SeedreamConfig, get_global_config, reload_config, set_config

# 服务器模块
from .server import cli_main, mcp

# 公开接口声明
__all__ = [
    # 配置类与函数
    "SeedreamConfig",
    "get_global_config",
    "reload_config",
    "set_config",
    # 客户端类
    "SeedreamClient",
    # 服务器相关
    "mcp",
    "cli_main",
]
