"""Web 操作台静态资源存在性守护：打包漏文件在测试期即暴露。

wheel 与 sdist 的打包清单由 CI 侧断言另行锁定，本文件守护源码树形态：
入口页、404 页、五个前端 JS 模块齐备，且入口页引用的静态相对路径全部可解析。
"""

from __future__ import annotations

import re

from seedream_mcp.webapp.constants import STATIC_DIR

_JS_DIR = "js"
_JS_MODULES = ("api.js", "generate.js", "gallery.js", "main.js", "refs.js")


def test_static_pages_exist() -> None:
    """入口页与 404 页随包存在于静态资源目录。"""
    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "404.html").is_file()


def test_index_referenced_static_assets_exist() -> None:
    """入口页引用的全部 /web/static 相对路径均落在静态资源目录内。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    referenced = set(re.findall(r'(?:src|href)="/web/static/([^"]+)"', html))

    assert referenced, "入口页未解析到任何静态资源引用，匹配规则可能已失配"
    for rel in referenced:
        assert (STATIC_DIR / rel).is_file(), f"静态资源缺失: {rel}"


def test_frontend_js_modules_exist() -> None:
    """五个前端 JS 模块齐备，缺任一即破坏入口模块导入图。"""
    for name in _JS_MODULES:
        assert (STATIC_DIR / _JS_DIR / name).is_file(), f"前端模块缺失: {name}"


def test_bearer_token_uses_session_storage_only() -> None:
    """Bearer 令牌只经 sessionStorage 会话内暂存，前端源码不得回退 localStorage。"""
    for name in ("api.js", "main.js"):
        source = (STATIC_DIR / _JS_DIR / name).read_text(encoding="utf-8")
        assert "localStorage" not in source, f"{name} 出现 localStorage 持久化"
    api_js = (STATIC_DIR / _JS_DIR / "api.js").read_text(encoding="utf-8")
    assert "sessionStorage" in api_js


def test_url_reference_goes_through_add_reference() -> None:
    """URL 手输参考图经 addReference 汇聚，拒绝时输入框保留用户粘贴的 URL。

    addReference 返回布尔告知入列与否，main.js 仅在成功时清空输入框；绕过
    汇聚直改 state.refs 的形态同样拒绝。
    """
    main_js = (STATIC_DIR / _JS_DIR / "main.js").read_text(encoding="utf-8")
    assert 'if (addReference("url", url)) {' in main_js
    assert "state.refs.push" not in main_js
