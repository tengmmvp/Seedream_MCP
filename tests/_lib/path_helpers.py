"""路径形态共享辅助：resolve 替身与词法违约形态表。"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple

import pytest


def abs_path_stub(path: str) -> str:
    """resolve 为当前平台绝对形态的替身路径，过 AutoSaveResult 的 local_path 构造门。"""
    return str(Path(path).resolve())


class FormViolationCase(NamedTuple):
    """背离已 resolve 绝对形态的违约条目，win32_only 条目仅 win32 判违约。"""

    id: str
    value_factory: Callable[[Path], str]
    reason: str
    win32_only: bool = False


# reason 短语与 path.resolved_form_violation 返回值逐字一致；冒号与保留名为
# win32 专属违约项，POSIX 为合法文件名字符。
FORM_VIOLATION_CASES: tuple[FormViolationCase, ...] = (
    FormViolationCase("relative", lambda tmp_path: "dir/../a.png", "非绝对路径"),
    FormViolationCase(
        "dotdot", lambda tmp_path: str(tmp_path / "a" / ".." / "b.png"), "含 .. 分量"
    ),
    FormViolationCase("nul", lambda tmp_path: str(tmp_path / "a\x00.png"), "段含 NUL 字符"),
    FormViolationCase(
        "ads-colon",
        lambda tmp_path: str(tmp_path / "a.png:hidden"),
        "段含冒号",
        win32_only=True,
    ),
    FormViolationCase(
        "reserved-name",
        lambda tmp_path: str(tmp_path / "CON.png"),
        "段为 Windows 保留名",
        win32_only=True,
    ),
)


def form_violation_params(
    cases: Iterable[FormViolationCase] = FORM_VIOLATION_CASES,
) -> list[Any]:
    """违约表转 parametrize 的 (value_factory, reason) 参数行，win32 专属条目带 skipif。"""
    skip_non_win32 = pytest.mark.skipif(
        sys.platform != "win32", reason="冒号与保留名违约仅 win32 生效"
    )
    return [
        pytest.param(
            case.value_factory,
            case.reason,
            id=case.id,
            marks=(skip_non_win32,) if case.win32_only else (),
        )
        for case in cases
    ]
