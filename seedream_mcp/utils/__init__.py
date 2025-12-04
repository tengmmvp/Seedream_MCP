"""
Seedream MCP工具 - 工具函数模块

包含参数验证、错误处理、日志配置、自动保存等工具函数。
"""

from .errors import SeedreamMCPError, SeedreamConfigError, SeedreamAPIError
from .validation import validate_prompt, validate_image_url, validate_size
from .logging import setup_logging
from .download_manager import DownloadManager, DownloadError
from .file_manager import FileManager, FileManagerError
from .auto_save import AutoSaveManager, AutoSaveResult, AutoSaveError
from .path_utils import (
    normalize_path,
    validate_image_path,
    validate_image_paths,
    get_relative_path,
    find_images_in_directory,
    get_file_info,
    suggest_similar_paths
)
from .user_guide import (
    get_path_usage_guide,
    get_error_solutions,
    format_error_message,
    get_quick_tips,
    validate_and_suggest_path
)

__all__ = [
    "SeedreamMCPError",
    "SeedreamConfigError", 
    "SeedreamAPIError",
    "validate_prompt",
    "validate_image_url",
    "validate_size",
    "setup_logging",
    "DownloadManager",
    "DownloadError",
    "FileManager", 
    "FileManagerError",
    "AutoSaveManager",
    "AutoSaveResult",
    "AutoSaveError",
    "normalize_path",
    "validate_image_path",
    "validate_image_paths",
    "get_relative_path",
    "find_images_in_directory",
    "get_file_info",
    "suggest_similar_paths",
    "get_path_usage_guide",
    "get_error_solutions",
    "format_error_message",
    "get_quick_tips",
    "validate_and_suggest_path",
]