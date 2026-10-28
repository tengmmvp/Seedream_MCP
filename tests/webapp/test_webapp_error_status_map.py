"""Web 操作台错误状态码映射测试：状态表逐条快照、表外回退、not_found 单一来源与端点映射口径。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from _lib.web_fixtures import (
    _assert_structured_failure,
    build_web_app,
    web_asgi_client,
    write_workspace_config,
)
from seedream_mcp.webapp import _responses
from seedream_mcp.webapp import gallery as gallery_module


async def test_browse_runner_failed_result_maps_to_500(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """browse runner 的 is_error 结果（browse_failed）回 500，不误报上游网关 502。"""
    payload: dict[str, Any] = {
        "tool": "browse_images",
        "success": False,
        "status": "failed",
        "images": [],
        "error": {"type": "browse_failed", "message": "目录扫描失败"},
    }

    async def _browse_failed(params: Any, ctx: Any = None, **kwargs: Any) -> CallToolResult:
        del params, ctx, kwargs
        return CallToolResult(content=[], structured_content=payload, is_error=True)

    monkeypatch.setattr(gallery_module, "run_browse_images", _browse_failed)
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with web_asgi_client(app) as client:
        response = await client.post("/web/api/browse", json={"show_details": True})

    _assert_structured_failure(response, "browse_images", "browse_failed", status=500)


def test_structured_status_unknown_type_falls_back_to_502() -> None:
    """表外字符串错误类型经 structured_status 回退 502。"""
    assert _responses.structured_status({"error": {"type": "mystery_code"}}) == 502


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [
        ("validation_error", 400),
        ("payload_too_large", 400),
        ("rate_limited", 429),
        ("payment_required", 402),
        ("auth_error", 503),
        ("config_error", 503),
        ("network_error", 502),
        ("api_error", 502),
        ("timeout_error", 504),
        ("generation_failed", 500),
        # 端点自有码自错误码单表并入，runner 失败码同经端点码表登记。
        ("internal_error", 500),
        ("browse_failed", 500),
        ("model_info_failed", 500),
        ("invalid_json", 400),
        ("invalid_request", 400),
        ("invalid_path", 400),
        ("not_found", 404),
        ("thumbnail_failed", 422),
        ("images_root_unavailable", 400),
        ("invalid_directory", 400),
        ("service_unavailable", 500),
    ],
)
def test_registered_error_type_statuses_unchanged(error_type: str, expected_status: int) -> None:
    """状态表逐条快照防映射重构回归，端点自有码与档案码同受快照与端点行为测试锁定。"""
    assert _responses.ERROR_TYPE_STATUS[error_type] == expected_status


def test_not_found_single_source_feeds_both_envelope_forms() -> None:
    """not_found 单一来源守护：平面与结构化两信封形态同 type 与状态，文案各守其变体。"""
    image_error = _responses.IMAGE_NOT_FOUND_INFO
    api_error = _responses.API_NOT_FOUND_INFO
    assert (api_error.type, api_error.http_status) == (image_error.type, image_error.http_status)
    assert (image_error.type, image_error.http_status) == ("not_found", 404)
    assert image_error.message == "图片不存在"
    assert api_error.message == "接口不存在"

    flat = _responses.flat_error_json(api_error)
    structured = _responses.endpoint_error_json(image_error)
    flat_payload = json.loads(bytes(flat.body))
    structured_payload = json.loads(bytes(structured.body))

    assert flat.status_code == structured.status_code == 404
    assert flat_payload["error"] == structured_payload["error"]["type"] == "not_found"
    assert flat_payload["error_description"] == "接口不存在"
    assert structured_payload["error"]["message"] == "图片不存在"
