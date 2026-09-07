"""Web 操作台图库浏览端点：存储根边界校验后透传 browse 工具并剥除服务器侧回显。

与 MCP 会话共用同一求值链与读权限判定；Web 文件端点仅服务存储根内文件，图库
浏览同样以存储根为界，解析出存储根的请求目录在端点拒绝。条目 path 为存储根
相对形态，前端可直接拼接为图片端点参数；workspace_roots 与 resolved_directories
回显字段携带服务器绝对路径，Web 前端不消费，返回浏览器前剥除；错误消息中的
读权限成员绝对路径替换为占位符，与 config-info 的防泄露口径一致。
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
    mask_scope_paths,
    normalize_path,
)
from . import _shared

logger = get_logger()

# browse 工具为 MCP 客户端回显的边界字段，携带服务器绝对路径，不出 Web 端点。
_ROOTS_ECHO_KEYS = ("workspace_roots", "resolved_directories")


def _strip_roots_echo(structured: dict[str, object]) -> None:
    """剥除携带服务器绝对路径的边界回显字段，Web 前端不消费。"""
    for key in _ROOTS_ECHO_KEYS:
        structured.pop(key, None)


def _sanitize_error_message(
    structured: dict[str, object], save_root: Path, read_scope: list[Path]
) -> None:
    """错误消息中的读权限成员绝对路径替换为占位符，不向浏览器泄露服务器路径。

    读权限由调用方经 _shared.read_scope_or_default 在工作线程预先派生，本函数
    为纯文本改写，不触达文件系统。
    """
    error = structured.get("error")
    if not isinstance(error, dict):
        return
    message = error.get("message")
    if isinstance(message, str):
        error["message"] = mask_scope_paths(message, save_root, read_scope)


async def _directory_outside_save_root(directory: str, save_root: Path) -> bool:
    """解析请求目录并判定是否落在存储根外；形态非法交 browse 核心报具体原因。"""

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
    directory = params.directory if params.directory is not None else "."
    if await _directory_outside_save_root(directory, save_root):
        return _shared.error_json(
            "invalid_directory", "目录不在保存根内，Web 图库仅浏览保存根目录", 400
        )
    try:
        result = await run_browse_images(params, ctx=None)
    except Exception:
        logger.exception("Web 图库浏览请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)
    structured = result.structured_content if result.structured_content is not None else {}
    if isinstance(structured, dict):
        _strip_roots_echo(structured)
        if result.is_error:
            # 读权限求值含同步文件系统调用，下沉工作线程；失败降级口径由
            # _shared.read_scope_or_default 单点定义。
            read_scope = await asyncio.to_thread(_shared.read_scope_or_default, save_root)
            _sanitize_error_message(structured, save_root, read_scope)
    status = 200 if not result.is_error else 400
    return JSONResponse(structured, status_code=status)
