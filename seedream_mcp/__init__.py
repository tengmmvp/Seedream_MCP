"""Seedream MCP 工具包。

提供 Seedream 图像生成的 MCP 服务器与客户端封装。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, TYPE_CHECKING

from .version import __version__  # noqa: F401

__author__ = "TengMMVP"
__email__ = "tengmmvp@gmail.com"

if TYPE_CHECKING:
    from .client import SeedreamClient  # noqa: F401
    from .config import (  # noqa: F401
        SeedreamConfig,
        get_active_config,
        get_global_config,
        reload_config,
        set_active_config,
        set_config,
    )
    from .server import cli_main, mcp  # noqa: F401

# PEP 562 延迟加载表：公开属性名 -> (子模块, 属性名)，首次访问时导入并缓存。
_LAZY_EXPORTS = {
    "SeedreamClient": (".client", "SeedreamClient"),
    "SeedreamConfig": (".config", "SeedreamConfig"),
    "get_active_config": (".config", "get_active_config"),
    "get_global_config": (".config", "get_global_config"),
    "reload_config": (".config", "reload_config"),
    "set_active_config": (".config", "set_active_config"),
    "set_config": (".config", "set_config"),
    "mcp": (".server", "mcp"),
    "cli_main": (".server", "cli_main"),
}

# 公开接口声明，派生自 _LAZY_EXPORTS 的键并补充 __version__。
__all__ = ["__version__"] + list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """延迟加载公开导出，避免包导入触发重模块初始化。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """补全 dir() 结果，纳入尚未触发导入的延迟导出公开名。"""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
