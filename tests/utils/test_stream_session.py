"""StreamSession 截止闩锁与等块超时、墙钟复查两路打捞收场的终局语义测试。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

import seedream_mcp.utils.io.stream as stream_module
from _lib.stream_fakes import _ReadyChunks


async def test_deadline_latch_is_final_for_any_reentry() -> None:
    """闩锁一经置位即终局：同值、更晚与不带 deadline 重入均抛截止异常且不再拉取。"""
    iterator = _ReadyChunks()
    async with stream_module.StreamSession(iterator) as session:
        deadline = time.monotonic() - 1
        assert await session.next_data_chunk(deadline) == b"chunk"
        assert iterator.anext_calls == 2
        for reentry in (deadline, deadline + 60, None):
            with pytest.raises(stream_module.StreamDeadlineExceeded):
                await session.next_data_chunk(reentry)
        assert iterator.anext_calls == 2


async def test_deadline_latch_returns_no_distinct_chunk_after_latch() -> None:
    """互异块闩锁后不再取到任何块，守护解闩重启生产者丢停泊块的缺陷回归。"""
    iterator = _ReadyChunks(payload=lambda index: f"chunk-{index}".encode())
    async with stream_module.StreamSession(iterator) as session:
        expired = time.monotonic() - 1
        assert await session.next_data_chunk(expired) == b"chunk-1"
        # 次块已预取停泊在队列写入处，随生产者取消一并丢弃。
        assert iterator.anext_calls == 2
        for reentry in (expired, expired + 60, None):
            with pytest.raises(stream_module.StreamDeadlineExceeded):
                await session.next_data_chunk(reentry)
        assert iterator.anext_calls == 2


async def test_deadline_salvage_of_drained_final_chunk_upgrades_to_ended() -> None:
    """末块在截止瞬间入队且结束标记因取消丢失：按流结束收场，完整流不误判超时。"""
    loop = asyncio.get_running_loop()
    gate = loop.create_future()

    async def _single_chunk() -> AsyncIterator[bytes]:
        await gate
        yield b"only"

    def _deliver() -> None:
        gate.set_result(None)

    # 交付回调先于生产者首步排队：末块入队与到点取消同批落地，
    # 结束标记仍停泊在队列写入处，复现审查实证的竞态形态。
    loop.call_soon(_deliver)
    session = stream_module.StreamSession(_single_chunk().__aiter__())
    assert await session.next_data_chunk(time.monotonic() - 1.0) == b"only"
    # 结束标记随生产者取消丢弃，末块打捞后按流结束收场而非截止终态。
    assert await session.next_data_chunk(time.monotonic() - 1.0) is None


async def test_deadline_salvage_of_drained_empty_final_chunk_upgrades_to_ended() -> None:
    """末块为空且迭代器已拉尽：空残余与末块同口径升格流结束，不误抛截止异常。"""
    loop = asyncio.get_running_loop()
    gate = loop.create_future()

    async def _empty_final_chunk() -> AsyncIterator[bytes]:
        await gate
        yield b""

    def _deliver() -> None:
        gate.set_result(None)

    # 空末块入队与到点取消同批落地、迭代器已拉尽而结束标记停泊在队列写入处，
    # 与非空末块同一竞态形态。
    loop.call_soon(_deliver)
    session = stream_module.StreamSession(_empty_final_chunk().__aiter__())
    assert await session.next_data_chunk(time.monotonic() - 1.0) is None
    # 升格流结束后续取块直接返回 None，不再触队。
    assert await session.next_data_chunk(time.monotonic() - 1.0) is None


class _FiniteNumberedChunks:
    """就绪交付有限编号块；拉尽那次 __anext__ 先把假时钟拨越界再抛 StopAsyncIteration。"""

    def __init__(self, count: int, clock: list[float]) -> None:
        self._count = count
        self._clock = clock
        self.yielded = 0

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        if self.yielded >= self._count:
            self._clock[0] = 100.0
            raise StopAsyncIteration
        self.yielded += 1
        return f"chunk-{self.yielded}".encode()


async def _drain_ready_stream(monkeypatch: pytest.MonkeyPatch, count: int) -> list[bytes]:
    """消费 count 块的就绪有限流直至收场，返回全部已交付块。

    假时钟初始在预算内、迭代器拉尽时拨越界；每块交付后让出一个循环轮次，使
    生产者把下一块或结束标记先行入队，取块入口的裁决面对的是已入队项。
    """
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    iterator = _FiniteNumberedChunks(count, clock)
    delivered: list[bytes] = []
    async with stream_module.StreamSession(iterator) as session:
        deadline = time.monotonic() + 50.0
        while True:
            chunk = await session.next_data_chunk(deadline)
            if chunk is None:
                # 流结束终态可复返 None；闩锁误收场时此处抛截止异常。
                assert await session.next_data_chunk(deadline) is None
                break
            delivered.append(chunk)
            await asyncio.sleep(0)
    return delivered


async def test_overshoot_at_exhaustion_delivers_queued_stream_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """迭代器拉尽拨越界假钟后，已入队的结束标记经入口打捞升格流结束，完整流不误抛截止。

    直接抛截止会把完整收到的响应误判超时触发重试；末块与结束标记均先行
    入队，取块入口的越界裁决按流结束正常交付。
    """
    delivered = await _drain_ready_stream(monkeypatch, count=33)

    assert [int(chunk.rsplit(b"-", 1)[1]) for chunk in delivered] == list(range(1, 34))
