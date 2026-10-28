"""Web 操作台静态资源与前端契约守护：打包漏文件与口径漂移在测试期即暴露。

wheel 与 sdist 的打包清单由 CI 侧断言另行锁定，本文件守护源码树形态：
入口页、404 页、五个前端 JS 模块齐备，且入口页引用的静态相对路径全部可解析。
前端 JS 的解析、消费骨架、分页、超时与善后口径以结构锚点契约逐域守护；前端
回退常量与 accept 列表同服务端推导对账；静态直出响应头的缓存口径亦在此守护。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _lib.web_fixtures import (
    build_web_app,
    function_body,
    prepare_static_dir,
    read_js_module_source,
    web_get,
    write_workspace_config,
)
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.formats import MIME_BY_EXTENSION, SUPPORTED_IMAGE_EXTENSIONS_ORDERED
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
from seedream_mcp.webapp.meta import _upload_budget_chars

_JS_DIR = "js"
_JS_MODULES = ("api.js", "generate.js", "gallery.js", "main.js", "refs.js")


def _assert_imported_from_api(source: str, *names: str) -> None:
    """断言指定符号经统一导入块引自 api.js，不私有复制。"""
    block = re.search(r'import \{[^}]*\} from "\./api\.js";', source)
    assert block is not None, "缺 api.js 导入块"
    for name in names:
        assert name in block.group(0), f"未从 api.js 引入 {name}"


# 字符串字面量优先整体归组，注释（// 与块注释）命中后剔除，见 _js_code_without_comments。
_JS_COMMENT_OR_STRING = re.compile(
    r"""("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|//[^\n]*|/\*.*?\*/""",
    re.DOTALL,
)


