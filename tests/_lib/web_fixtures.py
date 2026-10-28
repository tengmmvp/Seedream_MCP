"""Web 操作台测试共享辅助：生产装配序构建、ASGI 请求与图片样本。

build_web_app 经 _asgi_fakes 的共享装配核心按生产装配序（register ->
streamable_http_app -> mount -> attach）构建传输栈，保证测试栈与生产栈同源；
web_asgi_client 与 web_get 供 webapp 与传输 e2e 测试文件发起回环 ASGI 请求，
web_post_raw 以原始请求体 POST 供请求体解析分支用例，_assert_structured_failure
断言 browse 与 generate 端点共享的结构化失败信封；
drive_asgi_messages 直驱响应对象收集全部 ASGI 消息，asgi_start_headers 与
asgi_body_bytes 自消息列取值，供绕过传输栈直调响应对象的用例共享；路由状态
隔离 fixture 见 conftest 的 clean_web_routes；read_js_module_source 读取前端
JS 模块源文本、function_body 以列首 } 结构锚点取函数体源文本，供静态资源
契约断言共用。
"""

from __future__ import annotations

import io
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from _lib.asgi_fakes import _assemble_streamable_http_app
from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.webapp import constants as web_constants
from seedream_mcp.webapp.constants import (
    WEB_API_BROWSE,
    WEB_API_CONFIG_INFO,
    WEB_API_GENERATE_IMAGE_TO_IMAGE,
    WEB_API_GENERATE_MULTI_IMAGE_FUSION,
    WEB_API_GENERATE_SEQUENTIAL_GENERATION,
    WEB_API_GENERATE_TEXT_TO_IMAGE,
    WEB_API_IMAGE,
    WEB_API_THUMBNAIL,
    WEB_INDEX_PATH,
    WEB_ROOT_PATH,
)

# Web 端点路径全集：与 routes.register_web_routes 的注册表双向对齐，注册断言
# 按相等校验。新增端点须同步本清单，漏登记或漏注册都会使测试变红。
EXPECTED_WEB_PATHS = frozenset(
    {
        WEB_ROOT_PATH,
        WEB_INDEX_PATH,
        WEB_API_CONFIG_INFO,
        WEB_API_GENERATE_TEXT_TO_IMAGE,
        WEB_API_GENERATE_IMAGE_TO_IMAGE,
        WEB_API_GENERATE_MULTI_IMAGE_FUSION,
        WEB_API_GENERATE_SEQUENTIAL_GENERATION,
        WEB_API_BROWSE,
        WEB_API_THUMBNAIL,
        WEB_API_IMAGE,
    }
)


def build_web_app(
    auth_token: str = "",
    *,
    host: str = "127.0.0.1",
    web_enabled: bool = True,
) -> Any:
    """按生产装配序构建带 Web 路由的 streamable-http 传输栈。

    装配经 _asgi_fakes._assemble_streamable_http_app 单点完成，web_enabled 驱动
    Web 路由注册与静态挂载。
    """
    return _assemble_streamable_http_app(auth_token, host=host, web_enabled=web_enabled)


