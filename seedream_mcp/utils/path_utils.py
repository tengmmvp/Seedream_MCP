"""
Seedream MCP工具 - 路径处理工具

提供增强的文件路径处理功能，支持相对路径、绝对路径和路径验证。
"""

from pathlib import Path
from typing import List, Optional, Tuple, Union

from .logging import get_logger

logger = get_logger(__name__)

# 支持的图片格式
SUPPORTED_IMAGE_EXTENSIONS = {
    '.bmp', '.gif', '.jpeg', '.png', '.tiff', '.webp'
}


def normalize_path(path: str, base_dir: Optional[str] = None) -> Path:
    """
    标准化文件路径
    
    Args:
        path: 输入路径（相对或绝对）
        base_dir: 基础目录，用于解析相对路径
    
    Returns:
        标准化的Path对象
    """
    try:
        path_obj = Path(path)
        
        # 如果是绝对路径，直接返回
        if path_obj.is_absolute():
            return path_obj.resolve()
        
        # 如果是相对路径，基于base_dir或当前工作目录解析
        if base_dir:
            base_path = Path(base_dir)
            return (base_path / path_obj).resolve()
        else:
            return path_obj.resolve()
            
    except Exception as e:
        logger.error(f"路径标准化失败 {path}: {e}")
        raise ValueError(f"无效的路径格式: {path}")


def validate_image_path(path: str, base_dir: Optional[str] = None) -> Tuple[bool, str, Optional[Path]]:
    """
    验证图片文件路径
    
    Args:
        path: 图片文件路径
        base_dir: 基础目录
    
    Returns:
        (是否有效, 错误信息, 标准化路径)
    """
    try:
        # 如果是URL，直接返回有效
        if path.startswith(("http://", "https://")):
            return True, "", None
        
        # 标准化路径
        normalized_path = normalize_path(path, base_dir)
        
        # 检查文件是否存在
        if not normalized_path.exists():
            return False, f"文件不存在: {normalized_path}", normalized_path
        
        # 检查是否为文件
        if not normalized_path.is_file():
            return False, f"路径不是文件: {normalized_path}", normalized_path
        
        # 检查文件扩展名
        if normalized_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            return False, f"不支持的图片格式: {normalized_path.suffix}", normalized_path
        
        # 检查文件大小（限制为50MB）
        file_size = normalized_path.stat().st_size
        max_size = 50 * 1024 * 1024  # 50MB
        if file_size > max_size:
            return False, f"文件过大: {file_size / (1024*1024):.1f}MB (最大50MB)", normalized_path
        
        return True, "", normalized_path
        
    except Exception as e:
        logger.error(f"路径验证失败 {path}: {e}")
        return False, f"路径验证错误: {str(e)}", None


def validate_image_paths(paths: List[str], base_dir: Optional[str] = None) -> Tuple[bool, List[str], List[Optional[Path]]]:
    """
    批量验证图片文件路径
    
    Args:
        paths: 图片文件路径列表
        base_dir: 基础目录
    
    Returns:
        (是否全部有效, 错误信息列表, 标准化路径列表)
    """
    errors = []
    normalized_paths = []
    all_valid = True
    
    for i, path in enumerate(paths):
        is_valid, error_msg, normalized_path = validate_image_path(path, base_dir)
        
        if not is_valid:
            all_valid = False
            errors.append(f"路径 {i+1}: {error_msg}")
        else:
            errors.append("")
        
        normalized_paths.append(normalized_path)
    
    return all_valid, errors, normalized_paths


def get_relative_path(path: Union[str, Path], base_dir: Optional[str] = None) -> str:
    """
    获取相对路径
    
    Args:
        path: 文件路径
        base_dir: 基础目录，默认为当前工作目录
    
    Returns:
        相对路径字符串
    """
    try:
        path_obj = Path(path)
        base_path = Path(base_dir) if base_dir else Path.cwd()
        
        # 尝试获取相对路径
        try:
            relative_path = path_obj.relative_to(base_path)
            return str(relative_path)
        except ValueError:
            # 如果无法获取相对路径，返回绝对路径
            return str(path_obj.resolve())
            
    except Exception as e:
        logger.error(f"获取相对路径失败 {path}: {e}")
        return str(path)


def find_images_in_directory(
    directory: str,
    recursive: bool = True,
    max_depth: int = 3,
    extensions: Optional[List[str]] = None
) -> List[Path]:
    """
    在目录中查找图片文件
    
    Args:
        directory: 搜索目录
        recursive: 是否递归搜索
        max_depth: 最大搜索深度
        extensions: 指定的文件扩展名列表
    
    Returns:
        找到的图片文件路径列表
    """
    images = []
    
    try:
        dir_path = Path(directory).resolve()
        
        if not dir_path.exists() or not dir_path.is_dir():
            logger.warning(f"目录不存在或不是目录: {directory}")
            return images
        
        # 使用指定的扩展名或默认支持的扩展名
        target_extensions = set(extensions) if extensions else SUPPORTED_IMAGE_EXTENSIONS
        target_extensions = {ext.lower() for ext in target_extensions}
        
        def scan_directory(path: Path, current_depth: int = 0):
            if current_depth > max_depth:
                return
            
            try:
                for item in path.iterdir():
                    if item.is_file() and item.suffix.lower() in target_extensions:
                        images.append(item)
                    elif item.is_dir() and recursive and current_depth < max_depth:
                        scan_directory(item, current_depth + 1)
            except (PermissionError, OSError) as e:
                logger.warning(f"无法访问目录 {path}: {e}")
        
        scan_directory(dir_path)
        
    except Exception as e:
        logger.error(f"搜索图片文件失败 {directory}: {e}")
    
    return sorted(images)


def get_file_info(path: Union[str, Path]) -> dict:
    """
    获取文件信息
    
    Args:
        path: 文件路径
    
    Returns:
        包含文件信息的字典
    """
    try:
        path_obj = Path(path)
        
        if not path_obj.exists():
            return {"error": "文件不存在"}
        
        stat = path_obj.stat()
        
        return {
            "name": path_obj.name,
            "path": str(path_obj.resolve()),
            "relative_path": get_relative_path(path_obj),
            "size": stat.st_size,
            "size_str": _format_file_size(stat.st_size),
            "extension": path_obj.suffix.lower(),
            "modified": stat.st_mtime,
            "is_image": path_obj.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        }
        
    except Exception as e:
        logger.error(f"获取文件信息失败 {path}: {e}")
        return {"error": f"获取文件信息失败: {str(e)}"}


def _format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def suggest_similar_paths(target_path: str, search_dirs: List[str] = None) -> List[str]:
    """
    建议相似的文件路径
    
    Args:
        target_path: 目标路径
        search_dirs: 搜索目录列表
    
    Returns:
        相似路径建议列表
    """
    suggestions = []
    
    try:
        target_name = Path(target_path).name.lower()
        search_directories = search_dirs or ["."]
        
        for search_dir in search_directories:
            images = find_images_in_directory(search_dir, recursive=True, max_depth=2)
            
            for image_path in images:
                if target_name in image_path.name.lower():
                    suggestions.append(str(image_path))
                    
                if len(suggestions) >= 5:  # 限制建议数量
                    break
            
            if len(suggestions) >= 5:
                break
                
    except Exception as e:
        logger.error(f"生成路径建议失败: {e}")
    
    return suggestions