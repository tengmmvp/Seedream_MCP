"""Web 操作台静态资源存在性守护：打包漏文件在测试期即暴露。

wheel 与 sdist 的打包清单由 CI 侧断言另行锁定，本文件守护源码树形态：
入口页、404 页、五个前端 JS 模块齐备，且入口页引用的静态相对路径全部可解析；
前端 JS 的解析口径以每模块一个粗粒度契约守护（解析出口、类型化错误与关键
文案俱在）；静态直出响应头的缓存口径亦在此守护。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _lib.web_fixtures import build_web_app, prepare_static_dir, web_get, write_workspace_config
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.formats import MIME_BY_EXTENSION, SUPPORTED_IMAGE_EXTENSIONS_ORDERED
from seedream_mcp.webapp.meta import _upload_budget_chars
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


def _js_source(module: str) -> str:
    """读取前端模块源文本，粗粒度契约断言共用。"""
    return (STATIC_DIR / _JS_DIR / module).read_text(encoding="utf-8")


def _assert_imported_from_api(source: str, *names: str) -> None:
    """断言指定符号经统一导入块引自 api.js，不私有复制。"""
    block = re.search(r'import \{[^}]*\} from "\./api\.js";', source)
    assert block is not None, "缺 api.js 导入块"
    for name in names:
        assert name in block.group(0), f"未从 api.js 引入 {name}"


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
        source = _js_source(name)
        assert "localStorage" not in source, f"{name} 出现 localStorage 持久化"
    api_js = _js_source("api.js")
    assert "sessionStorage" in api_js


def test_url_reference_goes_through_add_reference() -> None:
    """URL 手输参考图经 addReference 汇聚，不得绕过其上限与容量防护。

    无 JS 运行时测试基建，以稳定结构锚点为契约：消费侧调用形态存在、消费侧
    无直写 state.refs 的绕过、refs.js 保有汇聚实现。
    """
    main_js = _js_source("main.js")
    refs_js = _js_source("refs.js")
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
        source = _js_source(name)
        assert "web_path" in source, f"{name} 不再消费 web_path 字段"
        assert "/web/api/thumbnail" in source, f"{name} 不再请求 /web/api/thumbnail 端点"
    gallery_js = _js_source("gallery.js")
    assert "/web/api/image" in gallery_js, "gallery.js 灯箱不再请求 /web/api/image 端点"


def test_api_js_parse_contract() -> None:
    """api.js 解析契约：JSON 解析出口唯一，类型化错误与关键文案俱在。

    裸 response.json() 只允许存在于宽松解析内，且以 SyntaxError 区分非法
    JSON（undefined 哨兵）与体读取失败（原样上抛）；严格解析拒绝非对象载荷
    （数组/标量）并归 bad_response 档；错误体尽力读取助手吞一切异常不回抛，
    非 ok 分支的消费契约由 generate/gallery 各自守护。
    """
    source = _js_source("api.js")
    assert source.count("await response.json()") == 1, "裸 response.json() 须仅存于宽松解析"
    assert "instanceof SyntaxError" in source, "宽松解析未区分非法 JSON 与体读取失败"
    assert "export class PayloadError" in source, "缺载荷哨兵类型化错误导出"
    assert "export async function parseJsonRequired" in source, "缺严格解析导出"
    assert "export function isAbortError" in source, "缺中止类判定导出"
    for literal in ('error.name === "TimeoutError"', 'error.name === "AbortError"'):
        assert literal in source, "中止类判定缺名称档位"
    for literal in ("bad_response", "empty_response"):
        assert literal in source, f"缺 {literal} 载荷档位"
    assert "响应不是有效的 JSON，服务可能异常，请稍后重试。" in source
    assert "服务返回了空响应，请稍后重试。" in source
    for literal in ('typeof payload !== "object"', "Array.isArray(payload)"):
        assert literal in source, f"严格解析缺对象判定: {literal}"
    assert "响应不是预期的 JSON 对象，服务可能异常，请稍后重试。" in source
    guard = re.search(
        r"export async function readErrorPayload.*?^}", source, re.DOTALL | re.MULTILINE
    )
    assert guard is not None, "缺错误体尽力读取导出"
    assert "await parseJsonLoose(response)" in guard.group(0), "错误体助手未复用宽松解析"
    assert "rethrowIfAbort" not in guard.group(0), "错误体助手不得回抛中止类"


def test_generate_js_parse_contract() -> None:
    """generate.js 解析契约：ok 载荷经严格解析，四档失败文案俱在。

    解析与中止判定统一引自 api.js，不私开裸 response.json() 与中止名判定；
    非 ok 失败体经尽力读取助手消费，读取异常（含中止）不逃逸外层超时档；
    失败分流（令牌、超时、网络、生成失败）的用户文案不因重构丢失。
    """
    source = _js_source("generate.js")
    _assert_imported_from_api(
        source, "isAbortError", "readErrorPayload", "parseJsonRequired", "PayloadError"
    )
    assert "await parseJsonRequired(response)" in source, "ok 载荷未经严格解析"
    assert "await readErrorPayload(response)" in source, "非 ok 失败体未经尽力读取"
    assert "parseJsonLoose" not in source, "绕过尽力读取助手私解析失败体"
    assert "await response.json()" not in source, "回归裸 response.json()"
    for literal in ('error.name === "TimeoutError"', 'error.name === "AbortError"'):
        assert literal not in source, "残留私有中止判定"
    for message in ("需要重新输入令牌。", "生成耗时异常。", "请求失败。", "生成失败。"):
        assert message in source, f"失败分流文案丢失: {message}"


def test_gallery_js_parse_contract() -> None:
    """gallery.js 解析契约：ok 载荷经严格解析，四档图库文案俱在。

    非 ok 失败体同样经尽力读取助手消费，读取异常（含中止）不逃逸外层超时档。
    """
    source = _js_source("gallery.js")
    _assert_imported_from_api(source, "isAbortError", "readErrorPayload", "parseJsonRequired")
    assert "await parseJsonRequired(response)" in source, "ok 载荷未经严格解析"
    assert "await readErrorPayload(response)" in source, "非 ok 失败体未经尽力读取"
    assert "parseJsonLoose" not in source, "绕过尽力读取助手私解析失败体"
    assert "await response.json()" not in source, "回归裸 response.json()"
    for literal in ('error.name === "TimeoutError"', 'error.name === "AbortError"'):
        assert literal not in source, "残留私有中止判定"
    for message in (
        "图库加载失败，请稍后重试。",
        "图库加载超时，请稍后重试。",
        "图库响应异常，请稍后重试。",
        "图库渲染失败，请稍后重试。",
    ):
        assert message in source, f"图库分流文案丢失: {message}"


def test_gallery_browse_binds_timeout_signal() -> None:
    """图库 browse 请求绑定超时 signal，停滞请求最终走中止分流并释放忙碌态。"""
    source = _js_source("gallery.js")
    _assert_imported_from_api(source, "timeoutSignal")
    assert "signal: timeoutSignal(" in source, "browse 请求未绑定超时 signal"


def test_refs_fallback_budget_matches_server_derivation() -> None:
    """refs.js 回退预算常量与服务端按默认请求体上限的推导一致。"""
    refs_js = _js_source("refs.js")
    match = re.search(r"DEFAULT_UPLOAD_BUDGET_CHARS = (\d+) \* 1024 \* 1024;", refs_js)
    assert match, "refs.js 回退预算常量形态改变，守护需同步"
    default_body = SeedreamConfig(api_key="guard-key").http_max_body_size
    assert int(match.group(1)) * 1024 * 1024 == _upload_budget_chars(default_body)


def test_upload_accept_matches_supported_mimes() -> None:
    """上传 accept 类型列表与服务端支持格式的 MIME 集合一致。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    tag = re.search(r'<input[^>]*id="ref-file"[^>]*>', html)
    assert tag, "入口页缺失 ref-file 上传输入"
    accepted = re.search(r'accept="([^"]+)"', tag.group(0))
    assert accepted, "上传输入缺失 accept 类型列表"
    expected = {MIME_BY_EXTENSION[ext] for ext in SUPPORTED_IMAGE_EXTENSIONS_ORDERED}
    assert set(accepted.group(1).split(",")) == expected


def test_tool_slug_literals_confined_to_api_js() -> None:
    """带引号工具 slug 字面量仅存于 api.js 常量定义处。"""
    slugs = ("text-to-image", "image-to-image", "multi-image-fusion", "sequential-generation")
    for name in ("generate.js", "gallery.js", "main.js", "refs.js"):
        source = _js_source(name)
        for slug in slugs:
            assert f'"{slug}"' not in source, f"{name} 出现脱离常量定义的 slug 字面量: {slug}"


async def test_static_direct_output_requires_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
