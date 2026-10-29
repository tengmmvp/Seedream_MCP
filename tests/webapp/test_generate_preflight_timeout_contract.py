"""生成 preflight 等待预算的前后端契约：服务端 query 读取与前端构造对账。

预算单源于服务端 _preflight_timeout_ms，前端 preflightTimeoutMs 在调用点把
提交快照的请求体直接构造为预算相关的查询参数。本文件绑定两侧缝隙：预检读取
的 query 键集与前端构造的键集须一致——新增预算相关读取漏同步时守护变红，
否则客户端按缺省档预算提前中止而服务端仍在执行计费请求。
"""

from __future__ import annotations

import inspect
import re

import pytest
from starlette.requests import Request

from _lib.web_fixtures import function_body, read_js_module_source
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.definitions.conventions import (
    IMAGE_TO_IMAGE_TOOL_NAME,
    MAX_PARALLEL_REQUEST_COUNT,
)
from seedream_mcp.webapp.generate import _preflight_timeout_ms

_JS_MODULES = ("api.js", "generate.js", "gallery.js", "main.js", "refs.js")

# 服务端预检读取但不由 Web 前端构造的 query 键：parallelism 为 body 携带该
# 字段的其他客户端预留投影位，Web 表单无此字段。
_SERVER_ONLY_QUERY_KEYS = frozenset({"parallelism"})

# 两侧须同步的预期键集：影响预算推导的请求体字段到 query 键的投影。
_EXPECTED_QUERY_KEYS = frozenset(
    {
        "request_count",
        "layer_decomposition",
        "image_count",
        "max_images",
        "download_tail",
    }
)


def _frontend_query_keys(source: str) -> frozenset[str]:
    """从 preflightTimeoutMs 源码段提取前端构造的 query 键集。"""
    return frozenset(re.findall(r'params\.set\("([a-z_]+)"', source))


def _assert_server_reads_covered_by_frontend(
    server_source: str, frontend_keys: frozenset[str]
) -> None:
    """服务端预检读取的 query 键须被前端构造集并专属键精确覆盖。

    漏同步的读取键前端不构造，预算将按缺省档推导；前端构造服务端不读取的
    键则为悬空构造，发出无人消费的键。
    """
    server_keys = set(re.findall(r'query\.get\("([a-z_]+)"', server_source))
    uncovered = server_keys - frontend_keys - _SERVER_ONLY_QUERY_KEYS
    assert not uncovered, (
        f"服务端预算 query 键 {sorted(uncovered)} 前端未构造，" f"客户端将按缺省档推导预算"
    )
    assert (
        not frontend_keys - server_keys
    ), f"前端构造 query 键 {sorted(frontend_keys - server_keys)} 服务端不读取，构造悬空"


def _preflight_js_body() -> str:
    return function_body(
        read_js_module_source("generate.js"), r"async function preflightTimeoutMs\(.*?^}"
    )


def test_server_preflight_reads_match_frontend_construction() -> None:
    """两侧 query 键集对账：漏同步使客户端按缺省档预算掐断计费请求。"""
    server_source = inspect.getsource(_preflight_timeout_ms)
    frontend_keys = _frontend_query_keys(_preflight_js_body())
    _assert_server_reads_covered_by_frontend(server_source, frontend_keys)
    server_keys = frozenset(re.findall(r'query\.get\("([a-z_]+)"', server_source))
    assert (
        server_keys - _SERVER_ONLY_QUERY_KEYS == _EXPECTED_QUERY_KEYS
    ), "服务端预检读取键集漂移，预期键集守护需同步"
    assert frontend_keys == _EXPECTED_QUERY_KEYS, "前端构造键集漂移，预期键集守护需同步"


def test_new_server_query_read_without_frontend_construction_turns_guard_red() -> None:
    """模拟「服务端新增预算 query 读取未同步前端」：改写源文本喂对账检查必须变红。

    开发者在 _preflight_timeout_ms 增读新预算键后忘记同步前端构造是本守护
    针对的真实失误形态；以改写文本复现，不触碰真实文件。
    """
    source = inspect.getsource(_preflight_timeout_ms)
    mutated = source.replace(
        "query = request.query_params",
        'query = request.query_params\n    query.get("batch_hint")',
        1,
    )
    assert mutated != source, "预检取参锚失配，漏同步模拟未生效"
    with pytest.raises(AssertionError):
        _assert_server_reads_covered_by_frontend(
            mutated, _frontend_query_keys(_preflight_js_body())
        )


