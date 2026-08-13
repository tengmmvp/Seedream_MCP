"""SSE 流式响应解析。

将 Seedream API 的 Server-Sent Events 流式响应解析为统一结构。从 SeedreamClient
剥离，便于独立测试与维护。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, cast

from .errors import SeedreamAPIError


def is_sse_response(response: Any) -> bool:
    """判断响应是否为 SSE 事件流。"""
    content_type = str(response.headers.get("content-type", ""))
    return content_type.startswith("text/event-stream")


def format_sse_success_event(event: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    """将 SSE 成功事件转换为统一图片项结构。"""
    return {
        "url": event.get("url"),
        "b64_json": event.get("b64_json"),
        "size": event.get("size"),
        "image_index": event.get("image_index"),
        "model": event.get("model", model_id),
        "created": event.get("created", int(time.time())),
        "type": event.get("type", "image_generation.partial_succeeded"),
    }


def format_sse_failed_event(event: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    """将 SSE 失败事件转换为统一图片项结构。"""
    raw_error = event.get("error")
    error = raw_error if isinstance(raw_error, dict) else {}
    return {
        "error": {
            "code": error.get("code"),
            "message": error.get("message"),
        },
        "image_index": event.get("image_index"),
        "model": event.get("model", model_id),
        "created": event.get("created", int(time.time())),
        "type": event.get("type", "image_generation.partial_failed"),
    }


def parse_sse_segment(
    segment: bytes | bytearray, log: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """解析单个 SSE 事件段，返回事件对象。解析失败时记录日志并返回 None。"""
    raw_segment = segment.strip()
    if not raw_segment:
        return None

    try:
        event_text = raw_segment.decode("utf-8")
        # Seedream SSE 事件将 JSON 负载承载在 data: 字段中；按 SSE 规范多行 data: 以换行拼接为完整负载，event:/id: 字段本接口未使用
        data_parts: List[str] = []
        for line in event_text.split("\n"):
            if line.startswith("data:"):
                data_parts.append(line[5:].strip())
        payload = "\n".join(data_parts) if data_parts else None
        # [DONE] 为流结束哨兵而非图片事件，直接丢弃
        if not payload or payload == "[DONE]":
            return None
        parsed_payload = json.loads(payload)
        if not isinstance(parsed_payload, dict):
            raise ValueError("SSE 事件数据必须是对象")
        return cast(Dict[str, Any], parsed_payload)
    except Exception as exc:
        if log is not None:
            log.error("SSE事件解析失败: {}", str(exc))
            log.debug("SSE事件原始段长度: {} bytes", len(raw_segment))
        return None


def _classify_sse_event(
    event: Dict[str, Any], model_id: str, items: List[Dict[str, Any]]
) -> tuple[bool, Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """分类单个 SSE 事件：追加图片项或返回完成元信息；请求级错误抛 SeedreamAPIError。

    主循环与流末尾残留处理共用此函数，避免事件分支逻辑重复。

    Returns:
        (completed, usage, tools) — completed 为 True 时 usage/tools 有效。
    """
    event_type = event.get("type")
    # 请求级错误事件：无 type 且顶层含 error 键。本质为 4xx，标记 status_code=400 使上层判定不可重试
    if event_type is None and isinstance(event.get("error"), dict):
        err = event["error"]
        raise SeedreamAPIError(
            message=err.get("message", "流式请求失败"),
            status_code=400,
            error_code=err.get("code"),
        )
    if event_type == "image_generation.partial_succeeded":
        items.append(format_sse_success_event(event, model_id))
    elif event_type == "image_generation.partial_failed":
        items.append(format_sse_failed_event(event, model_id))
    elif event_type == "image_generation.completed":
        return True, event.get("usage", {}) or {}, event.get("tools")
    return False, None, None


async def parse_sse_response(
    response: Any,
    *,
    model_id: str,
    chunk_size: int,
    buffer_max_size: int,
    log: Any,
) -> Dict[str, Any]:
    """增量解析 SSE 响应为统一的图片项列表与完成元信息。

    Args:
        response: httpx 流式响应对象，按 ``chunk_size`` 分块读取。
        model_id: 模型标识，用于填充图片项 model 字段的缺省值。
        chunk_size: 每次从流中读取的字节数。
        buffer_max_size: 缓冲区上限，既作为已消费前缀的回收阈值，也作为防异常流无限增长撑爆内存的截断阈值。
        log: loguru logger 实例，用于记录进度与告警。

    Returns:
        包含 success/data/usage/status/tools 的统一结果字典。
    """
    items: List[Dict[str, Any]] = []
    usage: Dict[str, Any] = {}
    status: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None

    # 使用 bytearray 累积流式数据：bytes 拼接为 O(n²) 拷贝，bytearray.extend 均摊 O(1)
    buffer = bytearray()
    # 已消费前缀偏移：用偏移指针替代逐次 del buffer[:n] 的 O(n) 前缀删除，定期批量回收均摊为 O(1)
    offset = 0
    processed_bytes = 0
    # 是否发生过单事件超限截断；用于在返回结果中标记不完整，通知调用方存在数据丢失
    truncated = False

    async for chunk in response.aiter_bytes(chunk_size):
        if not chunk:
            continue

        buffer += chunk
        processed_bytes += len(chunk)

        if processed_bytes > 0 and processed_bytes % (1024 * 1024) == 0:
            log.debug("已处理 {} MB 数据", processed_bytes // 1024 // 1024)

        # SSE 事件以空行分隔，即 b"\n\n"；先抽干所有完整事件，避免后续缓冲截断时丢失已就绪事件
        while True:
            sep = buffer.find(b"\n\n", offset)
            if sep == -1:
                break
            segment = bytes(buffer[offset:sep])
            offset = sep + 2
            event = parse_sse_segment(segment, log)
            if event is None:
                continue
            completed, evt_usage, evt_tools = _classify_sse_event(event, model_id, items)
            if completed:
                usage = evt_usage or {}
                status = "completed"
                tools = evt_tools

        # 周期性回收已消费前缀；阈值取 buffer_max_size，使每次 O(n) 回收均摊到至少 buffer_max_size 字节
        if offset > 0 and offset >= buffer_max_size:
            del buffer[:offset]
            offset = 0

        # 不完整尾部超过缓冲区上限：单个事件体积过大，无法完整解析。
        # while 循环已抽干所有完整事件，故 [offset, end) 必为单个未完成事件；
        # 丢弃该尾部以免内存无限增长，[0, offset) 内的完整事件均已处理进 items，不会跨界错位。
        live_len = len(buffer) - offset
        if live_len > buffer_max_size:
            log.warning(
                "单个 SSE 事件超过缓冲区上限 ({} > {})，丢弃该不完整事件",
                live_len,
                buffer_max_size,
            )
            del buffer[offset:]
            truncated = True

    trailing_event = parse_sse_segment(buffer[offset:], log)
    if trailing_event is not None:
        completed, evt_usage, evt_tools = _classify_sse_event(trailing_event, model_id, items)
        if completed:
            usage = evt_usage or {}
            status = "completed"
            tools = evt_tools

    # 与非流式 _build_api_result 保持一致：当存在部分失败（data 含 error 项）时
    # 标记 status=partial，避免流式/非流式在同等部分失败场景下 status 语义不一致，
    # 进而误导下游对生成结果完整性的判断。
    if status in (None, "completed") and any(
        isinstance(item, dict) and "error" in item for item in items
    ):
        status = "partial"

    # 单个事件超限被丢弃时结果不完整，标记 partial 通知调用方存在数据丢失
    if truncated and status in (None, "completed"):
        status = "partial"

    return {
        "success": True,
        "data": items,
        "usage": usage,
        "status": status,
        "tools": tools,
    }
