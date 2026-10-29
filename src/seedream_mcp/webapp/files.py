"""Web 操作台文件端点：缩略图与原图，均以图片目录为边界做越界防护。

路径安全为形态与边界校验：空/绝对/前导分隔符/上跳段拒绝、扩展名白名单、
resolve_against_base（与 browse 核心共用解析组合，盘符相对与 win32 冒号分量等
形态随 normalize_path 单源拒绝）的界内判定；存在性由各端点自拒——缩略图自留
常规文件判定归 404，原图靠打开序列同口径拒绝；违规 400、未命中 404。
图片目录解析与路径校验为同步文件系统操作，整体经 asyncio.to_thread
下沉至工作线程；缩略图经落盘缓存免除重复解码，
未命中解码受进程级信号量限流；原图以预开 no-follow 句柄为读源，
经 _responses 的共享 _NoFollowFileResponse 应答，与 meta 静态页直出同源。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

from ..utils.core.errors import SeedreamConfigError
from ..utils.core.executors import CpuOffloadPoolClosedError
from ..utils.core.formats import MIME_BY_EXTENSION, SUPPORTED_IMAGE_EXTENSIONS
from ..utils.images.thumbnail import cached_thumbnail_bytes
from ..utils.io.files import SymlinkRejectedError, open_regular_read
from ..utils.io.path import resolve_against_base, resolve_images_root
from . import _responses
from ._responses import _NoFollowFileResponse


def resolve_web_relative_path(rel: str, images_root: Path) -> Path:
    """把 Web 请求的相对路径解析为图片目录内的图片物理路径，仅做形态与边界校验。

    路径形态与扫描侧原样对齐：不去除首尾空白，外部投放的前导空白文件名按原名匹配。

    Args:
        rel: 相对图片目录的路径字符串，来自前端 web_path 或图库返回值。
        images_root: 已 resolve 的图片目录。

    Returns:
        resolve 后落在图片目录内的路径。

    Raises:
        ValueError: 路径为空、绝对或前导分隔符形态、含 ``..`` 段或扩展名不在白名单；
            盘符相对与 win32 冒号分量等形态由 normalize_path 单源拒绝，
            拒绝消息归一为本契约文案。
        FileNotFoundError: 越界。
    """
    if not rel:
        raise ValueError("路径不能为空")
    rel_path = Path(rel)
    if rel_path.is_absolute() or rel.startswith(("\\", "/")):
        raise ValueError("仅接受相对图片目录的路径")
    for segment in rel.replace("\\", "/").split("/"):
        if segment == "..":
            raise ValueError("路径不允许包含上跳段")
    suffix = rel_path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError("不支持的图片扩展名")

    try:
        resolved, within_images_root = resolve_against_base(rel, images_root)
    except ValueError as exc:
        # Web 契约固定消息，不透传 normalize_path 各分支的拒绝文案。
        raise ValueError("仅接受相对图片目录的路径") from exc
    if not within_images_root:
        raise FileNotFoundError(rel)
    return resolved


def _image_not_found() -> Response:
    """图片缺失的统一 404 响应。"""
    return _responses.endpoint_error_json(_responses.IMAGE_NOT_FOUND_INFO)


async def _resolve_request_path(request: Request) -> tuple[Path, Path] | Response:
    """在工作线程完成图片目录解析与请求路径形态/边界校验，错误按原状态码映射。

    Args:
        request: 携带 path 查询参数的 Web 请求。

    Returns:
        (落在图片目录内的物理路径, 图片目录)；解析失败时为对应的 400/404 错误响应。
    """
    rel = request.query_params.get("path", "")

    def _resolve() -> tuple[Path, Path]:
        # 解析异常经 to_thread 原生传播，沿用本函数外层的共享错误映射。
        images_root = resolve_images_root()
        return resolve_web_relative_path(rel, images_root), images_root

    try:
        return await asyncio.to_thread(_resolve)
    except SeedreamConfigError as exc:
        return _responses.endpoint_error_json(_responses.images_root_unavailable_error(exc))
    except ValueError as exc:
        return _responses.endpoint_error_json(_responses.invalid_path_error(exc))
    except FileNotFoundError:
        return _image_not_found()


async def web_thumbnail(request: Request) -> Response:
    """缩略图端点：长边不超过 768 像素的 JPEG，经落盘缓存。

    解码失败与文件不存在分档：文件缺失 404，存在但无法生成缩略图（损坏、
    像素超限、stat 瞬时失败或共享在途瞬态失败）422，CPU 卸载池关闭的退出竞态 500，
    监控与排障可按状态码区分；目录与断链等非常规文件形态由端点自留的常规文件
    判定按缺失口径归 404。
    """
    resolved = await _resolve_request_path(request)
    if isinstance(resolved, Response):
        return resolved
    image_path, images_root = resolved
    if not await asyncio.to_thread(image_path.is_file):
        return _image_not_found()

    try:
        data = await cached_thumbnail_bytes(image_path, images_root)
    except FileNotFoundError:
        # 缩略图链路中源图被后台清理删除，按缺失口径归 404。
        return _image_not_found()
    except OSError:
        # stat 瞬时失败不谎报缺失，随生成失败归 422。
        data = None
    except CpuOffloadPoolClosedError:
        # 池关闭是退出清理竞态，属服务端不可用，不并入图片缺陷的 422 口径。
        return _responses.endpoint_error_json(
            _responses.WebErrorInfo(
                "thumbnail_unavailable", "CPU 卸载线程池已关闭，缩略图不可用", 500
            )
        )
    if data is None:
        return _responses.endpoint_error_json(_responses.THUMBNAIL_FAILED_INFO)
    return Response(content=data, media_type="image/jpeg", headers=_responses.PRIVATE_CACHE_HEADER)


async def web_image(request: Request) -> Response:
    """原图端点：预开 no-follow 句柄为 FileResponse 读源，按上游语义应答。

    打开序列自带缺失、目录与符号链接的同口径拒绝，解析阶段免存在性 stat。
    """
    resolved = await _resolve_request_path(request)
    if isinstance(resolved, Response):
        return resolved
    image_path, _ = resolved

    try:
        opened = await asyncio.to_thread(open_regular_read, image_path)
    except (FileNotFoundError, SymlinkRejectedError):
        # 打开前消失与符号链接拒绝按缺失口径归 404。
        return _image_not_found()
    except OSError as exc:
        # 权限与 IO 类打开失败保留 500 诊断信号，不折叠为缺失。
        return _responses.endpoint_error_json(
            _responses.WebErrorInfo("image_open_failed", f"图片打开失败: {exc}", 500)
        )
    if opened is None:
        return _image_not_found()
    handle, stat_result = opened
    return _NoFollowFileResponse(
        handle,
        stat_result,
        media_type=MIME_BY_EXTENSION.get(image_path.suffix.lower(), "application/octet-stream"),
        headers=_responses.PRIVATE_CACHE_HEADER,
    )