def _js_code_without_comments(source: str) -> str:
    """剔除 JS 注释（换行保留、其余等长空白占位），结构锚定位不被注释文本欺骗。"""

    def blank(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return match.group(1)
        return re.sub(r"[^\n]", " ", match.group(0))

    return _JS_COMMENT_OR_STRING.sub(blank, source)


def _balanced_brace_block(code: str, open_idx: int) -> tuple[str, int]:
    """自 open_idx 的“{”起按花括号配平提取块，返回块文本与收尾“}”索引。"""
    depth = 0
    for idx in range(open_idx, len(code)):
        if code[idx] == "{":
            depth += 1
        elif code[idx] == "}":
            depth -= 1
            if depth == 0:
                return code[open_idx : idx + 1], idx
    raise AssertionError("花括号不配平，结构锚提取失配")


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


def test_tool_slug_literals_confined_to_api_js() -> None:
    """带引号工具 slug 字面量仅存于 api.js 常量定义处。"""
    slugs = ("text-to-image", "image-to-image", "multi-image-fusion", "sequential-generation")
    for name in ("generate.js", "gallery.js", "main.js", "refs.js"):
        source = read_js_module_source(name)
        for slug in slugs:
            assert f'"{slug}"' not in source, f"{name} 出现脱离常量定义的 slug 字面量: {slug}"


def test_frontend_js_modules_exist() -> None:
    """五个前端 JS 模块齐备，缺任一即破坏入口模块导入图。"""
    for name in _JS_MODULES:
        assert (STATIC_DIR / _JS_DIR / name).is_file(), f"前端模块缺失: {name}"


def test_bearer_token_uses_session_storage_only() -> None:
    """Bearer 令牌只经 sessionStorage 会话内暂存，不得回退 localStorage。"""
    for name in ("api.js", "main.js"):
        assert "localStorage" not in read_js_module_source(name), f"{name} 出现 localStorage 持久化"
    assert "sessionStorage" in read_js_module_source("api.js")


def test_url_reference_goes_through_add_reference() -> None:
    """URL 手输参考图经 addReference 汇聚，不得绕过其上限与容量防护。

    无 JS 运行时测试基建，以稳定结构锚点为契约：消费侧调用形态存在、消费侧
    无直写 state.refs 的绕过、refs.js 保有汇聚实现。
    """
    main_js = read_js_module_source("main.js")
    refs_js = read_js_module_source("refs.js")
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
        source = read_js_module_source(name)
        assert "web_path" in source, f"{name} 不再消费 web_path 字段"
        assert "/web/api/thumbnail" in source, f"{name} 不再请求 /web/api/thumbnail 端点"
    gallery_js = read_js_module_source("gallery.js")
    assert "/web/api/image" in gallery_js, "gallery.js 灯箱不再请求 /web/api/image 端点"


def test_api_js_parse_contract() -> None:
    """api.js 解析契约：JSON 解析出口唯一，类型化错误与关键文案俱在。

    宽松解析以裸 response.json() 为出口，且以 SyntaxError 区分非法
    JSON（undefined 哨兵）与体读取失败（原样上抛）；严格解析拒绝非对象载荷
    （数组/标量）并归 bad_response 档；错误体尽力读取助手吞非中止类异常、
    中止类上抛交调用方分档，非 ok 分支的消费契约由 postJson 共享骨架守护。
    """
    source = read_js_module_source("api.js")
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
    guard = function_body(source, r"export async function readErrorPayload.*?^}")
    assert "await parseJsonLoose(response)" in guard, "错误体助手未复用宽松解析"


def test_api_js_post_json_skeleton_contract() -> None:
    """api.js 的 JSON-POST 共享消费骨架：请求、401 吞、错误归一与严格解析单点收敛。

    gallery 与 generate 的 JSON-POST 消费骨架收敛于此，任一要素缺失或回流
    调用方都会使两域的失败分流口径漂移。
    """
    source = read_js_module_source("api.js")
    body = function_body(source, r"export async function postJson\(.*?^}")
    for literal in (
        "await apiFetch(path,",
        "isUnauthorizedError(error)",
        "rethrowIfAbort(error)",
        "await readErrorPayload(response)",
        "normalizePayloadError(",
        "await parseJsonRequired(response)",
        "error instanceof PayloadError",
        "if (!(error instanceof PayloadError)) throw error;",
    ):
        assert literal in body, f"消费骨架缺要素: {literal}"


def test_api_js_post_json_raw_contract() -> None:
    """api.js 原始 JSON-POST 助手：无信封语义端点（生成 preflight）的请求出口。

    与共享消费骨架的差别在不套消费语义：失败不逐档展示、载荷不套工具信封，
    网络、非 2xx 与载荷异常一律上抛交调用方 catch 分档；401 仍经请求层弹令牌门。
    body 缺省时发无体请求，预检投影只携带查询参数；非 ok 弃体与严格解析收敛
    于 consumeJsonOrThrow 共享响应尾。
    """
    source = read_js_module_source("api.js")
    body = function_body(source, r"export async function postJsonRaw\(.*?^}")
    assert "postJsonRaw(path, timeoutMs, body)" in body, "body 须为可缺省尾参"
    for literal in (
        "withTimeoutSignal(timeoutMs,",
        "await apiFetch(path,",
        "if (body !== undefined)",
        "options.body = JSON.stringify(body);",
        "return consumeJsonOrThrow(response);",
    ):
        assert literal in body, f"原始 JSON-POST 缺要素: {literal}"
    tail = function_body(source, r"async function consumeJsonOrThrow\(.*?^}")
    for literal in (
        "await discardBodyQuietly(response);",
        "return parseJsonRequired(response);",
    ):
        assert literal in tail, f"共享响应尾缺要素: {literal}"


def test_error_branch_discard_absorbed() -> None:
    """错误分支的弃体异常统一经 discardBodyQuietly 吸收，坏体流不得劫持 401 门禁与失败分流。"""
    api_js = read_js_module_source("api.js")
    helper = function_body(api_js, r"export async function discardBodyQuietly\(.*?^}")
    for name in ("apiFetch", "fetchBlobUrl"):
        anchor = function_body(api_js, rf"async function {name}\(.*?^}}")
        assert "await discardBodyQuietly(response);" in anchor, f"{name} 错误分支弃体未吸收异常"
    # 原始 JSON 端点的弃体收敛于共享响应尾，两个包装器不再各自手写。
    tail = function_body(api_js, r"async function consumeJsonOrThrow\(.*?^}")
    assert "await discardBodyQuietly(response);" in tail, "共享响应尾弃体未吸收异常"
    for name in ("getJsonRaw", "postJsonRaw"):
        anchor = function_body(api_js, rf"async function {name}\(.*?^}}")
        assert "consumeJsonOrThrow(response" in anchor, f"{name} 未复用共享响应尾"
    # config-info 拉取的弃体收敛于 api.js 的 getJsonRaw 骨架，仅图库灯箱仍手写。
    gallery_js = read_js_module_source("gallery.js")
    _assert_imported_from_api(gallery_js, "discardBodyQuietly")
    anchor = function_body(gallery_js, r"async function fetchLightboxOriginal\(.*?^}")
    assert (
        "await discardBodyQuietly(response);" in anchor
    ), "fetchLightboxOriginal 错误分支弃体未吸收异常"
    # 手抄惯用形在助手本体之外零残留，助手自身的 try/catch 是唯一合法形态。
    assert not re.search(
        r"try \{\s*await response\.body\?\.cancel\(\);\s*\} catch",
        api_js.replace(helper, ""),
    ), "api.js 残留手抄的弃体吞异常惯用形"
    for module in ("gallery.js", "generate.js"):
        assert not re.search(
            r"try \{\s*await response\.body\?\.cancel\(\);\s*\} catch",
            read_js_module_source(module),
        ), f"{module} 残留手抄的弃体吞异常惯用形"
    skeleton = function_body(api_js, r"export async function postJson\(.*?^}")
    assert re.search(
        r"try \{\s*errorPayload = await readErrorPayload\(response\);\s*\} catch",
        skeleton,
    ), "postJson 非 ok 失败体读取未吸收中止类"


def test_generate_js_parse_contract() -> None:
    """generate.js 解析契约：JSON-POST 消费经 api.js 共享骨架，四档失败文案俱在。

    解析与中止判定统一引自 api.js；非 ok 失败体与 ok 载荷解析收敛于 postJson
    骨架，非 ok 失败体读取异常（含中止）骨架内吸收、ok 载荷体读取失败上抛走
    网络档；失败分流（令牌、超时、网络、生成失败）的用户文案不因重构丢失。
    """
    source = read_js_module_source("generate.js")
    _assert_imported_from_api(source, "isAbortError", "getJsonRaw", "postJson")
    assert "`/web/api/generate/${tool}`" in source, "生成请求未经共享消费骨架"
    # config-info 为 GET 拉取，请求、非 2xx 弃体与严格解析收敛于 api.js 的
    # getJsonRaw 骨架，本地仅留失败文案；POST 端点的解析收敛于共享消费骨架。
    fetch = function_body(source, r"async function fetchConfigInfo\(.*?^}")
    assert "await getJsonRaw(" in fetch, "config-info 拉取未复用原始 GET-JSON 骨架"
    assert '"config-info 请求失败"' in fetch, "config-info 失败文案丢失"
    # 解析与弃体助手已内聚于 api.js 骨架，调用方不得绕过骨架直接消费。
    for name in ("parseJsonRequired", "discardBodyQuietly"):
        assert name not in source, f"{name} 已内聚于 api.js 骨架，generate.js 不得直接引用"
    for message in ("需要重新输入令牌。", "生成耗时异常。", "请求失败。", "生成失败。"):
        assert message in source, f"失败分流文案丢失: {message}"


def test_generate_config_rerender_resets_paging_via_gallery_domain() -> None:
    """配置重渲染的格式恢复失败路径经 resetGalleryPaging 复位分页。

    裸置偏移不清回退栈，分页状态回不到首页初始态；分页状态操作
    收敛 gallery 域。
    """
    source = read_js_module_source("generate.js")
    assert re.search(
        r'import \{[^}]*resetGalleryPaging[^}]*\} from "\./gallery\.js";', source
    ), "未从 gallery.js 引入分页复位"
    render = function_body(source, r"function renderConfigInfo\(.*?^}")
    assert "resetGalleryPaging()" in render, "配置渲染路径未复位分页（清回退栈）"


def test_gallery_js_parse_contract() -> None:
    """gallery.js 解析契约：JSON-POST 消费经 api.js 共享骨架，四档图库文案俱在。

    非 ok 失败体读取异常（含中止）骨架内吸收；ok 载荷读取中止类上抛走外层
    超时档、其余体读取失败走网络档。
    """
    source = read_js_module_source("gallery.js")
    _assert_imported_from_api(source, "isAbortError", "postJson")
    for message in (
        "图库加载失败，请稍后重试。",
        "图库加载超时，请稍后重试。",
        "图库响应异常，请稍后重试。",
        "图库渲染失败，请稍后重试。",
    ):
        assert message in source, f"图库分流文案丢失: {message}"


def test_gallery_browse_binds_timeout_signal() -> None:
    """图库 browse 请求绑定共享超时档，停滞请求最终走中止分流并释放忙碌态。"""
    source = read_js_module_source("gallery.js")
    _assert_imported_from_api(source, "IMAGE_FETCH_TIMEOUT_MS")
    assert re.search(
        r'postJson\("/web/api/browse", body, IMAGE_FETCH_TIMEOUT_MS,', source
    ), "browse 超时档未随共享消费骨架绑定"


def test_gallery_has_more_tri_state_contract() -> None:
    """图库 has_more 三态透传：null 不拍平，翻页与计数按三态分流。

    后端 null 表示扫描截断或停滞、更多未知，拍平成 false 会使下一页被禁用、
    翻页成死路；has_more 键缺失按无更多保守处理。停滞页续扫偏移由后端随
    next_offset 下发（跳过本页已取得条目，空停滞页等于原偏移原地重试，服务端
    按上次停滞窗口倍增扩窗），前端不自算步进；空页文案须如实改述扫描未完成
    （可续扫导引重试、终态改述原因），不得以「没有图片」误称扫尽。推进资格经
    canAdvanceGallery 单一谓词判定（按钮禁用与点击 handler 同点），
    advanceGalleryOffset 为纯变更器不重复持守卫；next_offset 并入
    state.gallery 单一状态持有，prev 经回退栈回跳。
    """
    gallery_js = read_js_module_source("gallery.js")
    main_js = read_js_module_source("main.js")
    # 严格等三态投影：显式 null 归未知态，键缺失按无更多保守（不可推进）。
    assert (
        "payload.has_more === null ? null : payload.has_more === true" in gallery_js
    ), "null 态被拍平，翻页死路回归"
    for literal in ("（可能还有更多）", "（还有更多）", "（扫描未完成）"):
        assert literal in gallery_js, f"三态计数文案缺失: {literal}"
    assert (
        "扫描未完成，本页暂无图片；点击「下一页」重试扫描。" in gallery_js
    ), "空停滞页缺如实状态与重试导引文案"
    empty_text = function_body(gallery_js, r"function galleryEmptyText\(.*?^}")
    assert (
        "gallery.hasMore === null && gallery.items.length === 0" in empty_text
    ), "空停滞页文案未限定空列表"
    advance = function_body(gallery_js, r"export function advanceGalleryOffset.*?^}")
    assert "canAdvanceGallery" not in advance, "推进变更器重复持有翻页谓词"
    assert (
        "state.gallery.offset = state.gallery.nextOffset" in advance
    ), "推进未消费后端 next_offset"
    assert "export function canAdvanceGallery" in gallery_js, "缺三态谓词单一属主导出"
    assert re.search(
        r'import \{[^}]*canAdvanceGallery[^}]*\} from "\./gallery\.js";', main_js
    ), "点击 handler 未引入翻页谓词"
    assert re.search(
        r"if \(canAdvanceGallery\(\)\) \{\s*advanceGalleryOffset\(\);\s*refreshGallery\(\);\s*\}",
        main_js,
    ), "点击未按翻页谓词判定后推进并刷新"
    assert "payload.next_offset" in gallery_js, "推进未持久化后端 next_offset"
    assert "payload.stall_permanent" in gallery_js, "永久停滞态未从载荷显式消费"
    assert "payload.rescan_futile" in gallery_js, "截断徒劳态未从载荷显式消费"
    assert "state.gallery.nextOffset" in gallery_js, "next_offset 未并入分页状态"
    assert "state.gallery.backStack.push(state.gallery.offset)" in gallery_js, "推进前未压回退栈"
    assert "state.gallery.backStack.pop()" in gallery_js, "prev 未弹回退栈回跳"
    # 初始态单源于 api.js 工厂，state 初始化与 gallery 域整体复位共用。
    api_js = read_js_module_source("api.js")
    assert "gallery: initialGalleryState()," in api_js, "state 初始化未消费初始态工厂"
    factory = function_body(api_js, r"export function initialGalleryState\(.*?^}")
    for field in (
        "offset",
        "backStack",
        "hasMore",
        "nextOffset",
        "stallPermanent",
        "rescanFutile",
        "items",
    ):
        assert f"{field}:" in factory, f"初始态工厂缺字段: {field}"
    _assert_imported_from_api(gallery_js, "initialGalleryState")
    reset = function_body(gallery_js, r"export function resetGalleryPaging.*?^}")
    assert "state.gallery = initialGalleryState();" in reset, "分页复位未整体置换初始态"
    assert "resetGalleryPaging()" in main_js, "复位路径未委托图库域清栈"


def test_gallery_permanent_stall_disables_advance() -> None:
    """永久停滞态（stall_permanent 显式下发）禁用下一页并改述空页文案。

    永久停滞是后端对目录持续无法访问的有意终态，后端对置位且更多未知的页
    撤下 next_offset，推进闸以其非空为主臂，保留下一页入口只会引导无效重试；
    hasMore 臂仅拦 has_more 键缺失的契约漂移保守面。空页文案如实改述目录
    不可访问，不与续扫导引混用。冻结窗回放可使永久停滞与 has_more 真共存
    （next_offset 照常下发），满页的已知条目仍可推进。
    """
    gallery_js = read_js_module_source("gallery.js")
    guard = function_body(gallery_js, r"function canAdvanceGallery\(.*?^}")
    assert re.search(
        r"state\.gallery\.nextOffset !== null\s*&&\s*state\.gallery\.hasMore !== false",
        guard,
    ), "推进闸形态漂移：主臂拦终态撤下的 next_offset，次臂拦 has_more 键缺失"
    assert "目录持续无法访问，请稍后重试。" in gallery_js, "永久停滞空页文案缺失"


def test_gallery_rescan_futile_disables_advance() -> None:
    """空截断徒劳页（rescan_futile 显式下发，非空窗口耗尽页亦可置位）禁用下一页并改述空页文案。

    截断预算与偏移无关，空截断页同偏移重扫必复现同一空窗，后端对此与永久
    停滞同撤 next_offset 并置 rescan_futile；推进闸保住 next_offset 非空主臂
    即封死空截断页的全预算重扫循环。空页文案如实改述目录条目过多已截断，
    不与续扫导引混用。
    """
    gallery_js = read_js_module_source("gallery.js")
    guard = function_body(gallery_js, r"function canAdvanceGallery\(.*?^}")
    assert (
        "state.gallery.nextOffset !== null" in guard
    ), "推进闸缺 next_offset 主臂，空截断页可回退全预算重扫循环"
    assert "扫描未完成，本页暂无图片；目录条目过多已截断。" in gallery_js, "空截断页文案缺失"


def test_gallery_fetch_page_rechecks_seq_before_failure_display() -> None:
    """取数失败的展示回调复查请求序号：被取代请求的失败不覆盖新请求的界面状态。"""
    gallery_js = read_js_module_source("gallery.js")
    fetcher = function_body(gallery_js, r"async function fetchGalleryPage\(.*?^}")
    assert "if (seq === requestSeq) showGalleryError(message);" in fetcher, "失败展示缺请求序号复查"


def test_gallery_error_path_clears_pager_dom() -> None:
    """失败路径经 clearPagerDom 清空翻页器并维持禁用，旧可用态不把用户引向空页。"""
    gallery_js = read_js_module_source("gallery.js")
    shower = function_body(gallery_js, r"function showGalleryError\(.*?^}")
    assert "clearPagerDom();" in shower, "失败路径未复位翻页器"


def test_gallery_count_line_gates_unknown_hint_on_terminal_states() -> None:
    """计数行 null 态分流：永久停滞与截断徒劳是终态，不再追加「可能还有更多」。

    截断徒劳与永久停滞同撤续扫偏移（下一页已禁用），非空页的未知提示须改述
    截断原因，口径对齐服务端未完成标记「目录条目过多，结果可能不完整」。
    """
    gallery_js = read_js_module_source("gallery.js")
    counter = function_body(gallery_js, r"function galleryCountText\(.*?^}")
    assert "!shownCount || stallPermanent" in counter, "计数行未按永久停滞收敛未知提示"
    assert (
        'if (rescanFutile) return "（目录条目过多，结果可能不完整）";' in counter
    ), "截断徒劳终态未改述服务端截断口径"
    render = function_body(gallery_js, r"async function refreshGalleryForSeq\(.*?^}")
    assert re.search(
        r"galleryCountText\(\s*state\.gallery\.hasMore,\s*payload\.total_count,\s*count,\s*"
        r"state\.gallery\.stallPermanent,\s*state\.gallery\.rescanFutile,?\s*\)",
        render,
    ), "计数行未消费截断徒劳态"


def test_gallery_empty_window_reports_skipped_entries() -> None:
    """SKIPPED 收尾的空窗如实改述部分图片无法访问并带跳过数，不误称目录无图。

    永久停滞的跳过不是瞬时故障，口径随 stall_permanent 取「持续」，与目录
    持续无法访问的空页终态改述同轴，避免同一终态在两处口径矛盾。
    """
    gallery_js = read_js_module_source("gallery.js")
    empty_text = function_body(gallery_js, r"function galleryEmptyText\(.*?^}")
    assert "skippedCount" in empty_text, "空窗文案未消费 skipped_count"
    assert 'gallery.stallPermanent ? "持续" : "暂时"' in empty_text, "跳过文案未按永久停滞区分口径"
    assert "无法访问（跳过 ${skippedCount} 张）。`" in empty_text, "SKIPPED 空窗缺如实改述文案"
    render = function_body(gallery_js, r"async function refreshGalleryForSeq\(.*?^}")
    assert re.search(
        r"galleryEmptyText\(\s*state\.gallery,\s*payload\.skipped_count,\s*format,?\s*\)",
        render,
    ), "空页文案未单点收敛到 galleryEmptyText"


def test_gallery_pool_stop_aftercare_by_stop_reason() -> None:
    """缩略图池停止消费的善后按停止原因分流：401 落过期终态，序号取代不盖终态。

    401 停止时当前条目与未认领条目统一落令牌过期终态，不留永久未决占位；被新
    请求取代（序号过期）的停止不做善后，仍可见旧页的未决占位留待新请求重渲染
    定夺，失败的新请求保留未决占位比假终态诚实。
    """
    gallery_js = read_js_module_source("gallery.js")
    assert "const unconsumed = await runBoundedPool(" in gallery_js, "图库未接住未消费回传"
    assert "令牌已过期，重新输入后请刷新图库" in gallery_js, "401 过期终态文案丢失"
    assert "markAuthExpired" in gallery_js, "缺过期终态标注"


def test_gallery_unavailable_root_releases_thumbnail_pool() -> None:
    """图片目录不可用分支回收图库缩略图池，已渲染页不泄漏对象 URL。

    目录不可用分支漏回收会使已渲染页的缩略图 blob URL 悬置到目录恢复并
    成功刷新才释放。
    """
    gallery_js = read_js_module_source("gallery.js")
    assert 'revokeObjectUrls("gallery");' in gallery_js, "图库域缺缩略图池回收"


def test_gallery_lightbox_budget_scales_with_content_length() -> None:
    """灯箱两段超时共用单控制器，体读取预算按 Content-Length 或条目 size_mb 放大封顶。"""
    gallery_js = read_js_module_source("gallery.js")
    budget = function_body(gallery_js, r"function lightboxBodyBudgetMs\(.*?^}")
    scaled = function_body(gallery_js, r"function scaledBodyBudgetMs\(.*?^}")
    for literal in (
        "Content-Length",
        "sizeMb",
        "LIGHTBOX_MIN_BYTES_PER_SEC",
        "LIGHTBOX_MAX_TIMEOUT_MS",
        "IMAGE_FETCH_TIMEOUT_MS",
        "Math.ceil",
    ):
        assert literal in budget + scaled, f"预算推导缺要素: {literal}"
    assert re.search(
        r"fetchLightboxOriginal\(\s*item\.web_path,\s*seq,\s*errorEl,\s*item\.size_mb,?\s*\)",
        gallery_js,
    ), "灯箱装载未携带条目 size_mb，缺长度响应退共享档"
    assert re.search(
        r"openLightbox\(\{\s*web_path: item\.path,\s*size_mb: item\.size_mb\s*\}\)",
        gallery_js,
    ), "图库条目开灯箱丢弃 size_mb，size_mb 回退级在唯一携带路径不可达"
    body = function_body(gallery_js, r"async function fetchLightboxOriginal\(.*?^}")
    # 超时武装与中止理由构造单源于 api.js 的 armTimeout，fetch 前按共享档武装，
    # 头部就位后按预算重臂，finally 一次撤销。
    _assert_imported_from_api(gallery_js, "armTimeout")
    assert re.search(
        r"disarm = armTimeout\(controller,\s*IMAGE_FETCH_TIMEOUT_MS\);", body
    ), "fetch 前未按共享档武装"
    assert re.search(r"finally\s*\{\s*disarm\(\);\s*\}", body), "计时器未在 finally 撤销"


def test_gallery_advance_guards_missing_next_offset() -> None:
    """next_offset 缺失的契约破坏不改状态；空停滞页原偏移原地重扫不污染回退栈。"""
    advance = function_body(
        read_js_module_source("gallery.js"), r"export function advanceGalleryOffset.*?^}"
    )
    assert "state.gallery.nextOffset == null" in advance, "缺 next_offset 空值防御"
    assert re.search(
        r"if \(state\.gallery\.nextOffset !== state\.gallery\.offset\) \{\s*"
        r"state\.gallery\.backStack\.push\(state\.gallery\.offset\);\s*\}",
        advance,
    ), "压栈未限定目标偏移不同，原地重扫污染回退栈"
    assert (
        "state.gallery.offset = state.gallery.nextOffset" in advance
    ), "推进未无条件消费后端 next_offset"


def test_local_image_fetches_bind_timeout_signal() -> None:
    """本服务取图超时单源于共享档：fetchBlobUrl 内建 60 秒武装，两域调用侧
    直投不包装，停滞请求不挂起 worker。"""
    api_js = read_js_module_source("api.js")
    assert "export const IMAGE_FETCH_TIMEOUT_MS = 60000;" in api_js
    helper = function_body(api_js, r"export async function fetchBlobUrl\(.*?^}")
    assert re.search(
        r"armTimeout\(controller, IMAGE_FETCH_TIMEOUT_MS\)", helper
    ), "helper 未内建共享档超时武装"
    assert re.search(r"finally\s*\{\s*disarm\(\);", helper), "helper 计时器未在 finally 撤销"
    # 灯箱原图自持计时器按同档起臂，形态由灯箱预算守护另行钉住；两域缩略图
    # 调用侧不再包装，超时随 helper 内建档生效。
    for module in ("gallery.js", "generate.js"):
        assert not re.search(
            r"withTimeoutSignal\(\s*IMAGE_FETCH_TIMEOUT_MS\b",
            read_js_module_source(module),
        ), f"{module} 取图调用侧残留超时包装"
    assert "IMAGE_FETCH_TIMEOUT_MS" not in read_js_module_source("generate.js")


def test_bounded_pool_shared_without_private_scaffolding() -> None:
    """并发取图池单源于 api.js 的 runBoundedPool，两域消费共享实现。"""
    assert "export async function runBoundedPool" in read_js_module_source("api.js")
    assert "return entries.slice(cursor);" in read_js_module_source(
        "api.js"
    ), "并发池未回传未消费条目"
    for name in ("gallery.js", "generate.js"):
        assert "runBoundedPool(" in read_js_module_source(name), f"{name} 未消费共享并发池"


def test_generate_submit_full_path_wrapped_by_restore_finally() -> None:
    """提交处理器全程 try/finally：状态复位、快照、预检、发送到渲染都在恢复块内。

    装配期意外异常（如 DOM 结构漂移使取值抛错）曾逸出恢复范围，按钮永久停留
    等待文案直到刷新；任何异常路径现在都收场失败态并由 finally 清提交占用、
    恢复按钮。
    """
    source = read_js_module_source("generate.js")
    text = function_body(source, r"export async function submitGenerate\(.*?^}")
    # 剔除注释后按花括号配平提取 try/catch/finally 子句，锚点先现于注释不再能假通过。
    code = _js_code_without_comments(text)
    try_block, try_end = _balanced_brace_block(code, code.index("try {") + len("try "))
    for anchor in (
        'setStatus("running", "生成中，请稍候");',
        "const body = buildRequestBody();",
        "await preflightTimeoutMs(tool, body);",
        "await postJson(",
        "await renderResults(payload);",
    ):
        assert anchor in try_block, f"提交主路径步骤逸出恢复块: {anchor}"
    catch_head = re.compile(r"\s*catch\s*\(\w+\)\s*\{").match(code, try_end + 1)
    assert catch_head is not None, "全程 try 未直接挂 catch 收场"
    catch_block, catch_end = _balanced_brace_block(code, catch_head.end() - 1)
    assert "showNetworkFailure(error);" in catch_block, "装配期异常未收场失败提示"
    finally_head = re.compile(r"\s*finally\s*\{").match(code, catch_end + 1)
    assert finally_head is not None, "全程 try 未直接挂 finally 恢复"
    finally_block, _ = _balanced_brace_block(code, finally_head.end() - 1)
    for anchor in ("activeSubmitAbort = null;", 'button.textContent = "生成图片";'):
        assert anchor in finally_block, f"按钮恢复逸出 finally: {anchor}"


def test_generate_submit_inflight_button_cancels_request() -> None:
    """生成等待期主按钮转「取消」：再次提交即中止在途请求并收场已取消提示。

    取消只中止客户端等待，服务端是否继续执行不受影响；取消分流以取消信号
    区分于超时兜底，超时文案路径保持原状。
    """
    generate_js = read_js_module_source("generate.js")
    api_js = read_js_module_source("api.js")
    submit = function_body(generate_js, r"export async function submitGenerate\(.*?^}")
    assert "let activeSubmitAbort = null;" in generate_js, "缺在途提交占用标记"
    assert re.search(
        r"if \(activeSubmitAbort\) \{\s*activeSubmitAbort\.abort\(\);\s*return;\s*\}",
        submit,
    ), "等待期再次提交未解释为取消"
    assert 'button.textContent = "取消";' in submit, "等待期主按钮未转为取消"
    assert '"已取消。"' in submit, "取消收场文案丢失"
    assert re.search(
        r"isAbortError\(error\) && cancelController\.signal\.aborted", submit
    ), "取消与超时未按取消信号分流"
    assert "cancelController.signal," in submit, "生成请求未携带取消信号"
    skeleton = function_body(api_js, r"export async function postJson\(.*?^}")
    assert "AbortSignal.any(" in skeleton, "postJson 未汇流调用方取消与内部超时"


def test_generate_submit_binding_has_fallback_catch() -> None:
    """main.js 提交绑定带兜底 catch：处理器外溢的残余异常不落未处理拒绝。"""
    main_js = read_js_module_source("main.js")
    assert re.search(
        r'\$\("generate-form"\)\.addEventListener\(\s*"submit",\s*\(event\) => \{\s*'
        r"submitGenerate\(event\)\.catch\(",
        main_js,
    ), "提交绑定缺兜底 catch"


def test_generate_network_failure_closure_pairs_status_and_card() -> None:
    """网络失败收口单源闭包：状态行失败态与卡片网络档标注成对渲染。

    请求期 catch、装配期兜底 catch 与 postJson 的 onNetworkError 回调共用
    该闭包，拆散会使任一消费点的失败分流口径漂移。
    """
    source = read_js_module_source("generate.js")
    closure = function_body(source, r"function showNetworkFailure\(.*?^}")
    assert 'setStatus("failed", "请求失败。");' in closure, "网络失败闭包缺失败态收场"
    assert 'type: "network"' in closure, "网络失败闭包缺网络档标注"


def test_generate_render_failure_settles_as_saved() -> None:
    """2xx 后渲染期异常按已成功收场：不伪装请求失败诱导重复提交，中止类交回外层超时档。"""
    source = read_js_module_source("generate.js")
    submit = function_body(source, r"export async function submitGenerate\(.*?^}")
    code = _js_code_without_comments(submit)
    render_at = code.index("await renderResults(payload);")
    settled_at = code.index("生成已成功，结果已保存到图库；展示部分失败。")
    assert render_at < settled_at, "已成功收场文案逸出渲染异常分支"
    assert "rethrowIfAbort(error);" in submit, "渲染期异常未交回中止类"
    assert 'console.error("结果渲染失败:", error);' in submit, "渲染期异常缺排障日志"


def test_generate_bootstrap_reload_rerenders_config_ui() -> None:
    """配置拉取链为纯数据拉取加显式渲染，后台重载补渲染配置 UI 且在途不叠加。

    纯数据拉取只落 state.configInfo 不重跑渲染，
    瞬时失败会使尺寸档位与工具形态永久空白、请求体静默缺 size；
    启动装载保持拉取加渲染的两步组合，
    后台重载复用可中止的同一拉取再补渲染与工具形态
    （渲染后于拉取落定，失败不半渲染），
    server-meta 由渲染路径改写即清除失败提示，在途重载不叠加第二次、落定清空在途 Promise。
    """
    source = read_js_module_source("generate.js")
    main_js = read_js_module_source("main.js")
    fetch = function_body(source, r"async function fetchConfigInfo\(.*?^}")
    assert re.search(
        r'getJsonRaw\(\s*"/web/api/config-info",\s*signal', fetch
    ), "中止信号未透传配置拉取骨架"
    load = function_body(source, r"export async function loadConfigInfo\(.*?^}")
    for anchor, meaning in (
        ("await fetchConfigInfo();", "启动路径未复用纯数据拉取"),
        ("renderConfigInfo();", "启动路径拉取后未渲染配置"),
    ):
        assert anchor in load, meaning
    assert re.search(r"let bootstrapReloadInFlight\b", source), "缺启动重载在途 Promise 声明"
    body = function_body(source, r"export function prefetchConfigInfo\(.*?^}")
    assert re.search(
        r"withTimeoutSignal\(\s*CONFIG_REFETCH_TIMEOUT_MS", body
    ), "启动重载未按后台总上限限时，停滞请求锁死在途 Promise"
    for anchor, meaning in (
        ("fetchConfigInfo(signal);", "重载未复用可中止的配置拉取"),
        ("renderConfigInfo();", "重载成功未重跑配置渲染"),
        ("applyToolUI();", "重载成功未重套工具形态"),
    ):
        assert anchor in body, meaning
    assert "return bootstrapReloadInFlight;" in body, "重载在途未防重入叠加"
    assert "bootstrapReloadInFlight = null;" in body, "重载落定未清在途 Promise，后续重载被永久锁死"
    assert "prefetchConfigInfo()" in main_js, "启动失败后台重载未触发"


def test_generate_unauthorized_stop_annotates_remaining_cards() -> None:
    """结果卡 401 停止消费：命中卡标注过期重试，未装载卡落中性终态不断言过期。

    未消费只代表停止取图，不等于令牌过期；持续 401 突发下统一标过期会诱导
    整批误判并重复计费生成，故善后循环用中性收场文案，图库可用时附找回指引。
    """
    generate_js = read_js_module_source("generate.js")
    assert "令牌已过期，重新输入后请再次生成" in generate_js, "过期标注文案丢失"
    assert "for (const entry of unconsumed)" in generate_js, "未消费条目无统一善后"
    loop = re.search(
        r"for \(const entry of unconsumed\)\s*\{[^{}]*\}",
        generate_js,
    )
    assert loop is not None, "未消费善后循环缺函数体锚"
    body = loop.group(0)
    assert "结果图加载中断，可从图库找回" in body, "未消费卡片缺图库可用时的找回指引"
    assert "images_root_available" in generate_js, "图库可用性状态源未接入未消费卡片的找回指引"


def test_timeout_signal_timer_disarmed_after_use() -> None:
    """超时计时自含于 withTimeoutSignal：武装经 armTimeout 单源，finally 撤销同函数。"""
    api_js = read_js_module_source("api.js")
    helper = function_body(api_js, r"export async function withTimeoutSignal.*?^}")
    assert "export function armTimeout" in api_js, "缺可复用超时武装原语"
    assert "armTimeout(controller, ms)" in helper, "未复用 armTimeout 武装计时器"
    assert re.search(r"finally\s*\{\s*disarm\(\);\s*\}", helper), "计时器未在 finally 撤销"
    _assert_imported_from_api(read_js_module_source("generate.js"), "withTimeoutSignal")


def test_refs_fallback_budget_matches_server_derivation() -> None:
    """refs.js 回退预算常量与服务端按默认请求体上限的推导一致。"""
    refs_js = read_js_module_source("refs.js")
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