@asynccontextmanager
async def web_asgi_client(
    app: Any, base_url: str = "http://127.0.0.1"
) -> AsyncIterator[httpx.AsyncClient]:
    """构建一次性 ASGI httpx 客户端，base_url 默认回环地址。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base_url
    ) as client:
        yield client


async def web_get(app: Any, path: str) -> httpx.Response:
    """GET 一次 Web 应用路由并返回响应。"""
    async with web_asgi_client(app) as client:
        return await client.get(path)


async def web_post_raw(app: Any, path: str, content: bytes) -> httpx.Response:
    """以 application/json 原始请求体 POST 一次，供请求体解析分支使用。"""
    async with web_asgi_client(app) as client:
        return await client.post(
            path, content=content, headers={"content-type": "application/json"}
        )


def _assert_structured_failure(
    response: httpx.Response,
    tool: str,
    error_type: str,
    *,
    status: int,
    message_contains: str | None = None,
) -> dict[str, Any]:
    """断言失败响应为完整结构化结果并映射预期状态码，返回解析后的载荷。"""
    payload = _assert_endpoint_error(
        response, error_type, status=status, message_contains=message_contains
    )
    assert payload["tool"] == tool
    assert payload["status"] == "failed"
    return payload


def _assert_endpoint_error(
    response: httpx.Response,
    error_type: str,
    *,
    status: int,
    message_contains: str | None = None,
) -> dict[str, Any]:
    """断言 {success, error} 信封的失败响应并映射预期状态码。"""
    assert response.status_code == status
    payload: dict[str, Any] = response.json()
    assert payload["success"] is False
    assert payload["error"]["type"] == error_type
    if message_contains is not None:
        assert message_contains in payload["error"]["message"]
    return payload


async def drive_asgi_messages(
    response: Any,
    scope: Mapping[str, object],
    messages: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """以空请求体 receive 与消息收集 send 直驱 ASGI 响应对象，回传全部消息。

    传入 messages 时同步收集到该列表，响应中途抛错时调用方仍持有已收消息。
    """
    collected = messages if messages is not None else []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        collected.append(message)

    await response(scope, receive, send)
    return collected


def asgi_start_headers(messages: list[dict[str, object]]) -> dict[str, str]:
    """取起始行消息并回传小写响应头映射。"""
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw_headers = cast("list[tuple[bytes, bytes]]", start["headers"])
    return {key.decode().lower(): value.decode() for key, value in raw_headers}


def asgi_body_bytes(messages: list[dict[str, object]]) -> bytes:
    """拼接全部响应 body 分块。"""
    return b"".join(
        cast("bytes", message["body"])
        for message in messages
        if message["type"] == "http.response.body"
    )


def write_workspace_config(tmp_path: Path) -> Path:
    """以 tmp 为工作区根注入活动配置并创建图片目录，返回图片目录路径。"""
    images_root = tmp_path / ".seedream" / "images"
    images_root.mkdir(parents=True)
    set_active_config(SeedreamConfig(api_key="test_key", workspace_root=str(tmp_path)))
    return images_root


def make_images_root_unresolvable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """注入数据根目录不可解析的配置并顶替其解析。"""
    import seedream_mcp.utils.io.path as io_path_module

    def _unresolvable(configured_dir: str) -> Path:
        del configured_dir
        raise OSError("simulated unresolvable path")

    set_active_config(SeedreamConfig(api_key="test_key", data_root=str(tmp_path / "pics")))
    monkeypatch.setattr(io_path_module, "resolve_cached_data_root", _unresolvable)


def prepare_static_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """以 tmp 目录顶替静态资源目录，写入 index 页、脚本与 404 页供各用例消费。"""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>web</title>", encoding="utf-8")
    (static_dir / "404.html").write_text("<!doctype html><p>404</p>", encoding="utf-8")
    (static_dir / "app.js").write_bytes(b"// placeholder")
    monkeypatch.setattr(web_constants, "STATIC_DIR", static_dir)
    return static_dir


def read_js_module_source(module: str) -> str:
    """读取前端 JS 模块源文本，调用期取 STATIC_DIR 以跟随其 monkeypatch 顶替。"""
    return (web_constants.STATIC_DIR / "js" / module).read_text(encoding="utf-8")


def function_body(source: str, pattern: str) -> str:
    """按结构锚点取函数体源文本，锚点以列首 } 收束，失配即提示守护同步。"""
    match = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    assert match is not None, f"缺函数体锚: {pattern}"
    return match.group(0)


def make_png_bytes(width: int = 8, height: int = 8) -> bytes:
    """生成一张 PIL 可解码的 PNG 字节，供图库、文件与缩略图端点测试使用。"""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 60)).save(buffer, format="PNG")
    return buffer.getvalue()
