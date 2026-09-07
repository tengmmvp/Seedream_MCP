"""Web 操作台各域 handler 共享的响应辅助与错误码映射。

本模块只放跨域复用的纯辅助：统一错误 JSON 形态、存储区解析契约、生成错误
类型到 HTTP 状态码的映射与文件端点的缓存头。域内专有逻辑不落此处，避免
演化为杂物箱。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.responses import JSONResponse

from ..utils.core.errors import SeedreamConfigError
from ..utils.io.io_path import get_read_scope, resolve_save_root

GENERATION_ERROR_STATUS: dict[str, int] = {
    "validation_error": 400,
    "payload_too_large": 400,
    "rate_limited": 429,
    "payment_required": 402,
    "config_error": 503,
}

# 缩略图与原图响应允许浏览器私有缓存：已保存图片内容不再变化。
PRIVATE_CACHE_HEADER = {"cache-control": "private, max-age=3600"}


def error_json(error: str, description: str, status: int) -> JSONResponse:
    """构造与传输层中间件同形态的错误 JSON 响应。"""
    return JSONResponse({"error": error, "error_description": description}, status_code=status)


def save_root_unavailable(exc: Exception) -> JSONResponse:
    """把存储区解析失败包装为携带配置指引的 400 响应。"""
    message = getattr(exc, "message", None) or str(exc)
    return error_json(
        "save_root_unavailable",
        f"无法确定存储区目录: {message}；可配置 SEEDREAM_AUTO_SAVE_BASE_DIR"
        " 或 SEEDREAM_WORKSPACE_ROOT",
        400,
    )


async def resolve_web_save_root() -> Path | JSONResponse:
    """解析存储区，含文件系统的解析下沉工作线程；不可解析时返回 400 响应。"""
    try:
        return await asyncio.to_thread(resolve_save_root)
    except SeedreamConfigError as exc:
        return save_root_unavailable(exc)


def read_scope_or_default(save_root: Path) -> list[Path]:
    """读权限求值，配置类失败退化为仅存储区。

    含 resolve 等同步文件系统调用，调用方须在工作线程执行；生成与图库两域的
    遮蔽上下文派生共用本单点，失败降级口径不再随调用点漂移。
    """
    try:
        return get_read_scope()
    except SeedreamConfigError:
        return [save_root]


def generation_status(structured: dict[str, object]) -> int:
    """按结构化结果的错误类型映射 HTTP 状态码。"""
    error = structured.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        if isinstance(error_type, str):
            return GENERATION_ERROR_STATUS.get(error_type, 502)
    return 502
