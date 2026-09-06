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


async def test_backslash_protocol_relative_path_not_redirected(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """反斜杠与百分号编码形态的协议相对目标不重定向，封堵归一绕过的开放重定向。

    浏览器把特殊 scheme 路径中的反斜杠按斜杠解析，/\\evil.com 会跳到外域；请求
    以绝对 URL 直发，httpx 把字面反斜杠编码为 %5C，两种形态服务端同形处理。
    """
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        backslash_response = await client.get(
            "http://127.0.0.1/\\evil.com/", follow_redirects=False
        )
        encoded_response = await client.get("http://127.0.0.1/%5Cevil.com/", follow_redirects=False)

    assert backslash_response.status_code == 404
    assert "location" not in backslash_response.headers
    assert encoded_response.status_code == 404
    assert "location" not in encoded_response.headers


async def test_decoded_backslash_path_not_redirected(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """解码后携带字面反斜杠的路径不重定向：生产服务器按 ASGI 规范解码 scope path。

    httpx 传输栈不解码百分号序列，字面反斜杠形态经合成 scope 直调兜底 handler
    锁定，确保 uvicorn 解码路径下的同形请求落 404。
    """
    from starlette.requests import Request

    from seedream_mcp.webapp import meta as meta_module

    prepare_static_dir(monkeypatch, tmp_path)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/\\evil.com/",
        "raw_path": b"/\\evil.com/",
        "query_string": b"",
        "headers": [],
    }

    response = await meta_module.web_not_found(Request(scope))

    assert response.status_code == 404


async def test_static_mount_denies_html_direct_access(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """静态挂载封禁 html 直达：页面只经 meta 端点携带安全头直出，JS 资源仍 200。

    大写 .HTML 变体同样拒绝：Windows 文件系统大小写不敏感会命中页面文件，
    小写匹配放行即绕过封禁；大小写不敏感文件系统之外该形态本就无文件可命中。
    """
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    html_response = await _get(app, "/web/static/index.html")
    html_upper_response = await _get(app, "/web/static/index.HTML")
    script_response = await _get(app, "/web/static/app.js")

    assert html_response.status_code == 404
    assert html_upper_response.status_code == 404
    assert script_response.status_code == 200


async def test_static_mount_denies_html_trailing_punctuation_variants(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """尾随斜杠、点与空格形态的 html 路径同样 404，封堵归一化绕过。

    Starlette normpath 剥尾斜杠、Win32 路径归一剥尾部点与空格，剥后仍命中
    真实页面文件；封禁判定不同口径归一即被这三种形态绕过直出页面。
    """
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    trailing_slash = await _get(app, "/web/static/index.html/")
    trailing_dot = await _get(app, "/web/static/index.html.")
    trailing_space = await _get(app, "/web/static/index.html%20")

    assert trailing_slash.status_code == 404
    assert trailing_dot.status_code == 404
    assert trailing_space.status_code == 404


async def test_static_mount_denies_html_short_name_variant(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """Windows 8.3 短名形态的页面请求同样 404，封禁须按物理路径判定。

    index~1.htm 的请求路径后缀是 .htm，按路径后缀判定会放行，OS 解析短名却
    命中 index.html。短名生成随卷开关不可控，故在 lookup_path 层模拟 OS 把
    短名解析到真实文件；拦截记录非空保证别名确经模拟层命中，封禁断言不空转。
    """
    from starlette.staticfiles import StaticFiles

    from seedream_mcp.webapp.routes import _GuardedStaticFiles

    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    intercepted: list[str] = []
    original_lookup = StaticFiles.lookup_path

    def short_name_lookup(self: StaticFiles, path: str) -> tuple[str, Any]:
        if path == "index~1.htm":
            intercepted.append(path)
            return original_lookup(self, "index.html")
        return original_lookup(self, path)

    monkeypatch.setattr(_GuardedStaticFiles, "lookup_path", short_name_lookup)

    response = await _get(app, "/web/static/index~1.htm")

    assert intercepted
    assert response.status_code == 404


async def test_page_responses_carry_security_headers(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """入口页与 404 页响应携带 CSP 与 nosniff，收敛脚本注入、iframe 嵌入与嗅探面。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    index_response = await _get(app, "/web")
    missing_response = await _get(app, "/random/nowhere")

    for response in (index_response, missing_response):
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_static_direct_output_carries_nosniff(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """静态直出的 js/css/svg 附 nosniff，阻断浏览器对静态资源的 MIME 嗅探。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    script_response = await _get(app, "/web/static/app.js")

    assert script_response.status_code == 200
    assert script_response.headers["x-content-type-options"] == "nosniff"
