"""
Seedream MCP工具 - 图片文件浏览工具

帮助用户浏览工作区中的图片文件，获取文件路径用于图像生成。
"""

from typing import Any, Dict, List
from pathlib import Path
from mcp.types import Tool, TextContent

from ..utils.logging import get_logger

logger = get_logger(__name__)

# 支持的图片格式
SUPPORTED_IMAGE_FORMATS = {
    '.bmp', '.gif', '.jpeg', '.png', '.tiff', '.webp'
}

# 工具定义
browse_images_tool = Tool(
    name="seedream_browse_images",
    description="浏览工作区中的图片文件，获取文件路径用于图像生成",
    inputSchema={
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "要浏览的目录路径，默认为当前工作目录。支持相对路径和绝对路径",
                "default": "."
            },
            "recursive": {
                "type": "boolean",
                "description": "是否递归搜索子目录",
                "default": True
            },
            "max_depth": {
                "type": "integer",
                "description": "最大搜索深度，防止过深的目录遍历",
                "default": 3,
                "minimum": 1,
                "maximum": 10
            },
            "limit": {
                "type": "integer",
                "description": "返回的最大文件数量",
                "default": 50,
                "minimum": 1,
                "maximum": 200
            },
            "format_filter": {
                "type": "array",
                "description": "过滤特定格式的图片文件，如 ['.jpeg', '.png']",
                "items": {
                    "type": "string"
                },
                "default": []
            },
            "show_details": {
                "type": "boolean",
                "description": "是否显示文件详细信息（大小、修改时间等）",
                "default": False
            },
            "show_guide": {
                "type": "boolean",
                "description": "是否显示完整的使用指导和技巧",
                "default": False
            }
        },
        "required": []
    }
)


