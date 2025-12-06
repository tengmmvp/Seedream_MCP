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

# ============================================================================
# 异常处理模块
# ============================================================================
from .errors import SeedreamMCPError, SeedreamConfigError, SeedreamAPIError

# ============================================================================
# 数据验证模块
# ============================================================================
from .validation import validate_prompt, validate_image_url, validate_size

# ============================================================================
# 日志管理模块
# ============================================================================
from .logging import setup_logging

# ============================================================================
# 文件管理模块
# ============================================================================
from .download_manager import DownloadManager, DownloadError
from .file_manager import FileManager, FileManagerError
from .auto_save import AutoSaveManager, AutoSaveResult, AutoSaveError

# ============================================================================
# 路径处理模块
# ============================================================================
from .path_utils import (
    normalize_path,
    validate_image_path,
    validate_image_paths,
    get_relative_path,
    find_images_in_directory,
    get_file_info,
    suggest_similar_paths,
)

# ============================================================================
# 用户指引模块
# ============================================================================
from .user_guide import (
    get_path_usage_guide,
    get_error_solutions,
    format_error_message,
    get_quick_tips,
    validate_and_suggest_path,
)

# ============================================================================
# 公开接口定义
# ============================================================================
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
    "validate_image_paths",
    "get_relative_path",
    "find_images_in_directory",
    "get_file_info",
    "suggest_similar_paths",
    # 用户指引工具
    "get_path_usage_guide",
    "get_error_solutions",
    "format_error_message",
    "get_quick_tips",
    "validate_and_suggest_path",
]
