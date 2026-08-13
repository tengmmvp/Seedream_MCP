"""Seedream MCP 路径处理工具：MCP 工作区 Roots 边界与路径越界校验。

以 MCP Roots 作为文件访问边界，对图片路径做规范化与越界校验，拦截包含 ``..``
或经由符号链接指向工作区之外的路径；无 MCP Roots 时回退 SEEDREAM_WORKSPACE_ROOT
环境变量。另提供目录图片查找与拼写相近路径建议。
"""

from __future__ import annotations

# 标准库导入
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlparse
from urllib.request import url2pathname

# 本地导入
from .errors import SeedreamConfigError, SeedreamValidationError
from .logging import get_logger
from .validation import (
    SUPPORTED_IMAGE_EXTENSIONS as VALIDATION_SUPPORTED_IMAGE_EXTENSIONS,
    validate_image_url,
)

logger = get_logger(__name__)

# 受支持的图片格式集合与工作区 Roots 上下文变量。
SUPPORTED_IMAGE_EXTENSIONS = set(VALIDATION_SUPPORTED_IMAGE_EXTENSIONS)
_WORKSPACE_ROOTS_VAR: ContextVar[tuple[Path, ...] | None] = ContextVar(
    "seedream_workspace_roots",
    default=None,
)


# ==================== 工作区根目录管理 ====================


def resolve_env_workspace_root() -> Path:
    """解析工作区根目录，失败时回退当前工作目录。

    本地开发无 MCP Roots 时作为文件访问边界回退。优先读取活动配置，config 未就绪时
    回退环境变量。
    """
    configured_root = _configured_workspace_root()
    if configured_root:
        try:
            return Path(configured_root).expanduser().resolve()
        except Exception as e:
            logger.warning("无效的工作区根目录配置 '{}': {}", configured_root, e)
    return Path.cwd().resolve()


def _configured_workspace_root() -> Optional[str]:
    """返回已配置的工作区根目录原始值，未配置返回 None。"""
    config = _safe_global_config()
    if config is not None:
        root = config.workspace_root
        return root.strip() if root else None
    env_root = os.getenv("SEEDREAM_WORKSPACE_ROOT")
    return env_root.strip() if env_root else None


def _safe_global_config() -> Any:
    """返回全局配置实例；未就绪时返回 None。"""
    from ..config import get_global_config

    try:
        return get_global_config()
    except SeedreamConfigError:
        return None


def get_workspace_roots() -> List[Path]:
    """获取当前请求生效的工作区根目录列表。

    优先使用 MCP Roots 作为文件访问边界，无 Roots 时回退环境变量配置。
    """
    roots_from_context = _WORKSPACE_ROOTS_VAR.get()
    if roots_from_context is not None:
        return list(roots_from_context)
    return [resolve_env_workspace_root()]


def get_workspace_root() -> Path:
    """获取当前请求默认工作区根目录，取 Roots 首项或环境变量目录。"""
    workspace_roots = get_workspace_roots()
    if not workspace_roots:
        raise ValueError("当前 MCP 会话未授权任何工作区目录")
    return workspace_roots[0]


async def _resolve_workspace_roots_from_context(ctx: Any) -> List[Path]:
    """从 MCP 上下文读取客户端 Roots 并转换为本地路径列表。

    将各 Root 的 file:// URI 转为本地路径，拒绝 UNC 形式以避免触发 SMB 连接。
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
    """在当前请求作用域内绑定 MCP Roots，退出时自动恢复。

    将客户端 Roots 设置到上下文变量作为该请求的文件访问边界；客户端不支持
    Roots 时回退环境变量边界。
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
            logger.debug("读取 MCP Roots 失败，回退环境变量边界: {}", exc)
        else:
            token = _WORKSPACE_ROOTS_VAR.set(tuple(resolved_roots))
            if resolved_roots:
                logger.debug("已应用 MCP Roots 边界: {}", resolved_roots)
            else:
                logger.debug("MCP Roots 为空，当前请求按无本地目录权限处理")

    try:
        yield resolved_roots
    finally:
        if token is not None:
            _WORKSPACE_ROOTS_VAR.reset(token)


