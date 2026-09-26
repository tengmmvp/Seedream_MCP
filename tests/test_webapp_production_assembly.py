"""Web 操作台生产装配守护测试。

直接调用 transport 的生产装配函数 _build_streamable_app 构建真实 app（构造
transport_security -> 注册 Web 路由 -> streamable_http_app -> 挂载静态资源 ->
装配中间件），经 httpx.ASGITransport 验证 Web 面路由、真实静态资源、Origin
守卫行为与默认关闭形态；run_streamable_http 仅承担 uvicorn serve 与退出清理，
装配正确性以本文件的生产同路径锁定。共享响应辅助的序列化下沉与路由模块的导入
纯净性守护同驻本文件。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from _cpu_offload_spy import CpuOffloadSpy
from _web_fixtures import web_asgi_client, write_workspace_config
from seedream_mcp.transport import _build_streamable_app
from seedream_mcp.utils.core.executors import CPU_OFFLOAD_SIZE_THRESHOLD
from seedream_mcp.utils.core.sanitizers import _CONTAINER_REPR_DEPTH_LIMIT
from seedream_mcp.webapp import _responses
from seedream_mcp.webapp.constants import STATIC_DIR, STATIC_MIME_ALLOWLIST, STATIC_PAGE_EXTENSIONS
from seedream_mcp.webapp.meta import _upload_budget_chars
from test_package_lazy_import import _run_in_subprocess


def test_upload_budget_derivation_floors_at_zero() -> None:
    """请求体上限不高于 4MiB 时预算推导为 0，0 为下发前端的有效预算值。"""
    assert _upload_budget_chars(4 * 1024 * 1024) == 0
    assert _upload_budget_chars(1024 * 1024) == 0
    assert _upload_budget_chars(64 * 1024 * 1024) == 45 * 1024 * 1024


async def test_production_app_serves_web_console(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """生产装配的 app 提供 /web 入口、config-info 接口、真实静态资源与 404 兜底。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "", True)

    async with web_asgi_client(app) as client:
        index_response = await client.get("/web")
        api_response = await client.get("/web/api/config-info")
        static_response = await client.get("/web/static/js/main.js")
        html_direct_response = await client.get("/web/static/index.html")
        missing_response = await client.get("/web/api/does-not-exist")

    assert index_response.status_code == 200
    assert index_response.headers["content-type"].startswith("text/html")
    assert api_response.status_code == 200
    info = api_response.json()
    assert info["images_root_available"] is True
    # 前端派生所需的字段齐备：未知模型档位与参考图上限与后端 unknown 家族同源，
    # 数值上限与后端常量同源，上传预算与请求体上限同源，水印默认值与配置同源。
    assert info["fallback_presets"] == ["1K", "1.5K", "2K", "3K", "4K"]
    assert info["unknown_max_reference_images"] == 14
    assert info["upload_budget_chars"] == 45 * 1024 * 1024
    assert info["max_request_count"] == 10
    assert info["max_images"] == 15
    assert info["default_watermark"] is False
    assert static_response.status_code == 200
    assert html_direct_response.status_code == 404
    assert missing_response.status_code == 404


async def test_production_app_without_web_returns_404(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """web_enabled=False 时不注册任何 Web 路由，入口与 API 路径均 404。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "", False)

    async with web_asgi_client(app) as client:
        index_response = await client.get("/web")
        api_response = await client.get("/web/api/config-info")

    assert index_response.status_code == 404
    assert api_response.status_code == 404


async def test_origin_guard_allows_same_origin_and_rejects_cross_origin(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """无令牌 Web 部署：同源 Origin 与无 Origin 放行，跨源与畸形 Origin 被 403 拒绝。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "", True)

    async with web_asgi_client(app) as client:
        same_origin = await client.get(
            "/web/api/config-info",
            headers={"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:8000"},
        )
        no_origin = await client.get("/web/api/config-info", headers={"host": "127.0.0.1:8000"})
        cross_origin = await client.get(
            "/web/api/config-info",
            headers={"host": "127.0.0.1:8000", "origin": "http://evil.example"},
        )
        malformed_origin = await client.get(
            "/web/api/config-info",
            headers={"host": "127.0.0.1:8000", "origin": "http://[::1"},
        )

    assert same_origin.status_code == 200
    assert no_origin.status_code == 200
    assert cross_origin.status_code == 403
    assert cross_origin.json()["error"] == "invalid_origin"
    assert malformed_origin.status_code == 403
    assert malformed_origin.json()["error"] == "invalid_origin"


