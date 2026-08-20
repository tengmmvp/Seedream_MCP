"""Web 操作台各域 handler 共享的响应辅助与错误码映射。

本模块只放跨域复用的纯辅助：统一错误 JSON 形态、生成错误类型到 HTTP 状态码
的映射与文件端点的缓存头。域内专有逻辑不落此处，避免演化为杂物箱。
"""

from __future__ import annotations

from starlette.responses import JSONResponse

# 生成结果 error.type 到 HTTP 状态码的映射，未列出的类型统一按上游失败回 502。
GENERATION_ERROR_STATUS: dict[str, int] = {
    "validation_error": 400,
    "payload_too_large": 400,
    "rate_limited": 429,
    "payment_required": 402,
}

# 缩略图与原图响应允许浏览器私有缓存：已保存图片内容不再变化。
PRIVATE_CACHE_HEADER = {"cache-control": "private, max-age=3600"}


def error_json(error: str, description: str, status: int) -> JSONResponse:
    """构造与传输层中间件同形态的错误 JSON 响应。"""
    return JSONResponse({"error": error, "error_description": description}, status_code=status)


def save_root_unavailable(exc: Exception) -> JSONResponse:
    """把保存根解析失败包装为携带配置指引的 400 响应。"""
    message = getattr(exc, "message", None) or str(exc)
    return error_json(
        "save_root_unavailable",
        f"无法确定保存根目录: {message}；可配置 SEEDREAM_AUTO_SAVE_BASE_DIR"
        " 或 SEEDREAM_WORKSPACE_ROOT",
        400,
    )


def generation_status(structured: dict[str, object]) -> int:
    """按结构化结果的错误类型映射 HTTP 状态码。"""
    error = structured.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        if isinstance(error_type, str):
            return GENERATION_ERROR_STATUS.get(error_type, 502)
    return 502
