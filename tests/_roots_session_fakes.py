"""roots 会话替身共享定义。

test_workspace_roots_scope 与 test_server_roots_dependency 共用，避免测试
模块间横向 import。
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from mcp.types import ListRootsResult, Root
from pydantic import FileUrl


def roots_result(roots: list[Path]) -> ListRootsResult:
    """构造工具链 resolver 注入形态的 roots 结果。"""
    return ListRootsResult(
        roots=[Root(uri=cast(FileUrl, root.as_uri()), name=root.name) for root in roots]
    )


class FakeSession:
    """以固定根目录应答 list_roots 的会话替身。"""

    def __init__(self, roots: list[Path]) -> None:
        self._roots = roots

    async def list_roots(self) -> ListRootsResult:
        return roots_result(self._roots)


class CapabilityDeclaringSession(FakeSession):
    """带 capability 探测的会话替身：check_client_capability 返回固定声明结果。"""

    def __init__(self, roots: list[Path], declared: bool) -> None:
        super().__init__(roots)
        self.declared = declared
        self.capability_probes = 0
        self.list_roots_calls = 0

    def check_client_capability(self, capability: object) -> bool:
        self.capability_probes += 1
        return self.declared

    async def list_roots(self) -> ListRootsResult:
        self.list_roots_calls += 1
        return await super().list_roots()


class ProbingErrorSession(CapabilityDeclaringSession):
    """capability 探测即抛异常的会话替身。"""

    def __init__(self, roots: list[Path]) -> None:
        super().__init__(roots, declared=True)

    def check_client_capability(self, capability: object) -> bool:
        raise RuntimeError("capability probe broken")
