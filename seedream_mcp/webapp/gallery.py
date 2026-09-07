"""Web 操作台图库浏览端点：存储区边界校验后透传 browse 工具并收敛前端消费形态。

与 MCP 会话共用同一求值链与读权限判定；Web 文件端点仅服务存储区内文件，图库
浏览同样以存储区为界，解析出存储区的请求目录在端点拒绝。browse 条目为绝对
路径，Web 层改写为存储区相对形态供前端直接拼接为图片端点参数；
workspace_roots 与 resolved_directories 回显字段 Web 前端不消费，返回浏览器
前剥除。
"""

from __future__ import annotations

import asyncio
import json
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
    save_root_relative,
)
from . import _shared

logger = get_logger()

# browse 工具为 MCP 客户端回显的边界字段，Web 前端不消费，不出 Web 端点。
_ROOTS_ECHO_KEYS = ("workspace_roots", "resolved_directories")


def _converge_for_web(structured: dict[str, object], save_root: Path) -> None:
    """剥除 Web 前端不消费的边界字段，条目 path 改写为存储区相对形态。

    Web 文件端点以存储区相对路径服务文件，前端拼接依赖相对形态；存储区外
    条目删除 path 键，与 generate 端 _rewrite_item_path 同契约。相对化为纯
    词法计算，不触达文件系统。
    """
    for key in _ROOTS_ECHO_KEYS:
        structured.pop(key, None)
    images = structured.get("images")
    if not isinstance(images, list):
        return
    for item in images:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str):
            relative = save_root_relative(path, save_root)
            if relative is not None:
                item["path"] = relative
            else:
                del item["path"]


async def _directory_outside_save_root(directory: str, save_root: Path) -> bool:
    """解析请求目录并判定是否落在存储区外；形态非法交 browse 核心报具体原因。"""

    def _outside() -> bool:
        try:
            resolved = normalize_path(directory, str(save_root))
        except ValueError:
            return False
        return not is_within_resolved(resolved, save_root)

    return await asyncio.to_thread(_outside)


async def web_browse(request: Request) -> Response:
    """图库浏览端点，请求体经 BrowseImagesInput 校验后透传 browse 工具。"""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _shared.error_json("invalid_json", "请求体不是合法 JSON", 400)
    if not isinstance(body, dict):
        return _shared.error_json("invalid_request", "请求体须为 JSON 对象", 400)
    try:
        params = BrowseImagesInput.model_validate(body)
    except ValidationError as exc:
        return _shared.error_json(
            "invalid_request", f"参数校验失败: {exc.errors()[0].get('msg')}", 400
        )

    save_root = await _shared.resolve_web_save_root()
    if isinstance(save_root, JSONResponse):
        return save_root
    directory = params.effective_directory
    if await _directory_outside_save_root(directory, save_root):
        return _shared.error_json(
            "invalid_directory", "目录不在存储区内，Web 图库仅浏览存储区目录", 400
        )
    try:
        result = await run_browse_images(params, ctx=None)
    except Exception:
        logger.exception("Web 图库浏览请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)
    structured = result.structured_content if result.structured_content is not None else {}
    if isinstance(structured, dict):
        _converge_for_web(structured, save_root)
    status = 200 if not result.is_error else 400
    return JSONResponse(structured, status_code=status)
