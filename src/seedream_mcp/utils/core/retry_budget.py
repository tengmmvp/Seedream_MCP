"""API 调用跨尝试预算策略的单一来源：累计预算与可行性下限均按单次 api_timeout 推导。"""

from __future__ import annotations

# 把单请求的最长占用约束在单次 api_timeout 的小常数倍内。
API_RETRY_BUDGET_FACTOR = 2

# 剩余预算低于单次 api_timeout 的该比例时不足以支撑一次完整尝试，不再发起。
API_RETRY_FEASIBILITY_FLOOR_FACTOR = 0.1


def api_retry_budget_seconds(api_timeout: float) -> float:
    """按单次 api_timeout 推导跨尝试累计预算秒数。"""
    return float(api_timeout) * API_RETRY_BUDGET_FACTOR


def retry_feasibility_floor(api_timeout: float) -> float:
    """按单次 api_timeout 推导剩余预算的可行性下限秒数。"""
    return float(api_timeout) * API_RETRY_FEASIBILITY_FLOOR_FACTOR
