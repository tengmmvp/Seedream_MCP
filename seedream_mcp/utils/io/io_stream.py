"""响应流读取与 data 条目归一的通用原语：限时取块、超限消息提示与完成态升格。

供 io_sse 的 SSE 解析、client 的流式与非流式读体及工具与 Web 出口的条目遍历
共用，不含 SSE 事件语义。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

# 等块取值哨兵：流正常结束与截止时间到达，与字节块区分。
STREAM_ENDED = object()
STREAM_DEADLINE_HIT = object()

# 响应体超限消息的环境变量调整提示，读体路径的共用单一来源。
RESPONSE_BODY_LIMIT_HINT = "，可经 SEEDREAM_RESPONSE_BODY_LIMIT 调整"


async def next_stream_chunk(
    iterator: AsyncIterator[bytes], deadline: float | None
) -> bytes | object:
    """取下一读取块，等待期间持续受截止时间约束。

    重组分块与慢速上游的下一块间隔可长于事件间隔，等块本身限时使总时长预算
    可中断；截止到达返回 STREAM_DEADLINE_HIT，流结束返回 STREAM_ENDED。到点后
    已就绪的流结束或缓冲块优先于超时判定，防止已完成的响应被误判超时、
    触发非幂等生成请求的重复计费重试。
    """
    if deadline is None:
        try:
            return await iterator.__anext__()
        except StopAsyncIteration:
            return STREAM_ENDED
    try:
        async with asyncio.timeout_at(deadline):
            return await iterator.__anext__()
    except StopAsyncIteration:
        return STREAM_ENDED
    except TimeoutError:
        return STREAM_DEADLINE_HIT


def data_items(data: Any) -> list[Any]:
    """归一 data 字段的条目形态：list 原样返回，dict 计为单条目，其余形态无条目。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def item_reports_failure(item: Any) -> bool:
    """判定条目是否携带错误，error 为 None 以外的任意取值均计，升格判定与结局日志的错误提取共用本口径。"""
    return isinstance(item, dict) and item.get("error") is not None


def item_has_image_payload(item: Any) -> bool:
    """判定条目是否携带图像载荷，url 或 b64_json 任一取值非空即计，client 的有效图片条目判定共用本口径。"""
    return isinstance(item, dict) and bool(item.get("url") or item.get("b64_json"))


def escalate_partial_status(status: str | None, data: Any) -> str | None:
    """data 含错误条目且状态呈完成态时升格 partial，流式与非流式共用。

    条目形态与失败判定分别由 data_items 与 item_reports_failure 单源定义。
    """
    if status not in (None, "completed"):
        return status
    if any(item_reports_failure(item) for item in data_items(data)):
        return "partial"
    return status
