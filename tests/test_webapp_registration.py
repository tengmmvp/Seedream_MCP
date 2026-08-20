"""Web 操作台路由注册测试：注册形态、幂等性与默认关闭形态。

默认关闭是部署面的关键契约：未调用 register_web_routes 时构建的 app 不含任何
/web 路径端点，404 即为未开启 Web 的全部暴露面。
"""

from __future__ import annotations

from pathlib import Path

import httpx

from _web_fixtures import (
    EXPECTED_WEB_PATHS,
    build_web_app,
    write_workspace_config,
)


def test_register_web_routes_registers_expected_paths(clean_web_routes: None) -> None:
    """注册后 MCPServer 自定义路由覆盖全部 Web 端点路径。"""
    from seedream_mcp.resources import mcp
    from seedream_mcp.webapp import register_web_routes

    register_web_routes()

    registered = {getattr(route, "path", None) for route in mcp._custom_starlette_routes}
    assert EXPECTED_WEB_PATHS <= registered


def test_register_web_routes_is_idempotent(clean_web_routes: None) -> None:
    """重复调用注册不产生重复路由。"""
    from seedream_mcp.resources import mcp
    from seedream_mcp.webapp import register_web_routes

    register_web_routes()
    count_first = len(mcp._custom_starlette_routes)
    register_web_routes()

    assert len(mcp._custom_starlette_routes) == count_first


async def test_web_endpoints_absent_when_not_registered(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """未注册时构建的 app 对 /、/web 与全部 API 路径回 404，锁定默认关闭。"""
    write_workspace_config(tmp_path)
    app = build_web_app(web_enabled=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        root_response = await client.get("/")
        index_response = await client.get("/web")
        api_response = await client.get("/web/api/config-info")

    assert root_response.status_code == 404
    assert index_response.status_code == 404
    assert api_response.status_code == 404


async def test_root_redirects_to_web_index(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """Web 开启时根路径重定向到操作台入口。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/web"


async def test_config_info_reachable_when_registered(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """注册并构建后 config-info 返回模型与保存根信息。"""
    save_root = write_workspace_config(tmp_path)
    app = build_web_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get("/web/api/config-info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["models"]
    assert all("allowed_presets" in model for model in payload["models"])
    assert payload["save_root"] == str(save_root)
