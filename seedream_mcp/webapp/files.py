"""Web 操作台文件端点：缩略图与原图，均以保存根为边界做越界防护。

路径安全为四重校验：空/绝对/上跳段拒绝、扩展名白名单、normalize_path 与
is_within_resolved 的边界比较、is_file 存在性；违规 400、未命中 404。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, Response

from ..config import get_active_config
from ..tools.core._helpers import _resolve_default_base_dir
from ..utils.core.errors import SeedreamValidationError
from ..utils.core.formats import MIME_BY_EXTENSION, SUPPORTED_IMAGE_EXTENSIONS
from ..utils.images.image_thumbnail import build_thumbnail_bytes
from ..utils.io.io_path import is_within_resolved, normalize_path
from . import _shared


def resolve_web_relative_path(rel: str, save_root: Path) -> Path:
    """把 Web 请求的相对路径解析为保存根内的图片物理路径。

    Args:
        rel: 相对保存根的路径字符串，来自前端 web_path 或图库返回值。
        save_root: 已 resolve 的保存根目录。

    Returns:
        resolve 后落在保存根内的常规文件路径。

    Raises:
        ValueError: 路径为空、绝对形态、含 ``..`` 段或扩展名不在白名单。
        FileNotFoundError: 越界或目标不是存在的常规文件。
    """
    rel = (rel or "").strip()
    if not rel:
        raise ValueError("路径不能为空")
    if Path(rel).is_absolute() or rel.startswith(("\\", "/")) or ":" in rel.split("\\")[0]:
        raise ValueError("仅接受相对保存根的路径")
    for segment in rel.replace("\\", "/").split("/"):
        if segment == "..":
            raise ValueError("路径不允许包含上跳段")
    suffix = Path(rel).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError("不支持的图片扩展名")

    resolved = normalize_path(rel, str(save_root))
    if not is_within_resolved(resolved, save_root) or not resolved.is_file():
        raise FileNotFoundError(rel)
    return resolved


def _resolve_path_or_error(request: Request, save_root: Path) -> Path | Response:
    """解析查询参数 path，违规返回 400/404 错误响应，成功返回物理路径。"""
    rel = request.query_params.get("path", "")
    try:
        return resolve_web_relative_path(rel, save_root)
    except ValueError as exc:
        return _shared.error_json("invalid_path", str(exc), 400)
    except FileNotFoundError:
        return _shared.error_json("not_found", "图片不存在", 404)


async def web_thumbnail(request: Request) -> Response:
    """缩略图端点：长边不超过 768 像素的 JPEG，生成失败或未命中返回 404。"""
    try:
        save_root = _resolve_default_base_dir(get_active_config())
    except SeedreamValidationError as exc:
        return _shared.save_root_unavailable(exc)
    resolved = _resolve_path_or_error(request, save_root)
    if isinstance(resolved, Response):
        return resolved

    data = await asyncio.to_thread(build_thumbnail_bytes, resolved)
    if data is None:
        return _shared.error_json("not_found", "缩略图生成失败", 404)
    return Response(content=data, media_type="image/jpeg", headers=_shared.PRIVATE_CACHE_HEADER)


async def web_image(request: Request) -> Response:
    """原图端点：以文件流返回保存根内的图片。"""
    try:
        save_root = _resolve_default_base_dir(get_active_config())
    except SeedreamValidationError as exc:
        return _shared.save_root_unavailable(exc)
    resolved = _resolve_path_or_error(request, save_root)
    if isinstance(resolved, Response):
        return resolved

    return FileResponse(
        resolved,
        media_type=MIME_BY_EXTENSION.get(resolved.suffix.lower(), "application/octet-stream"),
        headers=_shared.PRIVATE_CACHE_HEADER,
    )
