"""Seedream MCP 工具函数包。

各子模块的符号经直接子模块路径导入消费，本包门面不再重导出任何符号；
``__getattr__``/``__dir__`` 延迟加载机制保留，供后续按需恢复公开导出。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# 延迟加载映射：导出名 -> (子模块相对名, 子模块内属性名)。
# 原有条目经全仓 grep 确认零消费后清空，仅保留机制骨架。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {}

# 公开接口，派生自 _LAZY_EXPORTS 的键。
__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """按 PEP 562 延迟加载公开导出，首次访问时才导入对应子模块并缓存到模块字典。"""
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