def test_frontend_drops_query_construction_turns_guard_red() -> None:
    """模拟「前端漏构造一个预算键」：改写源文本喂对账检查必须变红。"""
    source = _preflight_js_body()
    mutated = source.replace('params.set("max_images", String(body.max_images));', "", 1)
    assert mutated != source, "前端构造锚失配，漏构造模拟未生效"
    with pytest.raises(AssertionError):
        _assert_server_reads_covered_by_frontend(
            inspect.getsource(_preflight_timeout_ms), _frontend_query_keys(mutated)
        )


def test_frontend_reads_fields_within_build_request_body_construction() -> None:
    """预检读取的请求体字段须为 buildRequestBody 真实构造集，不读取表单从不装配的字段。"""
    generate_js = read_js_module_source("generate.js")
    preflight = _preflight_js_body()
    build = function_body(generate_js, r"function buildRequestBody\(.*?^}")
    constructed = set(re.findall(r"\bbody\.([a-z_]+)\s*=", build))
    read = set(re.findall(r"\bbody\.([a-z_]+)", preflight))
    dangling = read - constructed
    assert (
        not dangling
    ), f"预检读取字段 {sorted(dangling)} 不在 buildRequestBody 构造集内，读取语义失真"


def test_generate_preflight_submit_snapshot_and_wire_contract() -> None:
    """提交快照与预检契约：快照先于预检，查询参数自快照构造且为无体轻请求。

    提交时请求体与工具一次性快照并复用至 POST，表单不得二次读取；预检任何
    失败回退等待预算兜底且不阻断提交；config-info 已收缩的五项原料字段在
    前端不得再有任何引用。
    """
    generate_js = read_js_module_source("generate.js")
    assert re.search(
        r'import \{[^}]*postJsonRaw[^}]*\} from "\./api\.js";', generate_js
    ), "预检未复用 api.js 原始 JSON-POST 助手"
    assert re.search(
        r"const tool = state\.tool;\s*"
        r"const body = buildRequestBody\(\);\s*"
        r"const timeoutMs = await preflightTimeoutMs\(tool, body\);",
        generate_js,
    ), "提交路径未先快照请求体与工具再预检"
    assert (
        len(re.findall(r"(?<!function )buildRequestBody\(", generate_js)) == 1
    ), "请求体组装须唯一调用点，表单不得二次读取"
    submit = function_body(generate_js, r"export async function submitGenerate\(.*?^}")
    assert re.search(
        r"await postJson\(\s*`/web/api/generate/\$\{tool}`,\s*body,\s*timeoutMs,",
        submit,
    ), "生成请求未复用快照的请求体与工具"
    assert "return generateFallbackTimeoutMs();" in generate_js, "预检失败未回退等待预算兜底"
    preflight = _preflight_js_body()
    assert re.search(
        r"postJsonRaw\(\s*`/web/api/generate/\$\{tool}\?\$\{params}`,\s*PREFLIGHT_TIMEOUT_MS,\s*\)",
        preflight,
    ), "预检未打到快照工具的生成端点或调用仍携带请求体参数"
    for name in _JS_MODULES:
        source = read_js_module_source(name)
        for field in (
            "generate_scheduling_budget_ms",
            "generate_max_images_per_request",
            "layer_decomposition_max_images",
            "auto_save_download_budget_ms",
            "auto_save_max_concurrent",
        ):
            assert field not in source, f"{name} 仍引用 config-info 已收缩字段: {field}"


