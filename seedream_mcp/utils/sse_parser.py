"""SSE 流式响应解析。

将 Seedream API 的 Server-Sent Events 流式响应解析为统一结构。从 SeedreamClient
剥离，便于独立测试与维护。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, cast

from .errors import SeedreamAPIError


def is_sse_response(response: Any) -> bool:
    """判断响应是否为 SSE 事件流。"""
    content_type = str(response.headers.get("content-type", ""))
    return content_type.startswith("text/event-stream")


def format_sse_success_event(event: dict[str, Any], model_id: str) -> dict[str, Any]:
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


def format_sse_failed_event(event: dict[str, Any], model_id: str) -> dict[str, Any]:
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


def parse_sse_segment(segment: bytes | bytearray, log: Any | None = None) -> dict[str, Any] | None:
    """解析单个 SSE 事件段，返回事件对象。解析失败时记录日志并返回 None。"""
    raw_segment = segment.strip()
    if not raw_segment:
        return None

    try:
        event_text = raw_segment.decode("utf-8")
        # Seedream SSE 事件将 JSON 负载承载在 data: 字段中；按 SSE 规范多行 data: 以换行拼接为完整负载，event:/id: 字段本接口未使用
        data_parts: list[str] = []
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
        return cast(dict[str, Any], parsed_payload)
    except Exception as exc:
        if log is not None:
            log.error("SSE事件解析失败: {}", str(exc))
            log.debug("SSE事件原始段长度: {} bytes", len(raw_segment))
        return None


# 大事件卸载阈值：超过此大小的 segment 的 json.loads 改到工作线程执行，
# 避免 stream + b64_json 多 MB 事件在事件循环中阻塞。小事件保持同步解析以
# 省去线程调度开销。
_SSE_OFFLOAD_THRESHOLD = 64 * 1024


async def _parse_segment(segment: bytes | bytearray, log: Any) -> dict[str, Any] | None:
    """解析单个 SSE 事件段，大段卸载到工作线程避免阻塞事件循环。

    ``parse_sse_segment`` 为同步函数，其 ``json.loads`` 对多 MB 负载耗时可观；
    超过 ``_SSE_OFFLOAD_THRESHOLD`` 的段通过 ``asyncio.to_thread`` 卸载，小段
    保持同步以避免线程调度开销。返回值语义与 ``parse_sse_segment`` 一致。
    """
    if len(segment) > _SSE_OFFLOAD_THRESHOLD:
        return await asyncio.to_thread(parse_sse_segment, segment, log)
    return parse_sse_segment(segment, log)


def _classify_sse_event(
    event: dict[str, Any], model_id: str, items: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any] | None, list[dict[str, Any]] | None]:
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
) -> dict[str, Any]:
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
    items: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    status: str | None = None
    tools: list[dict[str, Any]] | None = None

    # 使用 bytearray 累积流式数据：bytes 拼接为 O(n²) 拷贝，bytearray.extend 均摊 O(1)
    buffer = bytearray()
    # 已消费前缀偏移：用偏移指针替代逐次 del buffer[:n] 的 O(n) 前缀删除，定期批量回收均摊为 O(1)
    offset = 0
    processed_bytes = 0
    # 超限丢弃的 SSE 事件计数；用于在返回结果中区分"图片部分失败"与"事件因体积超限被丢弃"
    truncated_events = 0
    # b"\n\n" 续扫提示：记录上次未命中时的 buffer 长度，跨块续扫时跳过已确认无分隔符的前缀
    search_hint = 0

    async for chunk in response.aiter_bytes(chunk_size):
        if not chunk:
            continue

        # 规范化行尾为 \n 以兼容 CRLF/CR，使事件分隔 \n\n 判定对所有行尾风格一致，
        # 避免上游或中间代理改用 CRLF 时事件无法切分致整流丢失。
        # 仅在含 \r 时才分配替换副本；LF-only 为 SSE 流常态，此时退化为一次 memchr
        # 包含扫描，避免每块无条件分配两个全块临时对象造成的分配与 GC 开销
        raw_len = len(chunk)
        if b"\r" in chunk:
            chunk = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        buffer += chunk
        processed_bytes += raw_len

        if processed_bytes > 0 and processed_bytes % (1024 * 1024) == 0:
            log.debug("已处理 {} MB 数据", processed_bytes // 1024 // 1024)

        # SSE 事件以空行分隔，即 b"\n\n"；先抽干所有完整事件，避免后续缓冲截断时丢失已就绪事件
        while True:
            # 从 max(offset, search_hint - 1) 续扫 b"\n\n"。search_hint 记录上次未命中时的
            # buffer 长度，回退一字节以覆盖跨块分隔符边界：旧块末尾单个 \n 与新块首个 \n
            # 拼成的 \n\n。单个未完成事件按大块分多次送达时，避免每块都从 offset 重扫已确认
            # 无分隔符的尾部，将单事件扫描由平方复杂度降为线性
            sep = buffer.find(b"\n\n", max(offset, search_hint - 1))
            if sep == -1:
                search_hint = len(buffer)
                break
            # bytearray 切片已返回 bytearray 拷贝，parse_sse_segment 接受 bytes|bytearray，无需再 bytes() 转换
            segment = buffer[offset:sep]
            offset = sep + 2
            # offset 已推进至旧 search_hint 之后，重置使下次从新 offset 起扫，避免滞后提示误跳过新区间
            search_hint = 0
            event = await _parse_segment(segment, log)
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
            # 内容前移致旧索引失效；剩余部分已由上方 while 循环确认无分隔符，按当前长度刷新
            search_hint = len(buffer)

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
            truncated_events += 1
            # buffer 缩短至已消费前缀，刷新为当前长度；下次从 max(offset, len-1) 即 offset 起扫
            search_hint = len(buffer)

    trailing_event = await _parse_segment(buffer[offset:], log)
    if trailing_event is not None:
        completed, evt_usage, evt_tools = _classify_sse_event(trailing_event, model_id, items)
        if completed:
            usage = evt_usage or {}
            status = "completed"
            tools = evt_tools

    # 与非流式 _build_api_result 保持一致：当 data 项含 error 即存在部分失败时
    # 标记 status=partial，避免流式/非流式在同等部分失败场景下 status 语义不一致，
    # 进而误导下游对生成结果完整性的判断。
    if status in (None, "completed") and any(
        isinstance(item, dict) and "error" in item for item in items
    ):
        status = "partial"

    # 单个事件超限被丢弃时结果不完整，标记 partial 通知调用方存在数据丢失
    if truncated_events > 0 and status in (None, "completed"):
        status = "partial"

    return {
        "success": True,
        "data": items,
        "usage": usage,
        "status": status,
        "tools": tools,
        "truncated_events": truncated_events,
    }
