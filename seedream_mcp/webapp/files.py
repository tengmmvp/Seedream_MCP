"""Web 操作台文件端点：缩略图与原图，均以图片目录为边界做越界防护。

路径安全为四重校验：空/绝对/含冒号（盘符与 ADS）/上跳段拒绝、扩展名白名单、
normalize_path 与 is_within_resolved 的边界比较、is_file 存在性；违规 400、
未命中 404。图片目录解析与路径校验为同步文件系统操作，整体经 asyncio.to_thread
下沉至工作线程；缩略图经落盘缓存免除重复解码，未命中解码受进程级信号量限流。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, Response

from ..utils.core.errors import SeedreamConfigError
from ..utils.core.formats import MIME_BY_EXTENSION, SUPPORTED_IMAGE_EXTENSIONS
from ..utils.images.image_thumbnail import cached_thumbnail_bytes
from ..utils.io.io_path import is_within_resolved, normalize_path, resolve_images_root
from . import _shared


def resolve_web_relative_path(rel: str, images_root: Path) -> Path:
    """把 Web 请求的相对路径解析为图片目录内的图片物理路径。

    Args:
        rel: 相对图片目录的路径字符串，来自前端 web_path 或图库返回值。
        images_root: 已 resolve 的图片目录。

    Returns:
        resolve 后落在图片目录内的常规文件路径。

    Raises:
        ValueError: 路径为空、绝对形态、任意位置含冒号（Windows 盘符与 ADS
            数据流一并覆盖）、含 ``..`` 段或扩展名不在白名单。
        FileNotFoundError: 越界或目标不是存在的常规文件。
    """
    rel = (rel or "").strip()
    if not rel:
        raise ValueError("路径不能为空")
    if Path(rel).is_absolute() or rel.startswith(("\\", "/")) or ":" in rel:
        raise ValueError("仅接受相对图片目录的路径")
    for segment in rel.replace("\\", "/").split("/"):
        if segment == "..":
            raise ValueError("路径不允许包含上跳段")
    suffix = Path(rel).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError("不支持的图片扩展名")

    resolved = normalize_path(rel, str(images_root))
    if not is_within_resolved(resolved, images_root) or not resolved.is_file():
        raise FileNotFoundError(rel)
    return resolved


async def _resolve_request_path(request: Request) -> tuple[Path, Path] | Response:
    """在工作线程完成图片目录解析与请求路径校验，错误按原状态码映射。

    Returns:
        (落在图片目录内的物理路径, 图片目录)；解析失败时为对应的 400/404 错误响应。
    """
    rel = request.query_params.get("path", "")

    def _resolve() -> tuple[Path, Path]:
        images_root = resolve_images_root()
        return resolve_web_relative_path(rel, images_root), images_root

    try:
        return await asyncio.to_thread(_resolve)
    except SeedreamConfigError as exc:
        return _shared.images_root_unavailable(exc)
    except ValueError as exc:
        return _shared.error_json("invalid_path", str(exc), 400)
    except FileNotFoundError:
        return _shared.error_json("not_found", "图片不存在", 404)


async def web_thumbnail(request: Request) -> Response:
    """缩略图端点：长边不超过 768 像素的 JPEG，经落盘缓存。

    解码失败与文件不存在分档：文件缺失 404，存在但无法生成缩略图（损坏或
    像素超限）422，监控与排障可按状态码区分。
    """
    resolved = await _resolve_request_path(request)
    if isinstance(resolved, Response):
        return resolved
    image_path, images_root = resolved

    data = await cached_thumbnail_bytes(image_path, images_root)
    if data is None:
        return _shared.error_json("thumbnail_failed", "缩略图生成失败", 422)
    return Response(content=data, media_type="image/jpeg", headers=_shared.PRIVATE_CACHE_HEADER)


async def web_image(request: Request) -> Response:
    """原图端点：以文件流返回图片目录内的图片。"""
    resolved = await _resolve_request_path(request)
    if isinstance(resolved, Response):
        return resolved
    image_path, _ = resolved

    return FileResponse(
        image_path,
        media_type=MIME_BY_EXTENSION.get(image_path.suffix.lower(), "application/octet-stream"),
        headers=_shared.PRIVATE_CACHE_HEADER,
    )
