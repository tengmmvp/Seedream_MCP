"""记录进度上报的 Context 替身。

供 test_browse_tool_result 与 test_parallel_generation_tools 复用，替代各文件
自持的纯记录型替身；带事件循环让出或寿命期装配等特殊行为的替身仍在各文件自持。
"""

from __future__ import annotations

from typing import Any


class RecordingProgressContext:
    """记录每次进度上报的三元组，供进度序列与终态断言。

    Attributes:
        calls: (progress, total, message) 三元组列表，按上报顺序。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[float, float, str]] = []

    @property
    def progress_values(self) -> list[float]:
        """上报进度值序列。"""
        return [call[0] for call in self.calls]

    @property
    def request_context(self) -> Any:
        raise AttributeError("测试替身不提供请求上下文")

    async def report_progress(self, *, progress: float, total: float, message: str) -> None:
        """记录一次进度上报。"""
        self.calls.append((progress, total, message))
