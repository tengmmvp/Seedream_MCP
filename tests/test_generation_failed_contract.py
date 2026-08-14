"""生成结果失败判定契约测试。

``_is_generation_failed`` 被 common.py 与 results.py 共用，驱动 isError、自动保存
与响应格式化分支。HTTP 层 success 与显式 status=="failed" 任一命中即视为失败，
确保上游即便以 success=True 携带 status=failed 时下游仍按失败处理。
"""

from __future__ import annotations

from seedream_mcp.tools.core._helpers import _is_generation_failed


def test_status_failed_marks_generation_failed_even_when_success_true() -> None:
    """success=True 但 status=='failed' 视为失败：显式失败状态优先于 HTTP 成功标志。"""
    assert _is_generation_failed({"success": True, "status": "failed"}) is True


def test_status_completed_with_success_true_not_failed() -> None:
    """success=True 且 status 非 failed 视为成功。"""
    assert _is_generation_failed({"success": True, "status": "completed"}) is False


def test_success_false_marks_failed_regardless_of_status() -> None:
    """success=False 视为失败，无论 status 是否存在。"""
    assert _is_generation_failed({"success": False}) is True


def test_success_true_without_status_not_failed() -> None:
    """success=True 且 status 缺省视为成功，不因 status 字段缺失而误判失败。"""
    assert _is_generation_failed({"success": True}) is False
