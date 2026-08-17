"""SSE 流式响应解析。

将 Seedream API 的 Server-Sent Events 流式响应增量解析为统一的图片项列表与完成
元信息，供 client 的流式请求路径调用。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, cast

from ..core.errors import (
    SeedreamAPIError,
    sanitize_error_text,
    truncate_upstream_message_fragment,
)


def is_sse_response(response: Any) -> bool:
    """判断响应是否为 SSE 事件流。

    先剥离 ``;`` 参数与首尾空白再判定 media type，兼容含前导空白的
    `` text/event-stream`` 与带 charset 参数的 ``text/event-stream; charset=utf-8``。

    Args:
        response: HTTP 响应对象。

    Returns:
        Content-Type 为 text/event-stream 返回 True，否则返回 False。
    """
    content_type = str(response.headers.get("content-type", ""))
    media_type = content_type.split(";")[0].strip().lower()
    return media_type.startswith("text/event-stream")


def format_sse_success_event(event: dict[str, Any], model_id: str) -> dict[str, Any]:
    """将 SSE 成功事件转换为统一图片项结构。

    Args:
        event: SSE 成功事件的 JSON 对象。
        model_id: 模型标识，事件缺失 model 字段时填充缺省值。

    Returns:
        统一图片项结构的字典。
    """
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
    """将 SSE 失败事件转换为统一图片项结构。

    error.message 为上游自由文本，经 sanitize_error_text 剥离敏感片段与控制字符并
    截断后再进入图片项，防止被劫持的中间层借 per-image 错误回显凭据直达用户可见输出。

    Args:
        event: SSE 失败事件的 JSON 对象。
        model_id: 模型标识，事件缺失 model 字段时填充缺省值。

    Returns:
        统一图片项结构的字典，含净化后的 error 字段。
    """
    raw_error = event.get("error")
    error = raw_error if isinstance(raw_error, dict) else {}
    return {
        "error": {
            "code": error.get("code"),
            "message": sanitize_error_text(error.get("message")),
        },
        "image_index": event.get("image_index"),
        "model": event.get("model", model_id),
        "created": event.get("created", int(time.time())),
        "type": event.get("type", "image_generation.partial_failed"),
    }


def _extract_data_field_values(raw_segment: bytes | bytearray) -> list[bytes | bytearray]:
    """按 SSE 规范提取段内全部 data: 字段值，多行值由调用方以换行拼接。

    每行仅剥离字段名后的首个前导空格，其余空白属于负载本身；json.loads 对 JSON
    负载的前后空白天然容忍，单空格剥离已使 ``data: x`` 与 ``data:x`` 语义一致。
    """
    values: list[bytes | bytearray] = []
    for line in raw_segment.split(b"\n"):
        if line.startswith(b"data:"):
            value = line[5:]
            if value.startswith(b" "):
                value = value[1:]
            values.append(value)
    return values


def _has_lost_data_payload(tail: bytes | bytearray) -> bool:
    """判断流末尾残留段是否携带实际丢失的 data 负载。

    空白行、注释行与 ``[DONE]`` 哨兵经 parse_sse_segment 同样返回 None，但均不构成
    数据丢失；仅当残留段含非空且非哨兵的 data 负载时，解析失败才计为丢失事件。
    """
    for value in _extract_data_field_values(tail):
        stripped = value.strip()
        if stripped and stripped != b"[DONE]":
            return True
    return False


def parse_sse_segment(segment: bytes | bytearray, log: Any | None = None) -> dict[str, Any] | None:
    """解析单个 SSE 事件段，返回事件对象。

    解析失败时记录日志并返回 None。负载全程按 bytes 处理并直接交给 json.loads，
    避免大事件场景下整段事件的 str decode 分配造成瞬时内存峰值。
    """
    start = 0
    end = len(segment)
    while start < end and segment[start] in b" \t\r\n":
        start += 1
    while end > start and segment[end - 1] in b" \t\r\n":
        end -= 1
    raw_segment = segment[start:end] if start > 0 or end < len(segment) else segment
    if not raw_segment:
        return None

    try:
        # Seedream SSE 事件将 JSON 负载承载在 data: 字段中；按 SSE 规范多行 data: 以换行拼接为完整负载，event:/id: 字段本接口未使用。
        data_parts = _extract_data_field_values(raw_segment)
        payload = b"\n".join(data_parts) if data_parts else None
        # [DONE] 为流结束哨兵而非图片事件，直接丢弃。
        if not payload or payload == b"[DONE]":
            return None
        parsed_payload = json.loads(payload)
        if not isinstance(parsed_payload, dict):
            raise ValueError("SSE 事件数据必须是对象")
        return cast(dict[str, Any], parsed_payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, IndexError) as exc:
        if log is not None:
            log.error("SSE事件解析失败: {}", str(exc))
            log.debug("SSE事件原始段长度: {} bytes", len(raw_segment))
        return None


# 大事件卸载阈值：超过此大小的 segment 的「切片 + json.loads」整体改到工作线程执行，
# 避免 stream + b64_json 多 MB 事件在事件循环中产生 memcpy 与解析阻塞。小事件保持
# 同步处理以省去线程调度开销。
_SSE_OFFLOAD_THRESHOLD = 64 * 1024

# 处理进度 debug 日志的最小字节间隔：按增量阈值记录，替代对整 MB 取模的判定，模判定
# 在任意 chunk_size 下几乎不会恰好命中。
_SSE_PROGRESS_LOG_INTERVAL_BYTES = 16 * 1024 * 1024

# 单个 SSE 事件线上形态的最小字节估计：合法事件含 data: 字段名、JSON 对象信封与
# 空行分隔。该值是保守上界而非紧确界，极简事件的线上形态可比其更小，按其派生的
# 条目上限相应偏松；上限的存在性与字节总量上限同源，共同约束解析产物的内存放大。
_SSE_MIN_EVENT_BYTES = 64

# 解析条目数的绝对下限：组图单请求合法产出至多 15 张图片，总量限额极小的部署按字节
# 下界推导会得到误伤合法批次的过小上限，绝对下限兜底。
_SSE_MIN_ITEMS_LIMIT = 64


def _slice_parse_segment(
    buffer: bytearray, start: int, end: int, log: Any
) -> dict[str, Any] | None:
    """在工作线程内切出 buffer[start:end] 事件段并解析。

    bytearray 切片是一次 memcpy，单事件上限约 event_truncate_threshold 量级，
    与 json.loads 一并下沉线程，避免两者在事件循环上叠加阻塞。
    """
    return parse_sse_segment(buffer[start:end], log)


async def _parse_segment_range(
    buffer: bytearray, start: int, end: int, log: Any
) -> dict[str, Any] | None:
    """切出 buffer[start:end] 事件段并解析，大段把切片与解析一并卸载到工作线程。

    主循环在 await 期间挂起，buffer 的全部变更点（追加、前缀回收、超限截断）均位于
    本协程内，协程挂起即冻结追加，线程内读到的 buffer 内容稳定，无需快照副本。
    小段在事件循环内同步切片解析，避免线程调度开销。返回值语义与 ``parse_sse_segment``
    一致。
    """
    if end - start > _SSE_OFFLOAD_THRESHOLD:
        return await asyncio.to_thread(_slice_parse_segment, buffer, start, end, log)
    return parse_sse_segment(buffer[start:end], log)


async def _close_stream_response(response: Any) -> None:
    """尽力关闭流式响应，停止继续从上游读取。

    仅持有 aclose 的 httpx 响应需要显式关闭；伪响应对象（测试替身）无此方法则跳过。
    关闭失败不掩盖待抛出的超限错误。
    """
    aclose = getattr(response, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        pass


def _classify_sse_event(
    event: dict[str, Any],
    model_id: str,
    items: list[dict[str, Any]],
    log: Any,
    segment_len: int,
) -> tuple[bool, dict[str, Any] | None, list[dict[str, Any]] | None]:
    """分类单个 SSE 事件：追加图片项或返回完成元信息；请求级错误抛 SeedreamAPIError。

    主循环与流末尾残留处理共用此函数，避免事件分支逻辑重复。未识别 type 的事件丢弃
    并记录 debug 日志，携带事件 type 与事件段字节规模，便于排查上游新增事件形态。

    Args:
        event: 已解析的 SSE 事件对象。
        model_id: 模型标识，用于图片项缺省值。
        items: 图片项累计列表，成功与失败事件直接追加至此。
        log: loguru logger 实例。
        segment_len: 事件段字节规模，用于未知类型事件的排查日志。

    Returns:
        (completed, usage, tools) — completed 为 True 时 usage/tools 有效。

    Raises:
        SeedreamAPIError: 事件为请求级错误时抛出，status_code 固定为 400。
    """
    event_type = event.get("type")
    # 请求级错误事件：无 type 且顶层含 error 键。本质为 4xx，标记 status_code=400 使上层判定不可重试。
    if event_type is None and isinstance(event.get("error"), dict):
        err = event["error"]
        raw_code = err.get("code")
        raise SeedreamAPIError(
            # message 经与 handle_api_error 相同的 8KB 截断辅助处理，超大错误体不随
            # 异常进入日志；非字符串形态由该辅助归一化为文本。
            message=truncate_upstream_message_fragment(err.get("message", "流式请求失败")),
            status_code=400,
            # 仅接受非空字符串错误码，与 errors.handle_api_error 同口径：上游数字码
            # 转字符串属臆测语义，其余类型置 None 丢弃。
            error_code=raw_code if isinstance(raw_code, str) and raw_code else None,
        )
    if event_type == "image_generation.partial_succeeded":
        items.append(format_sse_success_event(event, model_id))
    elif event_type == "image_generation.partial_failed":
        items.append(format_sse_failed_event(event, model_id))
    elif event_type == "image_generation.completed":
        return True, event.get("usage", {}) or {}, event.get("tools")
    else:
        log.debug(
            "忽略未知类型的 SSE 事件: type={!r}, 段长 {} 字节",
            event_type,
            segment_len,
        )
    return False, None, None


async def parse_sse_response(
    response: Any,
    *,
    model_id: str,
    chunk_size: int,
    buffer_max_size: int,
    event_truncate_threshold: int,
    total_bytes_limit: int,
    log: Any,
    deadline: float | None = None,
) -> dict[str, Any]:
    """增量解析 SSE 响应为统一的图片项列表与完成元信息。

    Args:
        response: httpx 流式响应对象，按 ``chunk_size`` 分块读取。
        model_id: 模型标识，用于填充图片项 model 字段的缺省值。
        chunk_size: 每次从流中读取的字节数。
        buffer_max_size: 已消费前缀的回收阈值，buffer 偏移达到此值时批量回收前缀以控制
            常驻内存。不用于单事件截断，避免合法大图被误丢。
        event_truncate_threshold: 单个未完成 SSE 事件的截断阈值，仅作防异常流无限增长
            撑爆内存的安全阀；须大于单张合法图片 base64 负载上限，避免 stream + b64_json
            的大图事件被误截断而永久丢失。与 buffer_max_size 解耦，前者管前缀回收频率，
            后者管单事件体积上限。
        total_bytes_limit: 响应流累计接收字节总量上限，含全部事件段与不完整尾部。
            单事件阈值拦不住以大量小事件滴流的超限流，累计总量在读取过程中强制此上限，
            超限时终止解析并关闭响应。与非流式/流式 JSON 路径共用同一限额。解析产物
            的图片项条目数另按该限额除以最小事件字节下界派生独立硬上限，大量小事件
            在字节未超限时仍触顶终止，防止解析产物内存放大。
        log: loguru logger 实例，用于记录进度与告警。
        deadline: 解析全程的 time.monotonic 截止时间，None 表示不施加。读取超时按
            单次读操作计时，上游以小于该间隔持续滴流时读取超时永不触发，截止时间
            逐块封顶整个解析阶段；超限时关闭响应并抛 asyncio.TimeoutError，由
            client 侧并入超时重试路径。

    Returns:
        包含 success/data/usage/status/tools 的统一结果字典。

    Raises:
        SeedreamAPIError: 响应流累计接收字节超过 total_bytes_limit，或上游返回请求级
            错误事件。
        asyncio.TimeoutError: 提供 deadline 且解析中途超过截止时间。
    """
    items: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    status: str | None = None
    tools: list[dict[str, Any]] | None = None

    def apply_completed(
        completed: bool,
        evt_usage: Any,
        evt_tools: list[dict[str, Any]] | None,
    ) -> None:
        """记录完成事件的元信息，主循环与流末尾残留处理共用。

        上游 completed 事件的 usage 字段异形（如字符串或数字）时收敛为空 dict，
        与非流式 client._build_api_result 的守卫同口径，保证结果结构 usage 恒为 dict。
        """
        nonlocal usage, status, tools
        if completed:
            if isinstance(evt_usage, dict):
                usage = evt_usage
            else:
                log.debug(
                    "SSE completed 事件 usage 非 dict（{}），已收敛为空 dict",
                    type(evt_usage).__name__,
                )
                usage = {}
            status = "completed"
            tools = evt_tools

    # 使用 bytearray 累积流式数据：bytes 拼接为 O(n²) 拷贝，bytearray.extend 均摊 O(1)。
    buffer = bytearray()
    # 已消费前缀偏移：用偏移指针替代逐次 del buffer[:n] 的 O(n) 前缀删除，定期批量回收均摊为 O(1)。
    offset = 0
    processed_bytes = 0
    # 上次进度日志记录时的累计字节数，与 _SSE_PROGRESS_LOG_INTERVAL_BYTES 共同决定记录时机。
    last_progress_log_bytes = 0
    # 超限丢弃的 SSE 事件计数；用于在返回结果中区分“图片部分失败”与“事件因体积超限被丢弃”。
    truncated_events = 0
    # b"\n\n" 续扫提示：记录上次未命中时的 buffer 长度，跨块续扫时跳过已确认无分隔符的前缀。
    search_hint = 0
    # 上一块末尾孤立 \r 的悬置标志。块尾 \r 无法独立判定是 CRLF 前半还是单独 CR 行尾，
    # 先不归一化，留待与次块首字节拼接后判定，防止其被提前转为 \n 与次块首 \n 拼成
    # \n\n 假事件分隔符导致多行 data 事件被静默拆丢。
    pending_cr = False
    # 图片项条目数硬上限：总量上限约束的是线上接收字节，解析产物 items 为逐事件
    # 物化的 dict 列表，大量小事件在字节未超限时仍可放大内存；按最小事件字节下界
    # 从同一限额派生条目上界，字节限额不变时条目数有独立封顶。
    max_items = max(total_bytes_limit // _SSE_MIN_EVENT_BYTES, _SSE_MIN_ITEMS_LIMIT)
    # 条目数触顶标志：触顶后终止读取，流末尾残留段不再解析。
    items_capped = False

    async for chunk in response.aiter_bytes(chunk_size):
        if not chunk:
            continue

        # 总时长预算：逐块检查截止时间。读取超时按单次读操作计时，上游以小于该
        # 间隔持续滴流时读取超时永不触发，截止时间封顶整个解析阶段；超限关闭响应
        # 并抛超时类错误，由 client 侧并入既有超时重试路径。
        if deadline is not None and time.monotonic() > deadline:
            log.warning("SSE 响应流超过总时长预算，终止解析")
            await _close_stream_response(response)
            raise asyncio.TimeoutError("SSE 响应流读取超过总时长预算")

        # 规范化行尾为 \n 以兼容 CRLF/CR，使事件分隔 \n\n 判定对所有行尾风格一致，
        # 避免上游或中间代理改用 CRLF 时事件无法切分致整流丢失。
        # 悬置 \r 前置拼回本块头部一并归一化，块尾新出现的孤立 \r 撤出本块继续悬置。
        # 仅在含 \r 时才分配替换副本；LF-only 为 SSE 流常态，此时退化为一次 memchr
        # 包含扫描，避免每块无条件分配两个全块临时对象造成的分配与 GC 开销。
        raw_len = len(chunk)
        if pending_cr:
            chunk = b"\r" + chunk
            pending_cr = False
        if b"\r" in chunk:
            normalized = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if chunk.endswith(b"\r"):
                normalized = normalized[:-1]
                pending_cr = True
            chunk = normalized
        buffer += chunk
        processed_bytes += raw_len

        # 响应流总量上限：单事件截断阈值拦不住以大量小事件滴流的超限流，累计接收
        # 字节超限即终止解析并关闭响应，防止恶意或受损上游无限送数撑爆内存。
        if processed_bytes > total_bytes_limit:
            log.warning(
                "SSE 响应流总量超限: 已接收 {} 字节，上限 {} 字节",
                processed_bytes,
                total_bytes_limit,
            )
            await _close_stream_response(response)
            raise SeedreamAPIError(
                f"SSE 响应流总量超限: 已接收 {processed_bytes} 字节，"
                f"超过上限 {total_bytes_limit} 字节，可经 SEEDREAM_RESPONSE_BODY_LIMIT 调整"
            )

        if processed_bytes - last_progress_log_bytes >= _SSE_PROGRESS_LOG_INTERVAL_BYTES:
            last_progress_log_bytes = processed_bytes
            log.debug("已处理 {} 字节数据", processed_bytes)

        # SSE 事件以空行分隔，即 b"\n\n"；先抽干所有完整事件，避免后续缓冲截断时丢失已就绪事件。
        while True:
            # 从 max(offset, search_hint - 1) 续扫 b"\n\n"。search_hint 记录上次未命中时的
            # buffer 长度，回退一字节以覆盖跨块分隔符边界：旧块末尾单个 \n 与新块首个 \n
            # 拼成的 \n\n。单个未完成事件按大块分多次送达时，避免每块都从 offset 重扫已确认
            # 无分隔符的尾部，将单事件扫描由平方复杂度降为线性。
            sep = buffer.find(b"\n\n", max(offset, search_hint - 1))
            if sep == -1:
                search_hint = len(buffer)
                break
            seg_start = offset
            offset = sep + 2
            # offset 已推进至旧 search_hint 之后，重置使下次从新 offset 起扫，避免滞后提示误跳过新区间。
            search_hint = 0
            event = await _parse_segment_range(buffer, seg_start, sep, log)
            if event is None:
                continue
            apply_completed(*_classify_sse_event(event, model_id, items, log, sep - seg_start))

        # 条目数硬上限：与单事件截断同口径终止解析并计数标记 partial，通知调用方
        # 结果不完整；触顶后关闭响应停止读取，后续流内容不再消费。
        if len(items) >= max_items:
            log.warning(
                "SSE 事件条目数超过上限 {}: 已累计 {} 条，终止解析",
                max_items,
                len(items),
            )
            await _close_stream_response(response)
            truncated_events += 1
            items_capped = True
            break

        # 周期性回收已消费前缀；阈值取 buffer_max_size，使每次 O(n) 回收均摊到至少 buffer_max_size 字节。
        if offset > 0 and offset >= buffer_max_size:
            del buffer[:offset]
            offset = 0
            # 内容前移致旧索引失效；剩余部分已由上方 while 循环确认无分隔符，按当前长度刷新。
            search_hint = len(buffer)

        # 不完整尾部超过缓冲区上限：单个事件体积过大，无法完整解析。
        # while 循环已抽干所有完整事件，故 [offset, end) 必为单个未完成事件；
        # 丢弃该尾部以免内存无限增长，[0, offset) 内的完整事件均已处理进 items，不会跨界错位。
        live_len = len(buffer) - offset
        if live_len > event_truncate_threshold:
            log.warning(
                "单个 SSE 事件超过截断阈值 ({} > {})，丢弃该不完整事件",
                live_len,
                event_truncate_threshold,
            )
            del buffer[offset:]
            truncated_events += 1
            # buffer 缩短至已消费前缀，刷新为当前长度；下次从 max(offset, len-1) 即 offset 起扫。
            search_hint = len(buffer)

    # 条目数触顶时流已被终止，残留段不再解析；正常流结束才进入末尾残留处理。
    if not items_capped:
        # 流在悬置 \r 处结束：单独 CR 亦为完整行尾，补作 \n 保持归一语义后进入残留解析。
        if pending_cr:
            buffer += b"\n"
        trailing_len = len(buffer) - offset
        trailing_event = await _parse_segment_range(buffer, offset, len(buffer), log)
        if trailing_event is not None:
            apply_completed(
                *_classify_sse_event(trailing_event, model_id, items, log, trailing_len)
            )
        elif trailing_len > 0 and _has_lost_data_payload(buffer[offset:]):
            # 流末尾残留段含 data 负载但解析失败：与超阈值丢事件同口径计数并记录，
            # truncated_events 使 status 标记 partial，通知调用方结果不完整。
            truncated_events += 1
            log.debug("流末尾不完整事件解析失败，丢弃 {} 字节", trailing_len)

    # 与非流式 _build_api_result 保持一致：当 data 项含 error 即存在部分失败时
    # 标记 status=partial，避免流式/非流式在同等部分失败场景下 status 语义不一致，
    # 进而误导下游对生成结果完整性的判断。
    if status in (None, "completed") and any(
        isinstance(item, dict) and "error" in item for item in items
    ):
        status = "partial"

    # 单个事件超限被丢弃时结果不完整，标记 partial 通知调用方存在数据丢失。
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
