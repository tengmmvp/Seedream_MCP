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


async def test_trailing_slash_redirects_to_trimmed_path(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """尾斜杠路径 307 到去尾斜杠形态，恢复被兜底路由吞掉的 redirect_slashes 语义。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        mcp_response = await client.get("/mcp/", follow_redirects=False)
        web_response = await client.get("/web/", follow_redirects=False)
        unknown_final = await client.get("/unknown/", follow_redirects=True)

    assert mcp_response.status_code == 307
    assert mcp_response.headers["location"] == "/mcp"
    assert web_response.status_code == 307
    assert web_response.headers["location"] == "/web"
    assert unknown_final.status_code == 404


async def test_protocol_relative_path_not_redirected(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """协议相对形态 //host 去尾斜杠后仍是开放重定向目标，不走重定向落 404。

    请求以绝对 URL 直发：相对路径形态会被 httpx 按 RFC 3986 join 成外域地址。
    """
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get("http://127.0.0.1//evil.com/", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers


async def test_static_mount_denies_html_direct_access(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """静态挂载封禁 html 直达：页面只经 meta 端点携带安全头直出，JS 资源仍 200。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    html_response = await _get(app, "/web/static/index.html")
    script_response = await _get(app, "/web/static/app.js")

    assert html_response.status_code == 404
    assert script_response.status_code == 200


async def test_page_responses_carry_security_headers(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """入口页与 404 页响应携带 CSP 与 nosniff，收敛脚本注入与 MIME 嗅探面。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    index_response = await _get(app, "/web")
    missing_response = await _get(app, "/random/nowhere")

    for response in (index_response, missing_response):
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
