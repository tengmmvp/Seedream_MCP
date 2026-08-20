"""Web 操作台测试共享辅助：生产装配序构建与图片样本。

build_web_app 镜像 transport._run_streamable_http 的装配序（register ->
streamable_http_app -> mount -> attach），保证测试栈与生产栈同源；路由状态隔离
fixture 见 conftest 的 clean_web_routes。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.transport import _attach_streamable_http_middleware, _transport_security_for_host
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

_MAX_BODY = 64 * 1024 * 1024

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
    """按生产装配序构建带 Web 路由的 streamable-http 传输栈。"""
    if web_enabled:
        from seedream_mcp.webapp import register_web_routes

        register_web_routes()
    app = server.mcp.streamable_http_app(
        host=host,
        stateless_http=False,
        transport_security=_transport_security_for_host(host),
        max_request_body_size=_MAX_BODY,
    )
    if web_enabled:
        from seedream_mcp.webapp import mount_web_static

        mount_web_static(app)
    _attach_streamable_http_middleware(
        app, host, auth_token, max_body_size=_MAX_BODY, web_enabled=web_enabled
    )
    return app


def write_workspace_config(tmp_path: Path) -> Path:
    """以 tmp 为工作区根注入活动配置并创建保存根目录，返回保存根路径。"""
    save_root = tmp_path / ".seedream" / "images"
    save_root.mkdir(parents=True)
    set_active_config(SeedreamConfig(api_key="test_key", workspace_root=str(tmp_path)))
    return save_root


def prepare_static_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """以 tmp 目录顶替静态资源目录，写入 index 页、脚本与 404 页供各用例消费。"""
    from seedream_mcp.webapp import constants as web_constants

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>web</title>", encoding="utf-8")
    (static_dir / "404.html").write_text("<!doctype html><p>404</p>", encoding="utf-8")
    (static_dir / "app.js").write_bytes(b"// placeholder")
    monkeypatch.setattr(web_constants, "STATIC_DIR", static_dir)
    return static_dir


def make_png_bytes(width: int = 8, height: int = 8) -> bytes:
    """生成一张 PIL 可解码的 PNG 字节，供文件与缩略图端点测试使用。"""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 60)).save(buffer, format="PNG")
    return buffer.getvalue()
