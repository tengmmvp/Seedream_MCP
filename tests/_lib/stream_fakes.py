"""流式读取会话共享的异步迭代器替身。

即时就绪分块迭代器供 test_stream_session 与 test_sse_parser_buffer 复用，
载荷可配常量字节或按 1 起调用序号产出的工厂，互异编号块与就绪空块流均经
其构造。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable


class _ReadyChunks:
    """__anext__ 无挂起点即时交付载荷并计数，供断言生产者拉取次数与交付内容。"""

    def __init__(self, payload: bytes | Callable[[int], bytes] = b"chunk") -> None:
        self.anext_calls = 0
        self._payload = payload

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        self.anext_calls += 1
        if callable(self._payload):
            return self._payload(self.anext_calls)
        return self._payload
