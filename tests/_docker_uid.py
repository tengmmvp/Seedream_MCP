"""Docker 挂载属主契约的 uid 单一事实源。

uid 只在 Dockerfile 的 useradd --uid 声明一处落值，属主契约相关测试的期望串
经此模块组装，改 uid 时测试无需逐处同步字面量。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def mount_uid() -> str:
    """提取 Dockerfile useradd --uid 声明的容器用户 uid。"""
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    declaration = re.search(r"useradd --uid (\d+)", dockerfile)
    assert declaration is not None, "Dockerfile 必须经 useradd --uid 显式声明容器用户"
    return declaration.group(1)


def chown_owner_pair() -> str:
    """按 mount_uid 组装 chown 命令的属主对。"""
    uid = mount_uid()
    return f"{uid}:{uid}"