def get_file_size_str(size_bytes: int) -> str:
    """将文件大小转换为可读格式"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def is_image_file(file_path: Path) -> bool:
    """检查文件是否为支持的图片格式"""
    return file_path.suffix.lower() in SUPPORTED_IMAGE_FORMATS


def scan_images(
    directory: str,
    recursive: bool = True,
    max_depth: int = 3,
    limit: int = 50,
    format_filter: List[str] = None,
    current_depth: int = 0
) -> List[Dict[str, Any]]:
    """扫描目录中的图片文件"""
    images = []
    
    try:
        dir_path = Path(directory).resolve()
        
        if not dir_path.exists():
            logger.warning(f"目录不存在: {directory}")
            return images
            
        if not dir_path.is_dir():
            logger.warning(f"路径不是目录: {directory}")
            return images
            
        # 如果指定了格式过滤器，转换为小写
        if format_filter:
            format_filter = [fmt.lower() if fmt.startswith('.') else f'.{fmt.lower()}' 
                           for fmt in format_filter]
        
        # 遍历目录
        for item in dir_path.iterdir():
            if len(images) >= limit:
                break
                
            try:
                if item.is_file() and is_image_file(item):
                    # 检查格式过滤器
                    if format_filter and item.suffix.lower() not in format_filter:
                        continue
                        
                    stat = item.stat()
                    image_info = {
                        'name': item.name,
                        'path': str(item),
                        'relative_path': str(item.relative_to(Path.cwd())) if item.is_relative_to(Path.cwd()) else str(item),
                        'size': stat.st_size,
                        'size_str': get_file_size_str(stat.st_size),
                        'modified': stat.st_mtime,
                        'extension': item.suffix.lower()
                    }
                    images.append(image_info)
                    
                elif item.is_dir() and recursive and current_depth < max_depth:
                    # 递归搜索子目录
                    sub_images = scan_images(
                        str(item),
                        recursive=True,
                        max_depth=max_depth,
                        limit=limit - len(images),
                        format_filter=format_filter,
                        current_depth=current_depth + 1
                    )
                    images.extend(sub_images)
                    
            except (PermissionError, OSError) as e:
                logger.warning(f"无法访问文件/目录 {item}: {e}")
                continue
                
    except Exception as e:
        logger.error(f"扫描目录时出错 {directory}: {e}")
        
    return images


async def handle_browse_images(arguments: Dict[str, Any]) -> List[TextContent]:
    """处理图片浏览请求"""
    from ..utils.user_guide import get_path_usage_guide, get_quick_tips
    
    try:
        # 获取参数
        directory = arguments.get('directory', '.')
        recursive = arguments.get('recursive', True)
        max_depth = arguments.get('max_depth', 3)
        limit = arguments.get('limit', 50)
        format_filter = arguments.get('format_filter', [])
        show_details = arguments.get('show_details', False)
        show_guide = arguments.get('show_guide', False)
        
        logger.info(f"开始浏览图片文件: 目录={directory}, 递归={recursive}, 深度={max_depth}")
        
        # 如果请求显示使用指导
        if show_guide:
            guide = get_path_usage_guide()
            tips = get_quick_tips()
            
            result_lines = [guide, "", "🚀 快速技巧:"]
            result_lines.extend(tips)
            
            return [TextContent(
                type="text",
                text="\n".join(result_lines)
            )]
        
        # 扫描图片文件
        images = scan_images(
            directory=directory,
            recursive=recursive,
            max_depth=max_depth,
            limit=limit,
            format_filter=format_filter
        )
        
        if not images:
            no_files_message = [
                f"📁 在目录 '{directory}' 中未找到图片文件。",
                "",
                f"🔍 支持的格式: {', '.join(sorted(SUPPORTED_IMAGE_FORMATS))}",
                f"搜索设置: 递归={recursive}, 最大深度={max_depth}",
                "",
                "💡 建议:",
                "  • 检查目录路径是否正确",
                "  • 尝试启用递归搜索 (recursive=true)",
                "  • 增加搜索深度 (max_depth)",
                "  • 使用 show_guide=true 查看完整使用指导"
            ]
            
            return [TextContent(
                type="text",
                text="\n".join(no_files_message)
            )]
        
        # 构建结果文本
        result_lines = [
            f"找到 {len(images)} 个图片文件",
            f"搜索目录: {Path(directory).resolve()}",
            f"搜索设置: 递归={recursive}, 最大深度={max_depth}",
            ""
        ]
        
        if format_filter:
            result_lines.append(f"格式过滤: {', '.join(format_filter)}")
            result_lines.append("")
        
        # 按目录分组显示
        current_dir = None
        for image in sorted(images, key=lambda x: x['path']):
            image_dir = str(Path(image['path']).parent)
            
            # 显示目录标题
            if image_dir != current_dir:
                current_dir = image_dir
                result_lines.append(f"📁 {image_dir}")
                result_lines.append("-" * 50)
            
            # 显示文件信息
            if show_details:
                result_lines.append(
                    f"  📷 {image['name']} ({image['size_str']}) {image['extension']}"
                )
                result_lines.append(f"     路径: {image['relative_path']}")
            else:
                result_lines.append(f"  📷 {image['name']} - {image['relative_path']}")
            
        result_lines.extend([
            "",
            "💡 使用提示:",
            "• 复制上述路径用于图像生成工具的 image 参数",
            "• 支持相对路径和绝对路径",
            "• 多图融合工具可以使用多个路径的数组",
            "• 使用 show_guide=true 查看完整使用指导",
            "",
            "🔧 其他选项:",
            "• show_details=true: 显示文件详细信息",
            "• recursive=false: 仅搜索当前目录",
            "• limit=N: 限制显示的文件数量",
            "",
            f"支持的图片格式: {', '.join(sorted(SUPPORTED_IMAGE_FORMATS))}"
        ])
        
        logger.info(f"成功浏览图片文件，找到 {len(images)} 个文件")
        
        return [TextContent(
            type="text",
            text="\n".join(result_lines)
        )]
        
    except Exception as e:
        error_msg = f"浏览图片文件时出错: {str(e)}"
        logger.error(error_msg)
        
        error_message = [
            f"❌ {error_msg}",
            "",
            "💡 常见解决方案:",
            "  • 检查目录路径是否存在",
            "  • 确保有读取目录的权限",
            "  • 尝试使用绝对路径",
            "  • 使用 show_guide=true 查看使用指导"
        ]
        
        return [TextContent(
            type="text",
            text="\n".join(error_message)
        )]