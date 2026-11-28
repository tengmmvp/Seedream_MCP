"""终端用户标识解析测试：开关与模型门控、显式配置透传、系统派生回退、输入拼接规格与派生失败兜底。"""

from __future__ import annotations

import hashlib
import re

import pytest

from seedream_mcp.utils.core.safety import (
    _DERIVED_IDENTIFIER_LENGTH,
    derive_safety_identifier,
    resolve_safety_identifier,
)

from _lib.identity_fakes import stub_identity_probes
from _lib.log_fakes import capture_loguru_messages

# 5.5 家族声明 supports_safety_identifier=True，5.0 家族声明 False。
_SUPPORTED_MODEL_ID = "doubao-seedream-5-5-261028"
_UNSUPPORTED_MODEL_ID = "doubao-seedream-5-0-260128"

# 非 UTF-8 原始字节的机器名：OS 层经 surrogateescape 解码得到含代理字符的字符串。
_SURROGATE_IDENTITY_RAW = b"caf\xe9-box\nuser"
_SURROGATE_HOST = b"caf\xe9-box".decode("utf-8", errors="surrogateescape")


def _expected_identifier(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:_DERIVED_IDENTIFIER_LENGTH]


def test_resolve_disabled_attaches_none_even_with_explicit_identifier() -> None:
    """开关关闭时即使模型支持且配置了显式标识也不附加，来源为 disabled。"""
    decision = resolve_safety_identifier(
        enabled=False, model_id=_SUPPORTED_MODEL_ID, explicit_identifier="deploy-a"
    )

    assert decision.source == "disabled"
    assert decision.value is None


def test_resolve_disabled_takes_precedence_over_unsupported_model() -> None:
    """开关关闭与模型不支持同时成立时取 disabled，锁定开关先于模型门控裁决。"""
    decision = resolve_safety_identifier(
        enabled=False, model_id=_UNSUPPORTED_MODEL_ID, explicit_identifier="deploy-a"
    )

    assert decision.source == "disabled"
    assert decision.value is None


def test_resolve_unsupported_model_attaches_none_without_identity_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型不支持时不附加且不探测 OS 身份，即使开关开启且未显式配置。"""
    probes = stub_identity_probes(monkeypatch, host="", user="")

    decision = resolve_safety_identifier(enabled=True, model_id=_UNSUPPORTED_MODEL_ID)

    assert decision.source == "unsupported"
    assert decision.value is None
    assert probes == []


def test_resolve_enabled_prefers_explicit_identifier_verbatim() -> None:
    """显式配置直接透传、不经哈希，便于部署方与上游排障沟通，来源为 explicit。"""
    decision = resolve_safety_identifier(
        enabled=True, model_id=_SUPPORTED_MODEL_ID, explicit_identifier="deploy-a"
    )

    assert decision.source == "explicit"
    assert decision.value == "deploy-a"


def test_resolve_enabled_without_explicit_falls_back_to_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置显式值时回退系统派生，派生值为十六进制前缀且进程内稳定，来源为 derived。"""
    stub_identity_probes(monkeypatch, host="spec-host", user="spec-user")

    decision = resolve_safety_identifier(enabled=True, model_id=_SUPPORTED_MODEL_ID)

    assert decision.source == "derived"
    assert decision.value == _expected_identifier(b"spec-host\nspec-user")
    assert decision.value is not None
    assert re.fullmatch(r"[0-9a-f]+", decision.value) is not None
    assert len(decision.value) == _DERIVED_IDENTIFIER_LENGTH


def test_derived_identifier_hashes_host_and_user_concatenation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """派生输入为机器名与用户名以换行符拼接的 UTF-8 字节，期望哈希按同一拼接规则在测试内显式计算，实现改拼接即红。"""
    probes = stub_identity_probes(monkeypatch, host="spec-host", user="spec-user")

    expected = _expected_identifier(b"spec-host\nspec-user")

    assert derive_safety_identifier() == expected
    assert derive_safety_identifier() == expected
    assert probes == ["node", "getuser"]


def test_derived_identifier_hashes_surrogate_host_by_original_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """机器名经 surrogateescape 解码含代理字符时按原始字节哈希，派生不因编码失败中断。"""
    stub_identity_probes(monkeypatch, host=_SURROGATE_HOST, user="user")

    assert derive_safety_identifier() == _expected_identifier(_SURROGATE_IDENTITY_RAW)


def test_derived_identifier_keeps_user_when_host_probe_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """platform.node 抛 OSError 时机器名取占位、用户名保留真实值，并记录仅含「机器名」的告警。"""
    stub_identity_probes(monkeypatch, host=OSError("uname failed"), user="spec-user")
    messages: list[str] = []

    with capture_loguru_messages(messages, level="WARNING"):
        derived = derive_safety_identifier()

    assert derived == _expected_identifier(b"unknown-host\nspec-user")
    assert len(messages) == 1
    assert "机器名" in messages[0]
    assert "用户名" not in messages[0]


def test_derived_identifier_keeps_host_when_user_probe_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """getpass.getuser 抛 OSError 时用户名取占位、机器名保留真实值，并记录仅含「用户名」的告警。"""
    stub_identity_probes(monkeypatch, host="spec-host", user=OSError("no passwd entry"))
    messages: list[str] = []

    with capture_loguru_messages(messages, level="WARNING"):
        derived = derive_safety_identifier()

    assert derived == _expected_identifier(b"spec-host\nunknown-user")
    assert len(messages) == 1
    assert "用户名" in messages[0]
    assert "机器名" not in messages[0]


def test_derived_identifier_falls_back_to_placeholder_on_lone_surrogate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """身份含 U+DC80–U+DCFF 以外的孤立代理字符时整体退回占位值并告警，不抛异常。"""
    stub_identity_probes(monkeypatch, host="lone\ud800-host", user="spec-user")
    messages: list[str] = []

    with capture_loguru_messages(messages, level="WARNING"):
        derived = derive_safety_identifier()

    assert derived == _expected_identifier(b"unknown-host\nunknown-user")
    assert len(messages) == 1
    assert "safety_identifier 派生未取得机器名与用户名" in messages[0]


def test_derived_identifier_missing_identity_falls_back_to_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """机器名或用户名取不到时以占位值参与拼接，占位符改动即红。"""
    stub_identity_probes(monkeypatch, host="", user="")

    assert derive_safety_identifier() == _expected_identifier(b"unknown-host\nunknown-user")
