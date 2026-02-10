"""
Seedream MCP 工具包

提供 Seedream 图像生成的 MCP 服务器与客户端封装，支持配置管理、
客户端调用及 MCP 服务器命令行接口。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, TYPE_CHECKING

# 版本号
from .version import __version__

# 包元数据
__author__ = "tengmmvp"
__email__ = "tengmmvp@gmail.com"

if TYPE_CHECKING:
    from .client import SeedreamClient
    from .config import SeedreamConfig, get_global_config, reload_config, set_config
    from .server import cli_main, mcp

# 公开接口声明
__all__ = [
    "__version__",
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

_LAZY_EXPORTS = {
    "SeedreamClient": (".client", "SeedreamClient"),
    "SeedreamConfig": (".config", "SeedreamConfig"),
    "get_global_config": (".config", "get_global_config"),
    "reload_config": (".config", "reload_config"),
    "set_config": (".config", "set_config"),
    "mcp": (".server", "mcp"),
    "cli_main": (".server", "cli_main"),
}


def __getattr__(name: str) -> Any:
    """
    延迟加载公开导出，避免包导入触发重模块初始化。
    """
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