# ==================== 路径验证和规范化 ====================


def _is_within_resolved(path_resolved: Path, base_resolved: Path) -> bool:
    """判断已 resolve 的路径是否位于已 resolve 的基础目录内。

    直接做 relative_to 比较，不再重复 resolve。供循环场景复用以避免重复解析。
    """
    try:
        path_resolved.relative_to(base_resolved)
        return True
    except ValueError:
        return False


def is_path_within_base(path: Path, base_dir: Path) -> bool:
    """判断路径是否位于基础目录内。

    将 path 与 base_dir 均 resolve 后比较，可拦截包含 ``..`` 或经由符号链接
    指向基础目录之外的路径。
    """
    return _is_within_resolved(path.resolve(), base_dir.resolve())


def is_path_within_any_base(path: Path, base_dirs: Sequence[Path]) -> bool:
    """判断路径是否位于任一基础目录内。

    path 仅 resolve 一次后与各 base 比较，避免对每个 base 重复解析同一 path。
    """
    resolved_path = path.resolve()
    for base_dir in base_dirs:
        if _is_within_resolved(resolved_path, base_dir.resolve()):
            return True
    return False


def normalize_path(path: str, base_dir: Optional[str] = None) -> Path:
    """标准化文件路径为绝对 Path 对象。

    Args:
        path: 输入路径，可为相对或绝对。
        base_dir: 基础目录，用于解析相对路径。

    Returns:
        标准化的 Path 对象。
    """
    try:
        path_obj = Path(path)

        if path_obj.is_absolute():
            return path_obj.resolve()

        if base_dir:
            base_path = Path(base_dir)
            return (base_path / path_obj).resolve()
        else:
            return path_obj.resolve()

    except Exception as e:
        logger.error("路径标准化失败 {}: {}", path, e)
        raise ValueError(f"无效的路径格式: {path}")


def get_relative_path(path: Union[str, Path], base_dir: Optional[str] = None) -> str:
    """获取相对路径。

    Args:
        path: 文件路径。
        base_dir: 基础目录，默认为当前工作目录。

    Returns:
        相对路径字符串。
    """
    try:
        path_obj = Path(path)
        base_path = Path(base_dir) if base_dir else Path.cwd()

        try:
            relative_path = path_obj.relative_to(base_path)
            return str(relative_path)
        except ValueError:
            # 无法取相对路径时回退绝对路径。
            return str(path_obj.resolve())

    except Exception as e:
        logger.error("获取相对路径失败 {}: {}", path, e)
        return str(path)


# ==================== 图片路径验证 ====================


def validate_image_path(
    path: str, base_dir: Optional[str] = None, skip_dimensions: bool = False
) -> Tuple[bool, str, Optional[Path]]:
    """验证图片文件路径，强制其位于工作区边界内并符合图片规则。

    HTTP(S) URL 视为有效但标准化路径恒为 None，调用方须同时检查有效位与路径是否
    为 None，据以分流 URL 与本地文件处理，不可仅凭有效位判定为本地路径。

    Args:
        path: 图片文件路径；HTTP(S) URL 有效但路径返回 None。
        base_dir: 工作区基础目录，用于越界校验。
        skip_dimensions: 是否跳过图片像素维度校验。

    Returns:
        三元组 (是否有效, 错误信息, 标准化路径);URL 有效但路径为 None。
    """
    try:
        if path.startswith(("http://", "https://")):
            return True, "", None

        normalized_path = normalize_path(path, base_dir)

        if base_dir:
            base_path = Path(base_dir).resolve()
            if not is_path_within_base(normalized_path, base_path):
                return False, "路径超出允许的工作区目录范围", normalized_path

        # 委托 validation 模块执行格式与维度等统一规则校验。
        try:
            validated_path = validate_image_url(
                str(normalized_path), skip_dimensions=skip_dimensions
            )
            return True, "", Path(validated_path)
        except SeedreamValidationError as e:
            return False, e.message, normalized_path

    except Exception as e:
        logger.error("路径验证失败 {}: {}", path, e)
        return False, f"路径验证错误: {str(e)}", None


