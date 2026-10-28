"""runner 失败结果错误类型不可判时的负载合成测试：保形补型、整体合成与告警落点。"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.types import CallToolResult

from _lib.log_fakes import capture_loguru_messages
from seedream_mcp.tools.outputs import build_error_structured
from seedream_mcp.webapp import _responses


def _error_result(structured: Any) -> CallToolResult:
    """构造 is_error 且结构化负载为给定形态的 runner 结果。"""
    return CallToolResult(content=[], structured_content=structured, is_error=True)


@pytest.mark.parametrize("bad_type", [None, 42])
async def test_dict_payload_with_indeterminable_type_fills_internal_error(bad_type: Any) -> None:
    """error.type 为 null 或非字符串时仅补 type，上游 message/code 与顶层字段保留。"""
    structured: dict[str, Any] = {
        "tool": "browse_images",
        "success": False,
        "status": "failed",
        "error": {"type": bad_type, "message": "上游错误", "code": 401},
    }

    payload = await _responses.runner_result_payload("browse_images", _error_result(structured))

    assert payload == {
        "tool": "browse_images",
        "success": False,
        "status": "failed",
        "error": {"type": "internal_error", "message": "上游错误", "code": 401},
    }
    # 补型后状态码经单源映射自然推导，不落表外 502。
    assert _responses.structured_status(payload) == 500


async def test_dict_payload_without_error_key_inserts_error_object() -> None:
    """error 键缺失时插入 internal_error 错误对象，其余顶层字段原样保留。"""
    payload = await _responses.runner_result_payload(
        "text_to_image", _error_result({"success": False, "images": []})
    )

    assert payload == {
        "success": False,
        "images": [],
        "error": {"type": "internal_error", "message": _responses.INTERNAL_ERROR_INFO.message},
    }


@pytest.mark.parametrize("error_value", [None, "崩了", 42])
async def test_non_dict_error_replaced_with_error_object(error_value: Any) -> None:
    """error 为非 dict 形态时整体替换为 internal_error 错误对象。"""
    payload = await _responses.runner_result_payload(
        "browse_images", _error_result({"success": False, "error": error_value})
    )

    assert payload == {
        "success": False,
        "error": {"type": "internal_error", "message": _responses.INTERNAL_ERROR_INFO.message},
    }


@pytest.mark.parametrize("structured", [None, "bad"])
async def test_non_dict_payload_synthesizes_whole_result(structured: Any) -> None:
    """负载非 dict 时整体合成行为不变，与端点级既有兜底用例同口径。"""
    payload = await _responses.runner_result_payload("browse_images", _error_result(structured))

    assert payload == build_error_structured(
        "browse_images", "internal_error", _responses.INTERNAL_ERROR_INFO.message
    )


async def test_synthesis_logs_warning_with_tool_and_payload() -> None:
    """进入合成分支即落 warning，含工具名与原负载，「详情见日志」有真实落点。"""
    structured: dict[str, Any] = {
        "success": False,
        "error": {"type": None, "message": "上游错误"},
    }
    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        await _responses.runner_result_payload("browse_images", _error_result(structured))

    entries = [entry for entry in warnings if "错误类型不可判" in entry]
    assert len(entries) == 1
    assert "browse_images" in entries[0]
    assert "上游错误" in entries[0]
