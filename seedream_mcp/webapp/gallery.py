"""Web 操作台图库浏览端点：以保存根为工作区边界委托 browse 工具。

显式传入保存根的 roots 使输出 path 为相对保存根的真实相对路径，避免 Web 场景
无 MCP Roots 时的占位符掩码；条目路径归一为正斜杠，与生成的 web_path 及前端
URL 拼接保持一致。
"""

from __future__ import annotations

import json

from mcp.types import ListRootsResult, Root
from pydantic import FileUrl, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import get_active_config
from ..tools.core._helpers import _resolve_default_base_dir
from ..tools.core.schemas import BrowseImagesInput
from ..tools.runners import run_browse_images
from ..utils.core.errors import SeedreamValidationError
from . import _shared


def _normalize_browse_paths(structured: dict[str, object]) -> None:
    """把图库条目路径归一为正斜杠形态，与 web_path 及前端 URL 拼接保持一致。"""
    images = structured.get("images")
    if not isinstance(images, list):
        return
    for item in images:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            item["path"] = item["path"].replace("\\", "/")


async def web_browse(request: Request) -> Response:
    """图库浏览端点，BrowseImagesInput 字段透传，分页字段由前端驱动。"""
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
        save_root = _resolve_default_base_dir(get_active_config())
    except SeedreamValidationError as exc:
        return _shared.save_root_unavailable(exc)
    roots = ListRootsResult(roots=[Root(uri=FileUrl(save_root.as_uri()), name="web-save-root")])
    result = await run_browse_images(params, ctx=None, workspace_roots=roots)
    structured = result.structured_content if result.structured_content is not None else {}
    if isinstance(structured, dict):
        _normalize_browse_paths(structured)
    status = 200 if not result.is_error else 400
    return JSONResponse(structured, status_code=status)
