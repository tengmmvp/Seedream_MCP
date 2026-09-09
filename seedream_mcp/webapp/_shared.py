"""Web 操作台各域 handler 共享的响应辅助与错误码映射。

本模块只放跨域复用的纯辅助：统一错误 JSON 形态、图片目录解析契约、生成错误
类型到 HTTP 状态码的映射与文件端点的缓存头。域内专有逻辑不落此处，避免
演化为杂物箱。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.responses import JSONResponse

from ..utils.core.errors import SeedreamConfigError
from ..utils.io.io_path import (
    READ_SCOPE_AUTH_ENV_HINT,
    images_root_relative,
    resolve_images_root,
)

GENERATION_ERROR_STATUS: dict[str, int] = {
    "validation_error": 400,
    "payload_too_large": 400,
    "rate_limited": 429,
    "payment_required": 402,
    "config_error": 503,
}

# 缩略图与原图响应允许浏览器私有缓存：已保存图片内容不再变化；nosniff 阻断
# 旧浏览器把图片字节嗅探为其他类型。
PRIVATE_CACHE_HEADER = {
    "cache-control": "private, max-age=3600",
    "x-content-type-options": "nosniff",
}


def error_json(error: str, description: str, status: int) -> JSONResponse:
    """构造与传输层中间件同形态的错误 JSON 响应。"""
    return JSONResponse({"error": error, "error_description": description}, status_code=status)


def images_root_unavailable(exc: Exception) -> JSONResponse:
    """把图片目录解析失败包装为携带配置指引的 400 响应。"""
    message = getattr(exc, "message", None) or str(exc)
    return error_json(
        "images_root_unavailable",
        f"无法确定图片目录: {message}；可配置 {READ_SCOPE_AUTH_ENV_HINT}",
        400,
    )


async def resolve_web_images_root() -> Path | JSONResponse:
    """解析图片目录，含文件系统的解析下沉工作线程；不可解析时返回 400 响应。"""
    try:
        return await asyncio.to_thread(resolve_images_root)
    except SeedreamConfigError as exc:
        return images_root_unavailable(exc)


def structured_error_type(structured: dict[str, object]) -> str | None:
    """提取结构化结果的 error.type，缺失或形态不符返回 None。"""
    error = structured.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        if isinstance(error_type, str):
            return error_type
    return None


def generation_status(structured: dict[str, object]) -> int:
    """按结构化结果的错误类型映射 HTTP 状态码。"""
    error_type = structured_error_type(structured)
    if error_type is None:
        return 502
    return GENERATION_ERROR_STATUS.get(error_type, 502)


def converge_path_entry(
    item: dict[str, object], key: str, images_root: Path, *, resolve: bool = False
) -> None:
    """把条目的路径键改写为图片目录相对形态并附 web_path，越界删除该键。

    resolve 为 True 时先解析为物理路径再相对化（输入为任意本地路径）；False 时
    输入须已是 resolve 后的绝对路径，做纯词法相对化。generate 与 gallery 的
    条目收敛共用，改写规则单点维护。
    """
    value = item.get(key)
    if not isinstance(value, str) or not value:
        return
    path = Path(value)
    if resolve:
        try:
            path = path.resolve()
        except (OSError, ValueError):
            del item[key]
            return
    relative = images_root_relative(path, images_root)
    if relative is None:
        del item[key]
        return
    item["web_path"] = relative
    item[key] = relative
