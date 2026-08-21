"""Web 操作台图库浏览端点：以保存根为工作区边界委托 browse 工具。

显式把保存根声明为 roots，浏览范围与条目 path 都以保存根为基准，前端可将
path 直接拼接为图片端点参数。workspace_roots 与 resolved_directories 回显
字段携带服务器绝对路径，Web 前端不消费，返回浏览器前剥除；错误消息中的
保存根绝对路径替换为占位符，与 config-info 的防泄露口径一致。条目路径归一
为正斜杠，与 web_path 及前端 URL 拼接保持一致。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp.types import ListRootsResult, Root
from pydantic import FileUrl, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import get_active_config
from ..tools.core._helpers import resolve_default_base_dir
from ..tools.core.schemas import BrowseImagesInput
from ..tools.runners import run_browse_images
from ..utils.core.errors import SeedreamValidationError
from ..utils.core.logs import get_logger
from . import _shared

logger = get_logger()

# browse 工具为 MCP 客户端回显的边界字段，携带服务器绝对路径，不出 Web 端点。
_ROOTS_ECHO_KEYS = ("workspace_roots", "resolved_directories")


def _normalize_browse_paths(structured: dict[str, object]) -> None:
    """把图库条目路径归一为正斜杠形态，与 web_path 及前端 URL 拼接保持一致。"""
    images = structured.get("images")
    if not isinstance(images, list):
        return
    for item in images:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            item["path"] = item["path"].replace("\\", "/")


def _strip_roots_echo(structured: dict[str, object]) -> None:
    """剥除携带服务器绝对路径的边界回显字段，Web 前端不消费。"""
    for key in _ROOTS_ECHO_KEYS:
        structured.pop(key, None)


def _sanitize_error_message(structured: dict[str, object], save_root: Path) -> None:
    """错误消息中的保存根绝对路径替换为占位符，不向浏览器泄露服务器路径。"""
    error = structured.get("error")
    if not isinstance(error, dict):
        return
    message = error.get("message")
    if isinstance(message, str):
        error["message"] = message.replace(str(save_root), _shared.SAVE_ROOT_PLACEHOLDER)


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

    try:
        save_root = await asyncio.to_thread(resolve_default_base_dir, get_active_config())
    except SeedreamValidationError as exc:
        return _shared.save_root_unavailable(exc)
    roots = ListRootsResult(roots=[Root(uri=FileUrl(save_root.as_uri()), name="web-save-root")])
    try:
        result = await run_browse_images(params, ctx=None, workspace_roots=roots)
    except Exception:
        logger.exception("Web 图库浏览请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)
    structured = result.structured_content if result.structured_content is not None else {}
    if isinstance(structured, dict):
        _normalize_browse_paths(structured)
        _strip_roots_echo(structured)
        if result.is_error:
            _sanitize_error_message(structured, save_root)
    status = 200 if not result.is_error else 400
    return JSONResponse(structured, status_code=status)
