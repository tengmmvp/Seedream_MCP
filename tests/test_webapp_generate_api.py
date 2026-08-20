"""Web 操作台生成 API 测试：四端点复用 runners、错误映射与 web_path 增强。

runner 经对象式 monkeypatch 替换，覆盖成功、校验失败与错误类型映射到 HTTP
状态码的分支；共享资源 shim 的构造行为独立单测。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.types import CallToolResult

import seedream_mcp.resources as resources_module
from _web_fixtures import build_web_app, write_workspace_config
from seedream_mcp.webapp import generate as generate_module
from seedream_mcp.webapp.context import build_web_request_context


def _generation_result(payload: dict[str, Any], is_error: bool = False) -> CallToolResult:
    """构造生成 runner 的替身返回值。"""
    return CallToolResult(content=[], structured_content=payload, is_error=is_error)


def _install_runner(
    monkeypatch: pytest.MonkeyPatch, name: str, payload: dict[str, Any], is_error: bool = False
) -> None:
    """把 api 命名空间内的 runner 符号替换为返回固定结果的替身。"""

    async def _fake_runner(params: Any, config: Any, ctx: Any = None) -> CallToolResult:
        del params, config, ctx
        return _generation_result(payload, is_error)

    monkeypatch.setattr(generate_module, name, _fake_runner)


async def _post_json(app: Any, path: str, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return await client.post(path, json=body)


async def test_generate_returns_structured_payload_with_web_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """成功结果的 data 条目附上相对保存根的 web_path。"""
    save_root = write_workspace_config(tmp_path)
    local_path = save_root / "2026-08-20" / "text_to_image" / "a.png"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"png")
    _install_runner(
        monkeypatch,
        "run_text_to_image",
        {
            "tool": "text_to_image",
            "success": True,
            "data": [{"url": "https://x/a.png", "local_path": str(local_path)}],
        },
    )
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 200
    entry = response.json()["data"][0]
    assert entry["web_path"] == "2026-08-20/text_to_image/a.png"


async def test_generate_skips_web_path_outside_save_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """保存根之外的 local_path 不附 web_path，既有字段保持原样。"""
    write_workspace_config(tmp_path)
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"png")
    _install_runner(
        monkeypatch,
        "run_image_to_image",
        {"tool": "image_to_image", "success": True, "data": [{"local_path": str(outside)}]},
    )
    app = build_web_app()

    response = await _post_json(
        app, "/web/api/generate/image-to-image", {"prompt": "改图", "image": "https://x/a.png"}
    )

    assert response.status_code == 200
    assert "web_path" not in response.json()["data"][0]


async def test_generate_invalid_params_returns_400(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """空提示词经 pydantic 校验拒绝，映射 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": ""})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


async def test_generate_invalid_json_returns_400(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """非法 JSON 请求体映射 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.post(
            "/web/api/generate/text-to-image",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [("rate_limited", 429), ("payment_required", 402), ("generation_failed", 502)],
)
async def test_generate_maps_error_type_to_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
    error_type: str,
    expected_status: int,
) -> None:
    """失败结果按 error.type 映射状态码，响应体保留完整结构化结果。"""
    write_workspace_config(tmp_path)
    _install_runner(
        monkeypatch,
        "run_text_to_image",
        {
            "tool": "text_to_image",
            "success": False,
            "data": [],
            "error": {"type": error_type, "message": "x"},
        },
        is_error=True,
    )
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == expected_status
    assert response.json()["error"]["type"] == error_type


async def test_generate_endpoint_requires_token(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """令牌部署下生成端点未携带令牌时被 Bearer 中间件拒绝。"""
    write_workspace_config(tmp_path)
    app = build_web_app(auth_token="secret")

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 401


def test_build_web_request_context_returns_stub_when_resource_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共享资源在堂时 shim 的 lifespan 字典携带共享 client 与下载管理器。"""

    class _FakeClient:
        pass

    class _FakeDownloadManager:
        pass

    client = _FakeClient()
    download_manager = _FakeDownloadManager()
    monkeypatch.setattr(
        resources_module,
        "_active_resource",
        type("_R", (), {"client": client, "download_manager": download_manager})(),
    )

    stub = build_web_request_context()

    assert stub is not None
    assert stub.request_context.lifespan_context["client"] is client
    assert stub.request_context.lifespan_context["download_manager"] is download_manager


def test_build_web_request_context_returns_none_when_resource_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共享资源不在堂时返回 None，调用方回退每请求新建 client 路径。"""
    monkeypatch.setattr(resources_module, "_active_resource", None)

    assert build_web_request_context() is None