async def test_fetch_metadata_guard_rejects_cross_site_fetch(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """无令牌 Web 部署：GET/HEAD 携带 same-site 或 cross-site 的 Sec-Fetch-Site 被 403 拒绝。

    跨站 img/no-cors 加载不携带 Origin，Origin 守卫对其无效，依赖该头兜底；
    same-site 覆盖同注册域兄弟子域嵌入，HEAD 覆盖 no-cors 探测，same-origin 与
    无该头的旧客户端放行。
    """
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "", True)

    async with web_asgi_client(app) as client:
        cross_site = await client.get(
            "/web/api/config-info", headers={"sec-fetch-site": "cross-site"}
        )
        same_site = await client.get(
            "/web/api/config-info", headers={"sec-fetch-site": "same-site"}
        )
        head_cross_site = await client.head(
            "/web/api/config-info", headers={"sec-fetch-site": "cross-site"}
        )
        same_origin = await client.get(
            "/web/api/config-info", headers={"sec-fetch-site": "same-origin"}
        )
        no_header = await client.get("/web/api/config-info", headers={"host": "127.0.0.1:8000"})

    assert cross_site.status_code == 403
    assert cross_site.json()["error"] == "cross_site_fetch"
    assert same_site.status_code == 403
    assert same_site.json()["error"] == "cross_site_fetch"
    assert head_cross_site.status_code == 403
    assert same_origin.status_code == 200
    assert no_header.status_code == 200