def test_generate_preflight_cache_reuses_query_string() -> None:
    """preflight 结果按工具加完整查询串缓存：命中免认证往返，仅成功推导
    入缓存，条目带写入时刻且读取校验时效，超龄视为无缓存重新预检。"""
    generate_js = read_js_module_source("generate.js")
    preflight = _preflight_js_body()
    assert re.search(
        r"cacheKey = `\$\{tool\}\?\$\{params}`", preflight
    ), "缓存键未覆盖工具与完整查询串"
    assert "preflightCache.get(cacheKey)" in preflight, "提交未查缓存免往返"
    assert "preflightCache.set(cacheKey," in preflight, "成功推导未入缓存"
    assert "timeoutMs: payload.timeout_ms" in preflight, "缓存条目缺预算字段"
    assert "at: Date.now()" in preflight, "缓存条目缺写入时刻"
    assert re.search(
        r"Date\.now\(\)\s*-\s*cached\.at\s*<\s*PREFLIGHT_CACHE_TTL_MS", preflight
    ), "缓存读取缺时效校验，配置变更重启后旧标签页无限期沿用旧预算"
    assert re.search(
        r"const PREFLIGHT_CACHE_TTL_MS = \d+;", generate_js
    ), "缓存时效常量形态改变，守护需同步"
    assert preflight.index("payload.success === true") < preflight.index(
        "preflightCache.set("
    ), "非成功载荷也入缓存，回退上限失去下次提交重试预检的机会"


def test_generate_preflight_cache_invalidated_on_config_reload() -> None:
    """配置拉取链（启动装载与后台重载共用）落新配置即整体失效 preflight 缓存。"""
    fetch = function_body(
        read_js_module_source("generate.js"), r"async function fetchConfigInfo\(.*?^}"
    )
    assert re.search(
        r'state\.configInfo\s*=\s*await\s+getJsonRaw\(\s*"/web/api/config-info"', fetch
    ), "配置拉取未落新到 state.configInfo，守护锚需同步"
    assert "preflightCache.clear()" in fetch, "配置落新未整体失效预算缓存"


def test_generate_fallback_prefers_config_info_derivation() -> None:
    """preflight 失败的等待兜底优先消费 config-info 下发的满档推导。

    载荷值随部署配置走，调参部署 preflight 失败不再被缺省常量提前掐断；
    常量仅兜底 config 不可得窗口。
    """
    generate_js = read_js_module_source("generate.js")
    helper = function_body(generate_js, r"function generateFallbackTimeoutMs\(.*?^}")
    assert (
        "state.configInfo.generate_fallback_timeout_ms" in helper
    ), "回退未消费 config-info 下发的满档推导"
    assert "GENERATE_FALLBACK_TIMEOUT_MS" in helper, "缺 config 不可得的常量兜底"
    assert "return generateFallbackTimeoutMs();" in _preflight_js_body(), "预检失败未走统一回退取值"


def test_generate_timeout_fallback_covers_server_derivation() -> None:
    """generate.js 缺省常量保有不低于服务端默认满档推导 1.5 倍的裕度。

    config-info 可得时前端优先消费其下发的满档推导（载荷对账另行守护），
    本常量仅兜底 config 不可得窗口；按服务端默认配置的满档推导（表单上限
    请求数、图层拆分满额产图并计入最坏下载尾巴）×1.5 取整到百万圆整档预置，
    服务端调参抬升默认推导使裕度失守时在此失败，提示同步抬升该常量。
    """
    generate_js = read_js_module_source("generate.js")
    cap = re.search(r"GENERATE_FALLBACK_TIMEOUT_MS = (\d+);", generate_js)
    assert cap is not None, "generate.js 静态保守上限常量形态改变，守护需同步"
    config = SeedreamConfig(api_key="guard-key")
    # 满额批次取最坏组合：请求数取表单上限，图层拆分使单请求产图满额；
    # 经真实预检入口推导，与线缆行为同源。
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [],
            "query_string": (
                f"request_count={MAX_PARALLEL_REQUEST_COUNT}&layer_decomposition=1".encode()
            ),
        }
    )
    derived_ms = _preflight_timeout_ms(request, config, IMAGE_TO_IMAGE_TOOL_NAME)
    assert (
        int(cap.group(1)) >= derived_ms * 1.5
    ), "静态保守上限低于服务端默认满档推导的 1.5 倍，需抬升上限常量"
