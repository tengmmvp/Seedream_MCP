"""Seedream MCP 工具函数包。

聚合异常处理、数据验证、日志、下载、存储、自动保存与路径处理等子模块的公开接口。
采用 PEP 562 的 ``__getattr__`` 延迟加载：包导入不初始化 PIL、aiohttp、aiofiles 等
重型依赖，首次访问导出名时才按需导入对应子模块，兼顾导入性能与循环引用规避。
``__all__`` 派生自 ``_LAZY_EXPORTS`` 的键，二者天然一致，无需手动维护。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# 延迟加载映射：导出名 -> (子模块相对名, 子模块内属性名)。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # 异常处理（core）
    "SeedreamMCPError": (".core.errors", "SeedreamMCPError"),
    "SeedreamConfigError": (".core.errors", "SeedreamConfigError"),
    "SeedreamAPIError": (".core.errors", "SeedreamAPIError"),
    "SeedreamValidationError": (".core.errors", "SeedreamValidationError"),
    "SeedreamTimeoutError": (".core.errors", "SeedreamTimeoutError"),
    "SeedreamNetworkError": (".core.errors", "SeedreamNetworkError"),
    # 参数校验（core）与图像校验（images）
    "validate_prompt": (".core.validators", "validate_prompt"),
    "validate_image_input": (".images.image_validation", "validate_image_input"),
    "validate_size": (".core.validators", "validate_size"),
    # 日志管理（core）
    "setup_logging": (".core.logs", "setup_logging"),
    # 文件与下载（io）
    "DownloadManager": (".io.io_download", "DownloadManager"),
    "DownloadError": (".io.io_download", "DownloadError"),
    "FileManager": (".io.io_storage", "FileManager"),
    "FileManagerError": (".io.io_storage", "FileManagerError"),
    "AutoSaveManager": (".io.io_save", "AutoSaveManager"),
    "AutoSaveResult": (".io.io_save", "AutoSaveResult"),
    "AutoSaveError": (".io.io_save", "AutoSaveError"),
    # 路径处理（io）
    "normalize_path": (".io.io_path", "normalize_path"),
    "validate_image_path": (".images.image_validation", "validate_image_path"),
    "get_relative_path": (".io.io_path", "get_relative_path"),
    "find_images_in_directory": (".io.io_path", "find_images_in_directory"),
    "suggest_similar_paths": (".io.io_path", "suggest_similar_paths"),
}

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