async def test_origin_guard_not_assembled_when_token_configured(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """有令牌部署不装配 Origin 守卫：跨源 Origin 由 Bearer 判定，结果取决于令牌。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "s3cret", True)

    async with web_asgi_client(app) as client:
        cross_without_token = await client.get(
            "/web/api/config-info",
            headers={"host": "127.0.0.1:8000", "origin": "http://evil.example"},
        )
        cross_with_token = await client.get(
            "/web/api/config-info",
            headers={
                "host": "127.0.0.1:8000",
                "origin": "http://evil.example",
                "authorization": "Bearer s3cret",
            },
        )

    assert cross_without_token.status_code == 401
    assert cross_with_token.status_code == 200


def test_static_mime_allowlist_covers_shipped_asset_extensions() -> None:
    """静态 MIME 封闭清单与随包资产扩展一致：新增资产类型漏登记或页面扩展漏封禁即失败。"""
    shipped = {path.suffix.lower() for path in STATIC_DIR.rglob("*") if path.is_file()}
    assert shipped
    assert not (shipped - set(STATIC_PAGE_EXTENSIONS) - set(STATIC_MIME_ALLOWLIST))
    assert not (set(STATIC_PAGE_EXTENSIONS) & set(STATIC_MIME_ALLOWLIST))


@pytest.mark.parametrize(
    ("asset_path", "asset_extension", "expected_content_type"),
    [
        ("/web/static/assets/bg-letter.svg", ".svg", "image/svg+xml"),
        ("/web/static/css/style.css", ".css", "text/css; charset=utf-8"),
        ("/web/static/js/main.js", ".js", "text/javascript; charset=utf-8"),
    ],
    ids=["svg", "css", "js"],
)
async def test_production_app_static_mime_comes_from_closed_allowlist(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
    monkeypatch: pytest.MonkeyPatch,
    asset_path: str,
    asset_extension: str,
    expected_content_type: str,
) -> None:
    """静态直出 MIME 全量出自封闭清单且全程不经 guess_type，注册表猜型无从进入响应头。

    guess_type 顶替为记录型替身：静态路径若回退猜型，junk 返回值即污染
    content-type，替身调用记录同步使断言失败；text/* 的 charset 后缀经 starlette
    追加，期望值为清单裸值加该后缀的合成结果。
    """
    write_workspace_config(tmp_path)
    guess_calls: list[str] = []

    def _registry_junk_guess(url: str) -> tuple[str | None, str | None]:
        guess_calls.append(url)
        return ("application/x-registry-junk", None)

    monkeypatch.setattr("starlette.responses.guess_type", _registry_junk_guess)
    app = _build_streamable_app("127.0.0.1", False, "", True)

    async with web_asgi_client(app) as client:
        response = await client.get(asset_path)

    assert response.status_code == 200
    assert response.headers["content-type"] == expected_content_type
    assert expected_content_type.startswith(STATIC_MIME_ALLOWLIST[asset_extension])
    assert not guess_calls


async def test_production_app_static_not_modified_keeps_bare_304(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """条件请求命中 304 时不注入 content-type，安全头照常附加。"""
    write_workspace_config(tmp_path)
    app = _build_streamable_app("127.0.0.1", False, "", True)

    async with web_asgi_client(app) as client:
        first = await client.get("/web/static/assets/bg-letter.svg")
        refreshed = await client.get(
            "/web/static/assets/bg-letter.svg",
            headers={"if-none-match": first.headers["etag"]},
        )

    assert first.status_code == 200
    assert refreshed.status_code == 304
    assert "content-type" not in refreshed.headers


def test_importing_webapp_routes_keeps_global_mimetypes_registry_intact() -> None:
    """导入 webapp 路由模块不得改写进程级 mimetypes 注册表。"""
    _run_in_subprocess(
        "import mimetypes\n"
        "probe = ('.svg', '.js', '.css', '.html', '.png')\n"
        "before = [mimetypes.guess_type('a' + ext) for ext in probe]\n"
        "import seedream_mcp.webapp.routes\n"
        "after = [mimetypes.guess_type('a' + ext) for ext in probe]\n"
        "assert before == after, (before, after)"
    )


def _deep_nested_payload(depth: int) -> dict[str, object]:
    """构造指定深度的嵌套链。"""
    root: dict[str, object] = {}
    node: dict[str, object] = root
    for _ in range(depth):
        child: dict[str, object] = {}
        node["child"] = child
        node = child
    return root


def _leaf_dense_payload() -> dict[str, object]:
    """构造图库页典型形态的叶子稠密载荷。"""
    return {
        "images": [
            {"index": index, "path": f"a/b/img-{index:04d}.png", "size_mb": 1.5}
            for index in range(600)
        ]
    }


@pytest.mark.parametrize(
    ("payload_factory", "expected_body"),
    [
        (lambda: {"ok": True, "items": [1, 2]}, b'{"ok":true,"items":[1,2]}'),
        (lambda: {"payload": "x" * (CPU_OFFLOAD_SIZE_THRESHOLD + 1)}, None),
        (_leaf_dense_payload, None),
        (lambda: {"items": ["y" * 100] * 700}, None),
        (lambda: _deep_nested_payload(_CONTAINER_REPR_DEPTH_LIMIT + 1), None),
    ],
    ids=["small", "large", "leaf-dense", "accumulated-leaves", "deep-nesting"],
)
async def test_respond_structured_json_payload_shapes_run_in_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
    payload_factory: Callable[[], dict[str, object]],
    expected_body: bytes | None,
) -> None:
    """各形态载荷一律经专用 CPU 池序列化，序列化路径不随载荷形态分流事件循环。"""
    spy = CpuOffloadSpy(_responses.dump_strict_json)
    monkeypatch.setattr(_responses, "dump_strict_json", spy)

    structured = payload_factory()
    response = await _responses.respond_structured_json(structured, 200)

    assert response.status_code == 200
    assert json.loads(bytes(response.body)) == structured
    if expected_body is not None:
        assert response.body == expected_body
    spy.assert_ran_in_cpu_pool()


async def test_respond_structured_json_pool_closed_returns_service_unavailable() -> None:
    """CPU 卸载池关闭取消排队序列化时回 500 service_unavailable，与请求体解析同口径。"""
    from _cpu_offload_spy import saturate_cpu_offload_pool
    from seedream_mcp.utils.core.executors import shutdown_cpu_offload_executor

    structured: dict[str, object] = {"payload": "x" * (CPU_OFFLOAD_SIZE_THRESHOLD + 1)}

    async with saturate_cpu_offload_pool() as saturated:
        respond = asyncio.ensure_future(_responses.respond_structured_json(structured, 200))
        await saturated.wait_queued()
        shutdown_cpu_offload_executor()
        response = await respond

    await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5)

    assert response.status_code == 500
    assert json.loads(bytes(response.body))["error"] == "service_unavailable"


async def test_respond_structured_json_binary_leaf_runs_in_cpu_pool_outputs_full_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """b64_json 大串按全文进入响应体，序列化下沉专用 CPU 池执行。"""
    dump_spy = CpuOffloadSpy(_responses.dump_strict_json)
    monkeypatch.setattr(_responses, "dump_strict_json", dump_spy)
    b64_payload = "Q" * 300_000
    structured: dict[str, object] = {
        "tool": "text_to_image",
        "success": True,
        "data": [{"b64_json": b64_payload}],
    }

    response = await _responses.respond_structured_json(structured, 200)

    assert response.status_code == 200
    assert b64_payload.encode() in response.body
    dump_spy.assert_ran_in_cpu_pool()
