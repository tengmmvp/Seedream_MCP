"""Web 操作台图库浏览端点：以保存根为浏览基准委托 browse 工具。

不伪造会话 Roots，浏览目录以绝对保存根路径传入并在端点侧校验不逃逸保存
根，边界由 runner 内的环境变量回退链界定。条目 path 重写为保存根相对形态，
前端可将 path 直接拼接为图片端点参数。workspace_roots 与
resolved_directories 回显字段携带服务器绝对路径，Web 前端不消费，返回浏览
器前剥除；错误消息中的保存根绝对路径替换为占位符，与 config-info 的防泄露
口径一致。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import SeedreamConfig, get_active_config
from ..tools.core._helpers import resolve_default_base_dir
from ..tools.core.schemas import BrowseImagesInput, DIRECTORY_MAX_LENGTH
from ..tools.runners import run_browse_images
from ..utils.core.errors import SeedreamValidationError
from ..utils.core.logs import get_logger
from ..utils.io.io_path import get_workspace_root, is_within_resolved, normalize_path
from . import _shared

logger = get_logger()

# browse 工具为 MCP 客户端回显的边界字段，携带服务器绝对路径，不出 Web 端点。
_ROOTS_ECHO_KEYS = ("workspace_roots", "resolved_directories")


def _rewrite_paths_relative_to_save_root(
    structured: dict[str, object], workspace_base: Path, save_root: Path
) -> None:
    """条目 path 从边界根相对形态重写为保存根相对形态，与 web_path 口径一致。

    browse 以首个包含根为相对化基准，边界经回退链取得时基准是工作区根；
    回退形态下 resolved_directories 已被占位符脱敏，基准由调用方另行解析。
    换基失败的条目退化为正斜杠归一。
    """
    images = structured.get("images")
    if not isinstance(images, list):
        return
    for item in images:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            try:
                item["path"] = (workspace_base / item["path"]).relative_to(save_root).as_posix()
            except ValueError:
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
        error["message"] = _shared.mask_save_root_text(message, save_root)


def _resolve_browse_context(config: SeedreamConfig, directory: str) -> tuple[Path, Path, Path]:
    """单次线程往返解析保存根、工作区根与用户目录。"""
    save_root = resolve_default_base_dir(config)
    workspace_base = get_workspace_root()
    return save_root, workspace_base, normalize_path(directory, str(save_root))


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

    # 不伪造会话 Roots：伪造使 UNC 保存根在 file URI 转换层丢失令图库全量
    # 400。用户目录经 normalize_path 以保存根为基准解析，UNC、空字节与冒号
    # 分量在 resolve 前的输入级即被拒，不触发网络与文件系统访问；三步解析
    # 合并单次线程往返。
    original_directory = params.directory or "."
    try:
        save_root, workspace_base, resolved_dir = await asyncio.to_thread(
            _resolve_browse_context, get_active_config(), original_directory
        )
    except SeedreamValidationError as exc:
        return _shared.save_root_unavailable(exc)
    except ValueError as exc:
        return _shared.error_json("invalid_request", f"目录无效: {exc}", 400)
    # 显式配置的保存根越出工作区边界时 browse 无法授权浏览，报配置指引而非
    # 误导性的目录越界错误。
    if not is_within_resolved(save_root, workspace_base):
        return _shared.error_json(
            "save_root_outside_workspace",
            "保存根不在工作区边界内，无法浏览；请将 SEEDREAM_WORKSPACE_ROOT"
            " 配置为涵盖保存根的目录",
            400,
        )
    if not is_within_resolved(resolved_dir, save_root):
        return _shared.error_json("invalid_request", "目录须位于保存根之内", 400)
    resolved_directory = str(resolved_dir)
    # model_copy 跳过字段校验，长度上界在此手工复核。
    if len(resolved_directory) > DIRECTORY_MAX_LENGTH:
        return _shared.error_json("invalid_request", "目录路径长度超出上限", 400)
    params = params.model_copy(update={"directory": resolved_directory})
    try:
        result = await run_browse_images(params, ctx=None)
    except Exception:
        logger.exception("Web 图库浏览请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)
    structured = result.structured_content if result.structured_content is not None else {}
    if isinstance(structured, dict):
        # directory 回显还原为用户原始输入，绝对保存根路径不出端点。
        structured["directory"] = original_directory
        _rewrite_paths_relative_to_save_root(structured, workspace_base, save_root)
        _strip_roots_echo(structured)
        if result.is_error:
            _sanitize_error_message(structured, save_root)
    status = 200 if not result.is_error else 400
    return JSONResponse(structured, status_code=status)
