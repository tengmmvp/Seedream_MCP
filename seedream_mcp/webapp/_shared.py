"""Web 操作台各域 handler 共享的响应辅助与错误码映射。

本模块只放跨域复用的纯辅助：统一错误 JSON 形态、图片目录解析契约、生成错误
类型到 HTTP 状态码的映射与文件端点的缓存头。域内专有逻辑不落此处，避免
演化为杂物箱。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..utils.core.errors import SeedreamConfigError
from ..utils.io.io_path import (
    READ_SCOPE_AUTH_ENV_HINT as READ_SCOPE_AUTH_ENV_HINT,
    images_root_relative,
    resolve_images_root,
)

GENERATION_ERROR_STATUS: dict[str, int] = {
    "validation_error": 400,
    "payload_too_large": 400,
    "rate_limited": 429,
    "payment_required": 402,
    "auth_error": 503,
    "config_error": 503,
    "network_error": 502,
    "api_error": 502,
    "timeout_error": 504,
}

# 缩略图与原图响应允许浏览器私有缓存：已保存图片内容不再变化；nosniff 阻断
# 旧浏览器把图片字节嗅探为其他类型。
PRIVATE_CACHE_HEADER = {
    "cache-control": "private, max-age=3600",
    "x-content-type-options": "nosniff",
}


# /web/api 全部 JSON 响应的公共安全头：no-store 防代理缓存动态状态，nosniff
# 封堵旧浏览器 MIME 嗅探反射面。
WEB_JSON_HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}


def error_json(error: str, description: str, status: int) -> JSONResponse:
    """构造与传输层中间件同形态的错误 JSON 响应。"""
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers=WEB_JSON_HEADERS,
    )


async def parse_json_object_body(request: Request) -> tuple[dict[str, Any], JSONResponse | None]:
    """解析请求体为 JSON 对象，解析失败或形态不符时返回错误响应。

    解析随参考图体积线性增长，下沉工作线程；生成与图库端点共用同一口径。
    """
    try:
        body = await asyncio.to_thread(json.loads, await request.body())
    # 深嵌套 JSON 触发解析器递归上限抛 RecursionError，与解析失败同归 400。
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        return {}, error_json("invalid_json", f"请求体不是合法 JSON: {exc}", 400)
    if not isinstance(body, dict):
        return {}, error_json("invalid_request", "请求体须为 JSON 对象", 400)
    return body, None


def validation_error_json(exc: ValidationError) -> JSONResponse:
    """把 pydantic 校验失败格式化为统一的首个错误描述响应。"""
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", ()))
    return error_json(
        "invalid_request",
        f"参数校验失败: {field or first.get('type')} {first.get('msg')}",
        400,
    )


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


def browse_status(structured: dict[str, object]) -> int:
    """图库结构化结果的错误类型映射，validation_error 归 400 其余归 500。"""
    return 400 if structured_error_type(structured) == "validation_error" else 500


def dump_strict_json(structured: dict[str, object]) -> str:
    """以严格 JSON 序列化结构化结果。

    非有限浮点已在流水线用量净化处归零，此处 allow_nan=False 仅作漏网哨兵。
    """
    return json.dumps(
        structured,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


async def respond_structured_json(structured: dict[str, object], status: int) -> Response:
    """以严格 JSON 构造结构化结果响应，序列化下沉工作线程。"""
    payload = await asyncio.to_thread(dump_strict_json, structured)
    return Response(
        content=payload,
        media_type="application/json",
        status_code=status,
        headers=WEB_JSON_HEADERS,
    )


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
