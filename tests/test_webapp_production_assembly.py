"""Web 操作台生产装配守护测试。

直接调用 transport 的生产装配函数 _build_streamable_app 构建真实 app（构造
transport_security -> 注册 Web 路由 -> streamable_http_app -> 挂载静态资源 ->
装配中间件），经 httpx.ASGITransport 验证 Web 面路由、真实静态资源、Origin
守卫行为与默认关闭形态；_run_streamable_http 仅承担 uvicorn serve 与退出清理，
装配正确性以本文件的生产同路径锁定。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from _web_fixtures import write_workspace_config
from seedream_mcp.transport import _build_streamable_app


def _make_client(app: Any) -> httpx.AsyncClient:
    """以生产 app 构建回环 ASGI 测试客户端。"""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


async def test_production_app_serves_web_console(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """生产装配的 app 提供 /web 入口、config-info 接口、真实静态资源与 404 兜底。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "", True)

    async with _make_client(app) as client:
        index_response = await client.get("/web")
        api_response = await client.get("/web/api/config-info")
        static_response = await client.get("/web/static/main.js")
        html_direct_response = await client.get("/web/static/index.html")
        missing_response = await client.get("/web/api/does-not-exist")

    assert index_response.status_code == 200
    assert index_response.headers["content-type"].startswith("text/html")
    assert api_response.status_code == 200
    assert api_response.json()["save_root_available"] is True
    assert static_response.status_code == 200
    assert html_direct_response.status_code == 404
    assert missing_response.status_code == 404


async def test_production_app_without_web_returns_404(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """web_enabled=False 时不注册任何 Web 路由，入口与 API 路径均 404。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "", False)

    async with _make_client(app) as client:
        index_response = await client.get("/web")
        api_response = await client.get("/web/api/config-info")

    assert index_response.status_code == 404
    assert api_response.status_code == 404


async def test_origin_guard_allows_same_origin_and_rejects_cross_origin(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """无令牌 Web 部署：同源 Origin 与无 Origin 放行，跨源与畸形 Origin 被 403 拒绝。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "", True)

    async with _make_client(app) as client:
        same_origin = await client.get(
            "/web/api/config-info",
            headers={"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:8000"},
        )
        no_origin = await client.get("/web/api/config-info", headers={"host": "127.0.0.1:8000"})
        cross_origin = await client.get(
            "/web/api/config-info",
            headers={"host": "127.0.0.1:8000", "origin": "http://evil.example"},
        )
        malformed_origin = await client.get(
            "/web/api/config-info",
            headers={"host": "127.0.0.1:8000", "origin": "http://[::1"},
        )

    assert same_origin.status_code == 200
    assert no_origin.status_code == 200
    assert cross_origin.status_code == 403
    assert cross_origin.json()["error"] == "invalid_origin"
    assert malformed_origin.status_code == 403
    assert malformed_origin.json()["error"] == "invalid_origin"


async def test_origin_guard_not_assembled_when_token_configured(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """有令牌部署不装配 Origin 守卫：跨源 Origin 由 Bearer 判定，结果取决于令牌。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "s3cret", True)

    async with _make_client(app) as client:
        cross_without_token = await client.get(
            "/web/api/config-info",
            headers={"host": "127.0.0.1:8000", "origin": "http://evil.example"},
        )
        cross_with_token = await client.get(
            "/web/api/config-info",
            headers={
                "host": "127.0.0.1:8000",
                "origin": "http://evil.example",
                "authorization": "Bearer s3cret",
            },
        )

    assert cross_without_token.status_code == 401
    assert cross_with_token.status_code == 200
