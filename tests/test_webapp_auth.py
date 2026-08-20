"""Web 操作台鉴权测试：静态页面豁免边界与 Bearer 上跳段守卫。

配置令牌时静态页面组免鉴权加载、全部 /web/api 强制令牌；无令牌部署全程开放。
豁免判定的单元用例直接驱动中间件 _path_exempt，覆盖含 ``..`` 的路径拒绝豁免。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from _web_fixtures import build_web_app, prepare_static_dir, write_workspace_config
from seedream_mcp.transport import _BearerTokenAuthMiddleware


def test_bearer_path_exempt_matches_static_group_only() -> None:
    """豁免表仅命中 index、根路径重定向与静态前缀，API 与其余路径一律不豁免。"""
    middleware = _BearerTokenAuthMiddleware(
        app=None,  # type: ignore[arg-type]
        expected_token="secret",
        exempt_exact=frozenset({"/web", "/"}),
        exempt_prefixes=("/web/static/",),
    )

    assert middleware._path_exempt({"path": "/web"}) is True
    assert middleware._path_exempt({"path": "/"}) is True
    assert middleware._path_exempt({"path": "/web/static/app.js"}) is True
    assert middleware._path_exempt({"path": "/web/api/config-info"}) is False
    assert middleware._path_exempt({"path": "/mcp"}) is False


@pytest.mark.parametrize(
    "path",
    ["/web/static/../web/api/config-info", "/web/../mcp", "/web%2f..%2fapi"],
)
def test_bearer_path_exempt_rejects_traversal(path: str) -> None:
    """路径含上跳段时一律不豁免，防止借豁免前缀穿越到受保护路径。"""
    middleware = _BearerTokenAuthMiddleware(
        app=None,  # type: ignore[arg-type]
        expected_token="secret",
        exempt_exact=frozenset({"/web", "/"}),
        exempt_prefixes=("/web/static/",),
    )

    assert middleware._path_exempt({"path": path}) is False


async def test_static_pages_exempt_and_api_requires_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """令牌部署下静态页 200、API 无令牌 401、携带令牌 200。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app(auth_token="secret")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        index_response = await client.get("/web")
        static_response = await client.get("/web/static/app.js")
        unauthorized = await client.get("/web/api/config-info")
        authorized = await client.get(
            "/web/api/config-info", headers={"Authorization": "Bearer secret"}
        )

    assert index_response.status_code == 200
    assert index_response.headers["content-type"].startswith("text/html")
    assert static_response.status_code == 200
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"] == "invalid_token"
    assert authorized.status_code == 200


async def test_api_open_when_no_token_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """无令牌部署不装配 Bearer 中间件，Web 页面与 API 全程开放。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app(auth_token="")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        index_response = await client.get("/web")
        api_response = await client.get("/web/api/config-info")

    assert index_response.status_code == 200
    assert api_response.status_code == 200


def test_mount_web_static_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """重复挂载不叠加 Mount 条目，目录缺失时跳过不抛异常。"""
    from starlette.routing import Mount

    from seedream_mcp.webapp import constants as web_constants
    from seedream_mcp.webapp import routes as routes_module

    static_dir = prepare_static_dir(monkeypatch, tmp_path)
    app: Any = type("_App", (), {"routes": []})()

    routes_module.mount_web_static(app)
    routes_module.mount_web_static(app)

    mounts = [r for r in app.routes if isinstance(r, Mount)]
    assert len(mounts) == 1
    assert mounts[0].path == "/web/static"

    missing_dir = tmp_path / "missing"
    monkeypatch.setattr(web_constants, "STATIC_DIR", missing_dir)
    empty_app: Any = type("_App", (), {"routes": []})()
    routes_module.mount_web_static(empty_app)
    assert empty_app.routes == []
    assert static_dir.is_dir()
