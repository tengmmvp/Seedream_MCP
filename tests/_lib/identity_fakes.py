"""safety_identifier 派生的 OS 身份探测共享替身，清空进程级派生缓存保证用例隔离。"""

from __future__ import annotations

from collections.abc import Callable

import getpass
import platform

import pytest

from seedream_mcp.utils.core import safety


def stub_identity_probes(
    monkeypatch: pytest.MonkeyPatch, host: str | Exception, user: str | Exception
) -> list[str]:
    """以计数替身替换 platform.node 与 getpass.getuser 并清空派生缓存，返回逐次调用记录。

    host 与 user 为各自探测的返回值，传入异常实例则该探测每次调用都抛出它。
    返回的调用记录按调用顺序登记 "node" 与 "getuser"，供断言探测次数与是否发生。
    """
    probes: list[str] = []

    def _counting_probe(name: str, outcome: str | Exception) -> Callable[[], str]:
        def _probe() -> str:
            probes.append(name)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return _probe

    monkeypatch.setattr(safety, "_derived_cache", None)
    monkeypatch.setattr(platform, "node", _counting_probe("node", host))
    monkeypatch.setattr(getpass, "getuser", _counting_probe("getuser", user))
    return probes
