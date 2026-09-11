"""Web 操作台静态资源存在性守护：打包漏文件在测试期即暴露。

wheel 与 sdist 的打包清单由 CI 侧断言另行锁定，本文件守护源码树形态：
入口页、404 页、五个前端 JS 模块齐备，且入口页引用的静态相对路径全部可解析；
静态直出响应头的缓存口径亦在此守护。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from _web_fixtures import build_web_app, prepare_static_dir, web_get, write_workspace_config
from seedream_mcp.webapp.constants import (
    STATIC_DIR,
    WEB_API_BROWSE,
    WEB_API_CONFIG_INFO,
    WEB_API_GENERATE_IMAGE_TO_IMAGE,
    WEB_API_GENERATE_MULTI_IMAGE_FUSION,
    WEB_API_GENERATE_SEQUENTIAL_GENERATION,
    WEB_API_GENERATE_TEXT_TO_IMAGE,
    WEB_API_IMAGE,
    WEB_API_PREFIX,
    WEB_API_THUMBNAIL,
)

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


def test_frontend_api_literals_stay_within_registered_routes() -> None:
    """前端 JS/HTML 的 /web/api 字面量与 data-tool 值落在后端注册路径集合内。

    后端改路由常量而前端字面量未同步时，拼写漂移在此失败而非运行期 404。
    """
    registered = {
        WEB_API_CONFIG_INFO,
        WEB_API_BROWSE,
        WEB_API_THUMBNAIL,
        WEB_API_IMAGE,
        WEB_API_GENERATE_TEXT_TO_IMAGE,
        WEB_API_GENERATE_IMAGE_TO_IMAGE,
        WEB_API_GENERATE_MULTI_IMAGE_FUSION,
        WEB_API_GENERATE_SEQUENTIAL_GENERATION,
    }
    api_pattern = re.compile(r"/web/api/[a-z-]+")
    sources = [STATIC_DIR / "index.html", *(STATIC_DIR / _JS_DIR / name for name in _JS_MODULES)]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for literal in set(api_pattern.findall(text)):
            if literal == f"{WEB_API_PREFIX}/generate":
                # 生成端点前缀与 data-tool 值拼接，具体 slug 由下方另行校验。
                continue
            assert literal in registered, f"{path.name} 引用未注册路径: {literal}"
        for slug in set(re.findall(r'data-tool="([a-z-]+)"', text)):
            assert (
                f"{WEB_API_PREFIX}/generate/{slug}" in registered
            ), f"{path.name} 的 data-tool 值无对应生成端点: {slug}"


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
    """URL 手输参考图经 addReference 汇聚，不得绕过其上限与容量防护。

    无 JS 运行时测试基建，以稳定结构锚点为契约：消费侧调用形态存在、消费侧
    无直写 state.refs 的绕过、refs.js 保有汇聚实现。
    """
    main_js = (STATIC_DIR / _JS_DIR / "main.js").read_text(encoding="utf-8")
    refs_js = (STATIC_DIR / _JS_DIR / "refs.js").read_text(encoding="utf-8")
    assert 'addReference("url", url)' in main_js
    assert "state.refs.push" not in main_js
    assert "addReference" in refs_js


def test_generate_and_gallery_consume_web_path_contract() -> None:
    """generate.js 与 gallery.js 消费 web_path 字段并经图片端点取图。

    卡片网格走 /web/api/thumbnail 缩略图，原图经灯箱的 /web/api/image 按需
    加载；服务端改字段名或前端重构错位使任一字面量消失时在此失败，防止静默
    退化为仅展示远端 url。
    """
    for name in ("generate.js", "gallery.js"):
        source = (STATIC_DIR / _JS_DIR / name).read_text(encoding="utf-8")
        assert "web_path" in source, f"{name} 不再消费 web_path 字段"
        assert "/web/api/thumbnail" in source, f"{name} 不再请求 /web/api/thumbnail 端点"
    gallery_js = (STATIC_DIR / _JS_DIR / "gallery.js").read_text(encoding="utf-8")
    assert "/web/api/image" in gallery_js, "gallery.js 灯箱不再请求 /web/api/image 端点"


async def test_static_direct_output_requires_revalidation(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """静态直出携带 no-cache 逐次回源验证，包升级后不服务陈旧 JS。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await web_get(app, "/web/static/app.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
