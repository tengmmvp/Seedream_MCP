"""
Seedream MCP工具 - 工具函数模块

本模块提供Seedream MCP服务所需的核心工具函数，包括：
- 异常处理：自定义异常类型定义
- 数据验证：参数校验与格式验证
- 日志管理：日志系统配置
- 文件管理：下载、存储、自动保存
- 路径处理：路径规范化、验证与搜索
- 用户指引：错误提示、使用指南
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# 公开接口声明
__all__ = [
    # 异常类型
    "SeedreamMCPError",
    "SeedreamConfigError",
    "SeedreamAPIError",
    # 数据验证函数
    "validate_prompt",
    "validate_image_url",
    "validate_size",
    # 日志配置
    "setup_logging",
    # 下载管理
    "DownloadManager",
    "DownloadError",
    # 文件管理
    "FileManager",
    "FileManagerError",
    # 自动保存
    "AutoSaveManager",
    "AutoSaveResult",
    "AutoSaveError",
    # 路径处理工具
    "normalize_path",
    "validate_image_path",
    "get_relative_path",
    "find_images_in_directory",
    "suggest_similar_paths",
    # 用户指引工具
    "get_path_usage_guide",
    "get_error_solutions",
    "format_error_message",
    "get_quick_tips",
    "validate_and_suggest_path",
]

# 延迟加载映射：导出名 -> (子模块相对名, 子模块内属性名)
# 包导入不再触发 PIL/aiohttp/aiofiles 等重型依赖初始化，仅在首次访问时按需加载
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # 异常处理模块
    "SeedreamMCPError": (".errors", "SeedreamMCPError"),
    "SeedreamConfigError": (".errors", "SeedreamConfigError"),
    "SeedreamAPIError": (".errors", "SeedreamAPIError"),
    # 数据验证模块
    "validate_prompt": (".validation", "validate_prompt"),
    "validate_image_url": (".validation", "validate_image_url"),
    "validate_size": (".validation", "validate_size"),
    # 日志管理模块
    "setup_logging": (".logging", "setup_logging"),
    # 文件管理模块
    "DownloadManager": (".download_manager", "DownloadManager"),
    "DownloadError": (".download_manager", "DownloadError"),
    "FileManager": (".file_manager", "FileManager"),
    "FileManagerError": (".file_manager", "FileManagerError"),
    "AutoSaveManager": (".auto_save", "AutoSaveManager"),
    "AutoSaveResult": (".auto_save", "AutoSaveResult"),
    "AutoSaveError": (".auto_save", "AutoSaveError"),
    # 路径处理模块
    "normalize_path": (".path_utils", "normalize_path"),
    "validate_image_path": (".path_utils", "validate_image_path"),
    "get_relative_path": (".path_utils", "get_relative_path"),
    "find_images_in_directory": (".path_utils", "find_images_in_directory"),
    "suggest_similar_paths": (".path_utils", "suggest_similar_paths"),
    # 用户指引模块
    "get_path_usage_guide": (".user_guide", "get_path_usage_guide"),
    "get_error_solutions": (".user_guide", "get_error_solutions"),
    "format_error_message": (".user_guide", "format_error_message"),
    "get_quick_tips": (".user_guide", "get_quick_tips"),
    "validate_and_suggest_path": (".user_guide", "validate_and_suggest_path"),
}


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