def find_images_in_directory(
    directory: str,
    recursive: bool = True,
    max_depth: int = 3,
    extensions: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[Path]:
    """在目录中查找图片文件。

    Args:
        directory: 搜索目录。
        recursive: 是否递归搜索。
        max_depth: 最大搜索深度。
        extensions: 指定的文件扩展名列表。
        limit: 返回数量上限，<=0 时返回空列表；扫描按 normcase 稳定顺序，凑够即提前停止。

    Returns:
        找到的图片文件路径列表。
    """
    images: list[Path] = []

    # 上限为 0 或负数时直接返回空列表，不进入扫描。
    if limit is not None and limit <= 0:
        return images

    try:
        dir_path = Path(directory).resolve()

        if not dir_path.exists() or not dir_path.is_dir():
            logger.warning("目录不存在或不是目录: {}", directory)
            return images

        target_extensions = set(extensions) if extensions else SUPPORTED_IMAGE_EXTENSIONS
        target_extensions = {ext.lower() for ext in target_extensions}

        # 无上限时记为 -1 表示收集全部。
        target_count = limit if limit is not None else -1

        def scan_directory(path: Path, current_depth: int = 0) -> bool:
            """按 normcase 稳定顺序深度优先扫描；凑够 target_count 即返回 True 提前终止。"""
            if current_depth > max_depth:
                return False
            try:
                with os.scandir(path) as it:
                    entries = sorted(it, key=lambda entry: os.path.normcase(entry.path))
            except (PermissionError, OSError) as e:
                logger.warning("无法访问目录 {}: {}", path, e)
                return False

            for entry in entries:
                entry_path = Path(entry.path)
                # follow_symlinks=False：不跟随符号链接，避免符号链接环与经由符号链接越界遍历。
                if (
                    entry.is_file(follow_symlinks=False)
                    and entry_path.suffix.lower() in target_extensions
                ):
                    images.append(entry_path)
                    if target_count >= 0 and len(images) >= target_count:
                        return True
                elif (
                    entry.is_dir(follow_symlinks=False) and recursive and current_depth < max_depth
                ):
                    if scan_directory(entry_path, current_depth + 1):
                        return True
            return False

        scan_directory(dir_path)

    except Exception as e:
        logger.error("搜索图片文件失败 {}: {}", directory, e)

    return images


def suggest_similar_paths(target_path: str, search_dirs: Optional[List[str]] = None) -> List[str]:
    """在搜索目录下建议与目标路径拼写相近的图片路径。

    用于路径校验失败时给出纠错建议，缓解用户手误导致的路径错误。

    Args:
        target_path: 目标路径。
        search_dirs: 搜索目录列表，默认当前目录。

    Returns:
        相似路径建议列表，最多 5 条。
    """
    suggestions = []

    try:
        target_name = Path(target_path).name.lower()
        search_directories = search_dirs or ["."]

        for search_dir in search_directories:
            images = find_images_in_directory(search_dir, recursive=True, max_depth=2, limit=500)

            for image_path in images:
                if target_name in image_path.name.lower():
                    suggestions.append(str(image_path))

                if len(suggestions) >= 5:
                    break

            if len(suggestions) >= 5:
                break

    except Exception as e:
        logger.error("生成路径建议失败: {}", e)

    return suggestions


# ==================== 辅助函数 ====================


def _file_uri_to_path(uri: str) -> Optional[Path]:
    """将 file:// URI 转换为本地路径，拒绝 UNC 形式以避免触发 SMB 连接。"""
    try:
        parsed = urlparse(uri)
    except Exception:
        return None

    if (parsed.scheme or "").lower() != "file":
        return None

    path_part = url2pathname(parsed.path or "")
    netloc = parsed.netloc or ""
    if netloc and netloc.lower() != "localhost":
        # 拒绝 UNC 路径如 file://host/share，避免 Windows 下触发 SMB 连接泄露凭据。
        return None

    if not path_part:
        return None

    try:
        return Path(path_part).expanduser().resolve()
    except Exception:
        return None
