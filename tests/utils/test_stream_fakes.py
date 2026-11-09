"""流式读取共享替身 _ReadyChunks 的契约守护测试。"""

from __future__ import annotations

from _lib.stream_fakes import _ReadyChunks


async def test_ready_chunks_yields_constant_chunk_and_counts() -> None:
    """每次 __anext__ 即时交付同一块并递增计数，__aiter__ 返回自身。"""
    iterator = _ReadyChunks()

    assert aiter(iterator) is iterator
    assert await anext(iterator) == b"chunk"
    assert await anext(iterator) == b"chunk"
    assert iterator.anext_calls == 2
