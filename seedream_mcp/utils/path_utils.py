"""
Seedream MCP工具 - 路径处理工具
"""

# 标准库导入
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlparse
from urllib.request import url2pathname

# 本地导入
from .errors import SeedreamValidationError
from .logging import get_logger
from .validation import (
    SUPPORTED_IMAGE_EXTENSIONS as VALIDATION_SUPPORTED_IMAGE_EXTENSIONS,
    validate_image_url,
)

logger = get_logger(__name__)

# 校验允许的图片格式与工作区 Roots 上下文变量
SUPPORTED_IMAGE_EXTENSIONS = set(VALIDATION_SUPPORTED_IMAGE_EXTENSIONS)
_WORKSPACE_ROOTS_VAR: ContextVar[tuple[Path, ...] | None] = ContextVar(
    "seedream_workspace_roots",
    default=None,
)


# ==================== 工作区根目录管理 ====================


def _resolve_env_workspace_root() -> Path:
    """
    从环境变量解析工作区根目录，失败时回退当前工作目录。
    """
    configured_root = os.getenv("SEEDREAM_WORKSPACE_ROOT")
    if configured_root and configured_root.strip():
        try:
            return Path(configured_root).expanduser().resolve()
        except Exception as e:
            logger.warning(f"无效的 SEEDREAM_WORKSPACE_ROOT 配置: {configured_root} ({e})")
    return Path.cwd().resolve()


def get_workspace_roots() -> List[Path]:
    """
    获取当前请求生效的工作区根目录列表。

    优先使用 MCP Roots（请求级上下文变量），无 Roots 时回退环境变量配置。
    """
    roots_from_context = _WORKSPACE_ROOTS_VAR.get()
    if roots_from_context is not None:
        return list(roots_from_context)
    return [_resolve_env_workspace_root()]


def get_workspace_root() -> Path:
    """
    获取当前请求默认工作区根目录（Roots 首项或环境变量目录）。
    """
    workspace_roots = get_workspace_roots()
    if not workspace_roots:
        raise ValueError("当前 MCP 会话未授权任何工作区目录")
    return workspace_roots[0]


async def _resolve_workspace_roots_from_context(ctx: Any) -> List[Path]:
    """
    从 MCP 上下文读取客户端 Roots，并转换为本地路径列表。
    """
    if ctx is None:
        return []

    session = getattr(ctx, "session", None)
    list_roots = getattr(session, "list_roots", None)
    if session is None or not callable(list_roots):
        return []

    roots_result = await list_roots()

    resolved_roots: List[Path] = []
    for root in getattr(roots_result, "roots", []):
        uri_value = getattr(root, "uri", None)
        if uri_value is None:
            continue
        resolved_path = _file_uri_to_path(str(uri_value))
        if resolved_path is None:
            continue
        if resolved_path in resolved_roots:
            continue
        resolved_roots.append(resolved_path)

    return resolved_roots


@asynccontextmanager
async def workspace_roots_scope(ctx: Any) -> AsyncIterator[List[Path]]:
    """
    在当前请求作用域内绑定 MCP Roots（若可用），退出时自动恢复。
    """
    token: Token[tuple[Path, ...] | None] | None = None
    resolved_roots: List[Path] = []

    session = getattr(ctx, "session", None)
    list_roots = getattr(session, "list_roots", None)
    roots_supported = session is not None and callable(list_roots)

    if roots_supported:
        try:
            resolved_roots = await _resolve_workspace_roots_from_context(ctx)
        except Exception as exc:
            logger.debug(f"读取 MCP Roots 失败，回退环境变量边界: {exc}")
        else:
            token = _WORKSPACE_ROOTS_VAR.set(tuple(resolved_roots))
            if resolved_roots:
                logger.debug(f"已应用 MCP Roots 边界: {resolved_roots}")
            else:
                logger.debug("MCP Roots 为空，当前请求按无本地目录权限处理")

    try:
        yield resolved_roots
    finally:
        if token is not None:
            _WORKSPACE_ROOTS_VAR.reset(token)


