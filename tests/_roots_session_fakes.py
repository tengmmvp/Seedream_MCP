"""roots 会话替身共享定义。

test_workspace_roots_scope 与 test_server_roots_dependency 共用，避免测试
模块间横向 import。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from mcp.types import ListRootsResult, Root
from pydantic import FileUrl


def roots_result(roots: list[Path]) -> ListRootsResult:
    """构造工具链 resolver 注入形态的 roots 结果。"""
    return ListRootsResult(
        roots=[Root(uri=cast(FileUrl, root.as_uri()), name=root.name) for root in roots]
    )


class FakeSession:
    """按 SDK 契约经 send_request 应答 roots/list 的会话替身，记录调用参数。"""

    def __init__(self, roots: list[Path]) -> None:
        self._roots = roots
        self.send_request_calls: list[dict[str, Any]] = []

    def _record_send_request(
        self,
        request: Any,
        request_read_timeout_seconds: float | None,
        metadata: Any,
    ) -> None:
        """记录一次 send_request 的调用参数，供调用形态断言消费。"""
        self.send_request_calls.append(
            {
                "request": request,
                "request_read_timeout_seconds": request_read_timeout_seconds,
                "metadata": metadata,
            }
        )

    async def send_request(
        self,
        request: Any,
        result_type: Any,
        request_read_timeout_seconds: float | None = None,
        metadata: Any = None,
        progress_callback: Any = None,
    ) -> ListRootsResult:
        del result_type, progress_callback
        self._record_send_request(request, request_read_timeout_seconds, metadata)
        return await self._conclude_send_request(request_read_timeout_seconds)

    async def _conclude_send_request(
        self, request_read_timeout_seconds: float | None
    ) -> ListRootsResult:
        """send_request 记录参数后的结局，子类覆写注入返回/抛错/悬挂形态。"""
        del request_read_timeout_seconds
        return roots_result(self._roots)


class CapabilityDeclaringSession(FakeSession):
    """带 capability 探测的会话替身：check_client_capability 返回固定声明结果。"""

    def __init__(self, roots: list[Path], declared: bool) -> None:
        super().__init__(roots)
        self.declared = declared
        self.capability_probes = 0

    def check_client_capability(self, capability: object) -> bool:
        self.capability_probes += 1
        return self.declared


class ProbingErrorSession(CapabilityDeclaringSession):
    """capability 探测即抛异常的会话替身。"""

    def __init__(self, roots: list[Path]) -> None:
        super().__init__(roots, declared=True)

    def check_client_capability(self, capability: object) -> bool:
        raise RuntimeError("capability probe broken")


class BackChannelSession(CapabilityDeclaringSession):
    """带固定反向通道探测结果的会话替身。"""

    def __init__(self, roots: list[Path], can_send_request: bool) -> None:
        super().__init__(roots, declared=True)
        self.can_send_request = can_send_request


class BackChannelUnavailableSession(BackChannelSession):
    """声明 roots capability 但反向通道不可用的会话替身，send_request 一经发起即失败。"""

    def __init__(self, roots: list[Path]) -> None:
        super().__init__(roots, False)

    async def _conclude_send_request(
        self, request_read_timeout_seconds: float | None
    ) -> ListRootsResult:
        del request_read_timeout_seconds
        raise AssertionError("反向通道不可用时不得发起 roots/list")


class FailingSession(CapabilityDeclaringSession):
    """声明 roots 且反向通道可用的会话替身：send_request 记录参数后抛 RuntimeError。"""

    def __init__(self) -> None:
        super().__init__([Path("/workspace")], declared=True)
        self.can_send_request = True

    async def _conclude_send_request(
        self, request_read_timeout_seconds: float | None
    ) -> ListRootsResult:
        del request_read_timeout_seconds
        raise RuntimeError("send_request failed")


class HangingSession(CapabilityDeclaringSession):
    """声明 roots 且反向通道可用的会话替身：send_request 永不完成，出站写被流控暂停，读超时无从起算。"""

    def __init__(self) -> None:
        super().__init__([Path("/workspace")], declared=True)
        self.can_send_request = True

    async def _conclude_send_request(
        self, request_read_timeout_seconds: float | None
    ) -> ListRootsResult:
        del request_read_timeout_seconds
        await asyncio.sleep(3600)
        raise AssertionError("悬挂的 send_request 不应被等到")
