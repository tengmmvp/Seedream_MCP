"""Web 操作台静态资源存在性守护：打包漏文件在测试期即暴露。

wheel 与 sdist 的打包清单由 CI 侧断言另行锁定，本文件守护源码树形态：
入口页、404 页、五个前端 JS 模块齐备，且入口页引用的静态相对路径全部可解析。
"""

from __future__ import annotations

import re

from seedream_mcp.webapp.constants import STATIC_DIR

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
        assert (STATIC_DIR / name).is_file(), f"前端模块缺失: {name}"
