"""响应流读取与 data 条目归一的通用原语：限时取块、超限消息提示与完成态升格。

限时取块经 StreamSession 由常驻生产者任务与容量 1 的队列承载，含等块限时、
到点打捞与取块入口的墙钟复查，供 sse 的 SSE 解析与 client 的流式及非流式
读体共用；条目判定辅助另供 sse、client 及工具与 Web 出口共用，不含 SSE
事件语义。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any, cast


class StreamDeadlineExceeded(Exception):
    """限时读取会话命中截止，已无更多数据块可产。"""


# 响应体超限消息的环境变量调整提示，读体路径的共用单一来源。
RESPONSE_BODY_LIMIT_HINT = "，可经 SEEDREAM_RESPONSE_BODY_LIMIT 调整"


# 流结束标记：生产者拉尽迭代器后入队，与字节块区分，兼作流结束终态值。
_STREAM_ENDED = object()

# 截止命中终态标记：重入取块时抛新构造的 StreamDeadlineExceeded。
_DEADLINE_EXCEEDED = object()


class _ProducerFailure:
    """生产者捕获的迭代器异常包装，经队列送交消费方原样上抛。"""

    __slots__ = ("error",)

    def __init__(self, error: BaseException) -> None:
        self.error = error


class StreamSession:
    """限时取块的读取会话：常驻生产者任务拉取迭代器经容量 1 的队列供块。

    每响应一个生产者任务，等块不逐块临时建任务；读取与等待解耦，等块超时与
    取块入口的墙钟复查两条截止路径共用同一裁决：同批已入队的非空块与流结束
    打捞返回，防止已完成的响应被误判超时、触发非幂等生成请求的重复计费
    重试；迭代器已拉尽时的残余打捞升格为流结束——结束标记可因随后的生产者
    取消未入队，完整收到的响应按正常完成交付。打捞仅到点后首次发生，闩锁
    一经置位即终局锁定。消费方提前离开（截止终态、异常上抛或 async with
    退出）时取消生产者，在途的迭代器拉取一并取消。
    """

    def __init__(self, iterator: AsyncIterator[bytes]) -> None:
        self._iterator = iterator
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue(1)
        self._producer: asyncio.Task[None] | None = None
        # 收尾标记：置位后生产者收到的取消不再包装送队，直接按任务取消收场。
        self._closing = False
        # 迭代器拉尽标记：先于结束标记的送交置位，标记因取消未入队时仍可识别完整流。
        self._iterator_drained = False
        # 终态产出：流结束、截止命中或迭代器异常，已交付后续取块按值重放，不再触队。
        self._terminal: object | None = None

    async def next_data_chunk(self, deadline: float | None) -> bytes | None:
        """取下一个非空数据块；流尽返回 None，截止命中抛 StreamDeadlineExceeded。

        重组分块与慢速上游的下一块间隔可长于事件间隔，等块本身限时使总时长预算
        可中断；到点时已入队的非空块与流结束优先于超时判定返回，不被取消摧毁，
        防止已完成的响应被误判超时、触发非幂等生成请求的重复计费重试；打捞仅
        到点后首次发生，越界读取收敛到一个块，读体时长不脱出单次尝试时限，且
        迭代器已拉尽时升格为流结束而非截止终态。持续就绪的流等块可不挂起，
        截止经每次取块入口的墙钟复查浮现，入口越界与等块超时经同一截止裁决
        收场，到点后按同批入队的有限块收场。
        截止命中为会话级终局闩锁：单会话单一 deadline，超时后会话应整体丢弃，不带 deadline 重入续读。
        """
        if isinstance(self._terminal, BaseException):
            raise self._terminal
        if self._terminal is _STREAM_ENDED:
            return None
        if self._terminal is _DEADLINE_EXCEEDED:
            raise StreamDeadlineExceeded
        item: bytes | object | None = None
        if self._producer is None:
            self._producer = asyncio.ensure_future(self._produce())
        try:
            # 入口越界与等块超时同口径：让出一个轮次待同批项入队，先打捞后判超时。
            if deadline is not None and time.monotonic() > deadline:
                await asyncio.sleep(0)
                item = await self._adjudicate_deadline()
            while True:
                if item is None:
                    if deadline is None:
                        item = await self._queue.get()
                    else:
                        try:
                            async with asyncio.timeout_at(deadline):
                                item = await self._queue.get()
                        except TimeoutError:
                            item = await self._adjudicate_deadline()
                # 出队项单点分派：等块与两路截止打捞共用，终态产出随分派落位，
                # 失败与流结束覆写同批的截止标记；截止标记兼打捞事实：裁决到
                # 分派无 await，标记必源自本次裁决。
                if isinstance(item, _ProducerFailure):
                    self._terminal = item.error
                    raise item.error
                if item is _STREAM_ENDED:
                    self._terminal = _STREAM_ENDED
                    return None
                chunk = cast("bytes", item)
                if chunk:
                    if self._terminal is _DEADLINE_EXCEEDED and self._iterator_drained:
                        # 结束标记随生产者取消丢弃而迭代器已拉尽：残余即完整
                        # 响应的末块，升格流结束，不按截止误判触发重试。
                        self._terminal = _STREAM_ENDED
                    return chunk
                if self._terminal is _DEADLINE_EXCEEDED:
                    if self._iterator_drained:
                        # 空末块与已拉尽同口径：完整收尾无更多数据，按流结束交付。
                        self._terminal = _STREAM_ENDED
                        return None
                    # 空块不救：立即就绪的空块流每次跑赢到点，超时永不浮现、读循环自旋。
                    raise StreamDeadlineExceeded
                item = None
        except BaseException:
            # 消费侧被取消等外因弃置等待时，同步收尾生产者与其在途拉取。
            await self._abort_producer()
            raise

    async def _adjudicate_deadline(self) -> bytes | object:
        """共用截止裁决：打捞同批已入队项并收尾生产者后落闩锁，无可打捞抛 StreamDeadlineExceeded。

        打捞交付后越界读取收敛到一个块，无可打捞亦终局锁定；返回项由取块的
        单点分派处置，迭代器已拉尽时的升格在分派内完成。
        """
        item = None if self._queue.empty() else self._queue.get_nowait()
        await self._abort_producer()
        self._terminal = _DEADLINE_EXCEEDED
        if item is None:
            raise StreamDeadlineExceeded
        return item

    async def _produce(self) -> None:
        """常驻生产者：循环拉取迭代器下一块入队，流尽送结束标记，异常送失败包装。"""
        try:
            while True:
                chunk = await self._iterator.__anext__()
                await self._queue.put(chunk)
        except StopAsyncIteration:
            # 拉尽标记先于送结束标记置位：随后的 put 可因取消丢弃，标记保住
            # 「已正常拉尽」事实，消费方据此把末块打捞升格为流结束。
            self._iterator_drained = True
            await self._queue.put(_STREAM_ENDED)
        except BaseException as exc:
            if self._closing:
                raise
            await self._queue.put(_ProducerFailure(exc))

    async def _abort_producer(self) -> None:
        """取消常驻生产者并等待退出；未启动或已结束时为空操作。"""
        task = self._producer
        if task is None or task.done():
            return
        self._closing = True
        consumer = asyncio.current_task()
        cancelling_before = consumer.cancelling() if consumer is not None else 0
        task.cancel()
        try:
            await task
        except BaseException:
            # 等待期间送达消费方的新取消以取消计数增量识别，重抛不被收尾吞掉；
            # 其余为生产者被故意取消的收尾异常，不外溢。
            if consumer is not None and consumer.cancelling() > cancelling_before:
                raise asyncio.CancelledError()

    async def aclose(self) -> None:
        """会话收尾：消费方提前离开读取循环时取消生产者与在途拉取。"""
        await self._abort_producer()

    async def __aenter__(self) -> StreamSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


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
