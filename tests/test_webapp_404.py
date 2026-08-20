"""Web 操作台兜底 404 测试：风格化 404 页、API 前缀 JSON 错误与路由顺序。

兜底路由在 mount_web_static 中排在静态挂载之后追加；顺序若颠倒，静态资源
请求会被 ``/{path:path}`` 吞掉，静态资源用例即为本顺序的回归守护。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from _web_fixtures import build_web_app, prepare_static_dir, write_workspace_config


async def _get(app: Any, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return await client.get(path)


async def test_unknown_path_returns_styled_html_404(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """未知路径返回风格化 404 页而非 Starlette 默认纯文本。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _get(app, "/random/nowhere")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "404" in response.text


async def test_unknown_api_path_returns_json_404(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """API 前缀下未知接口保持统一 JSON 错误形态，供前端程序化消费。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _get(app, "/web/api/nonexistent")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


async def test_fallback_does_not_swallow_static_or_known_routes(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """兜底路由排在静态挂载之后：静态资源与既有入口路由全部正常命中。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    static_response = await _get(app, "/web/static/app.js")
    index_response = await _get(app, "/web")
    root_response = await _get(app, "/")

    assert static_response.status_code == 200
    assert index_response.status_code == 200
    assert root_response.status_code == 307


async def test_web_disabled_keeps_default_plain_404(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """默认关闭时不注册兜底路由，未知路径保持 Starlette 默认 404 形态。"""
    write_workspace_config(tmp_path)
    app = build_web_app(web_enabled=False)

    response = await _get(app, "/random/nowhere")

    assert response.status_code == 404
    assert not response.headers.get("content-type", "").startswith("text/html")
