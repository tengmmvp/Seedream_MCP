"""终端用户标识解析测试：开关门控、显式配置透传与系统派生回退及其输入拼接规格。"""

from __future__ import annotations

import getpass
import hashlib
import platform
import re

import pytest

from seedream_mcp.utils.core import safety
from seedream_mcp.utils.core.safety import (
    _DERIVED_IDENTIFIER_LENGTH,
    derive_safety_identifier,
    resolve_safety_identifier,
)


def test_resolve_disabled_attaches_none_even_with_explicit_identifier() -> None:
    """开关关闭时即使配置了显式标识也不附加。"""
    assert resolve_safety_identifier(enabled=False, explicit_identifier="deploy-a") is None


def test_resolve_enabled_prefers_explicit_identifier_verbatim() -> None:
    """显式配置直接透传、不经哈希，便于部署方与上游排障沟通。"""
    resolved = resolve_safety_identifier(enabled=True, explicit_identifier="deploy-a")

    assert resolved == "deploy-a"


def test_resolve_enabled_without_explicit_falls_back_to_derived() -> None:
    """未配置显式值时回退系统派生，派生值为十六进制前缀且进程内稳定。"""
    resolved = resolve_safety_identifier(enabled=True)

    assert resolved == derive_safety_identifier()
    assert re.fullmatch(r"[0-9a-f]+", resolved) is not None
    assert len(resolved) == _DERIVED_IDENTIFIER_LENGTH


def test_derived_identifier_hashes_host_and_user_concatenation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """派生输入为机器名与用户名以换行符拼接的 UTF-8 字节，期望哈希按同一拼接规则在测试内显式计算，实现改拼接即红。"""
    monkeypatch.setattr(safety, "_derived_cache", None)
    monkeypatch.setattr(platform, "node", lambda: "spec-host")
    monkeypatch.setattr(getpass, "getuser", lambda: "spec-user")

    expected = hashlib.sha256("spec-host\nspec-user".encode("utf-8")).hexdigest()[
        :_DERIVED_IDENTIFIER_LENGTH
    ]

    assert derive_safety_identifier() == expected
    assert resolve_safety_identifier(enabled=True) == expected


def test_derived_identifier_missing_identity_falls_back_to_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """机器名或用户名取不到时以占位值参与拼接，占位符改动即红。"""
    monkeypatch.setattr(safety, "_derived_cache", None)
    monkeypatch.setattr(platform, "node", lambda: "")
    monkeypatch.setattr(getpass, "getuser", lambda: "")

    expected = hashlib.sha256(b"unknown-host\nunknown-user").hexdigest()[
        :_DERIVED_IDENTIFIER_LENGTH
    ]

    assert derive_safety_identifier() == expected
