"""Web 操作台图库浏览端点：图片目录边界校验后透传 browse 工具并收敛前端消费形态。

与 MCP 会话共用同一求值链与读权限判定；Web 文件端点仅服务图片目录内文件，图库
浏览同样以图片目录为界，解析出图片目录的请求目录在端点拒绝。browse 条目为绝对
路径，Web 层改写为图片目录相对形态供前端直接拼接为图片端点参数；
workspace_roots 与 resolved_directories 回显字段 Web 前端不消费，返回浏览器
前剥除。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..tools.core.schemas import BrowseImagesInput
from ..tools.runners import run_browse_images
from ..utils.core.logs import get_logger
from ..utils.io.io_path import (
    is_within_resolved,
    normalize_path,
)
from . import _shared

logger = get_logger()

# browse 工具为 MCP 客户端回显的边界字段，Web 前端不消费，不出 Web 端点。
_ROOTS_ECHO_KEYS = ("workspace_roots", "resolved_directories")


def _converge_for_web(structured: dict[str, object], images_root: Path) -> None:
    """剥除 Web 前端不消费的边界字段，条目 path 改写为图片目录相对形态。

    条目改写经 _shared.converge_path_entry 与 generate 端单点维护；browse 条目
    已是 resolve 后的绝对路径，走纯词法相对化。
    """
    for key in _ROOTS_ECHO_KEYS:
        structured.pop(key, None)
    images = structured.get("images")
    if not isinstance(images, list):
        return
    for item in images:
        if isinstance(item, dict):
            _shared.converge_path_entry(item, "path", images_root)


async def _directory_outside_images_root(directory: str, images_root: Path) -> bool:
    """解析请求目录并判定是否落在图片目录外；形态非法交 browse 核心报具体原因。"""

    def _outside() -> bool:
        try:
            resolved = normalize_path(directory, str(images_root))
        except ValueError:
            return False
        return not is_within_resolved(resolved, images_root)

    return await asyncio.to_thread(_outside)


async def web_browse(request: Request) -> Response:
    """图库浏览端点，请求体经 BrowseImagesInput 校验后透传 browse 工具。

    请求体解析与参数校验是同步 CPU 工作，与生成端点同口径下沉工作线程，
    避免超大 JSON 阻塞事件循环。
    """
    body, parse_error = await _shared.parse_json_object_body(request)
    if parse_error is not None:
        return parse_error
    try:
        params = await asyncio.to_thread(BrowseImagesInput.model_validate, body)
    except ValidationError as exc:
        return _shared.validation_error_json(exc)

    images_root = await _shared.resolve_web_images_root()
    if isinstance(images_root, JSONResponse):
        return images_root
    directory = params.effective_directory
    if await _directory_outside_images_root(directory, images_root):
        return _shared.error_json(
            "invalid_directory", "目录不在图片目录内，Web 图库仅浏览图片目录", 400
        )
    try:
        result = await run_browse_images(params, ctx=None)
    except Exception:
        logger.exception("Web 图库浏览请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)
    structured = result.structured_content if result.structured_content is not None else {}
    if isinstance(structured, dict):
        _converge_for_web(structured, images_root)
    if not result.is_error:
        return JSONResponse(structured, status_code=200)
    # validation_error 为模型可自纠的参数错误归 400，扫描失败等服务端故障归 500。
    error_type = _shared.structured_error_type(structured) if isinstance(structured, dict) else None
    status = 400 if error_type == "validation_error" else 500
    return JSONResponse(structured, status_code=status)