# ==================== 路径验证和规范化 ====================


def is_path_within_base(path: Path, base_dir: Path) -> bool:
    """
    判断路径是否位于基础目录内。
    """
    try:
        path.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def is_path_within_any_base(path: Path, base_dirs: Sequence[Path]) -> bool:
    """
    判断路径是否位于任一基础目录内。
    """
    return any(is_path_within_base(path, base_dir) for base_dir in base_dirs)


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


# ==================== 图片路径验证 ====================


def validate_image_path(
    path: str, base_dir: Optional[str] = None
) -> Tuple[bool, str, Optional[Path]]:
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

        # 校验路径是否在受控目录内
        if base_dir:
            base_path = Path(base_dir).resolve()
            if not is_path_within_base(normalized_path, base_path):
                return False, f"路径超出允许目录: {base_path}", normalized_path

        # 委托 validation 模块执行统一规则校验
        try:
            validated_path = validate_image_url(str(normalized_path))
            return True, "", Path(validated_path)
        except SeedreamValidationError as e:
            return False, e.message, normalized_path

    except Exception as e:
        logger.error(f"路径验证失败 {path}: {e}")
        return False, f"路径验证错误: {str(e)}", None


def validate_image_paths(
    paths: List[str], base_dir: Optional[str] = None
) -> Tuple[bool, List[str], List[Optional[Path]]]:
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


# ==================== 文件信息和查找 ====================


def find_images_in_directory(
    directory: str,
    recursive: bool = True,
    max_depth: int = 3,
    extensions: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[Path]:
    """
    在目录中查找图片文件

    Args:
        directory: 搜索目录
        recursive: 是否递归搜索
        max_depth: 最大搜索深度
        extensions: 指定的文件扩展名列表
        limit: 返回数量上限，达到后提前停止扫描

    Returns:
        找到的图片文件路径列表
    """
    images: list[Path] = []

    try:
        dir_path = Path(directory).resolve()

        if not dir_path.exists() or not dir_path.is_dir():
            logger.warning(f"目录不存在或不是目录: {directory}")
            return images

        # 使用指定的扩展名或默认支持的扩展名
        target_extensions = set(extensions) if extensions else SUPPORTED_IMAGE_EXTENSIONS
        target_extensions = {ext.lower() for ext in target_extensions}

        def scan_directory(path: Path, current_depth: int = 0) -> None:
            if current_depth > max_depth:
                return

            try:
                for item in path.iterdir():
                    if limit is not None and len(images) >= limit:
                        return
                    if item.is_file() and item.suffix.lower() in target_extensions:
                        images.append(item)
                        if limit is not None and len(images) >= limit:
                            return
                    elif item.is_dir() and recursive and current_depth < max_depth:
                        scan_directory(item, current_depth + 1)
                        if limit is not None and len(images) >= limit:
                            return
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
            "is_image": path_obj.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS,
        }

    except Exception as e:
        logger.error(f"获取文件信息失败 {path}: {e}")
        return {"error": f"获取文件信息失败: {str(e)}"}


def suggest_similar_paths(target_path: str, search_dirs: Optional[List[str]] = None) -> List[str]:
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


# ==================== 辅助函数 ====================


def _file_uri_to_path(uri: str) -> Optional[Path]:
    """
    将 file:// URI 转换为本地路径。
    """
    try:
        parsed = urlparse(uri)
    except Exception:
        return None

    if (parsed.scheme or "").lower() != "file":
        return None

    path_part = url2pathname(parsed.path or "")
    netloc = parsed.netloc or ""
    if netloc and netloc.lower() != "localhost":
        path_part = f"//{netloc}{path_part}"

    if not path_part:
        return None

    try:
        return Path(path_part).expanduser().resolve()
    except Exception:
        return None


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
