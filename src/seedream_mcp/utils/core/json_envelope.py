"""紧凑严格 JSON 序列化原语，与 HTTP 响应的扁平错误体、安全响应头。"""

from __future__ import annotations

import json
from typing import Any

# no-store 防代理缓存动态状态，nosniff 封堵 MIME 嗅探。
SECURE_JSON_HEADERS: dict[str, str] = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}

# 安全头的 ASGI 线缆预编码形态，免每响应重复编码。
SECURE_JSON_HEADER_BYTES: tuple[tuple[bytes, bytes], ...] = tuple(
    (name.encode("ascii"), value.encode("ascii")) for name, value in SECURE_JSON_HEADERS.items()
)


def dump_compact_strict_json(value: Any) -> str:
    """紧凑严格 JSON 序列化：非 ASCII 原文输出、分隔符省空白、非有限浮点抛 ValueError。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def dump_strict_json_bytes(value: Any) -> bytes:
    """紧凑严格 JSON 的 UTF-8 字节形态，供下沉路径把序列化与编码一并带离事件循环。"""
    return dump_compact_strict_json(value).encode("utf-8")


def flat_error_body(error: str, description: str) -> bytes:
    """构造 error 与 error_description 两键的 JSON 错误响应体字节。"""
    return dump_strict_json_bytes({"error": error, "error_description": description})
