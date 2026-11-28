"""cli_main 启动日志的 safety_identifier 来源标签四态与标识值不落日志。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest

import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core import safety
from seedream_mcp.utils.core.safety import _DERIVED_IDENTIFIER_LENGTH

from _lib.cli_fakes import make_run_args, stub_cli_main
from _lib.identity_fakes import stub_identity_probes
from _lib.log_fakes import capture_loguru_messages

# 替身身份派生的标识与显式配置值都是不得出现在启动日志中的明文。
_STUB_HOST = "startup-host"
_STUB_USER = "startup-user"
_EXPLICIT_IDENTIFIER = "deploy-startup-secret"
_STUB_DERIVED_IDENTIFIER = hashlib.sha256(
    f"{_STUB_HOST}\n{_STUB_USER}".encode("utf-8")
).hexdigest()[:_DERIVED_IDENTIFIER_LENGTH]


def _startup_effective_line(monkeypatch: pytest.MonkeyPatch, config: SeedreamConfig) -> str:
    """以替身跑完 cli_main，返回唯一的「生成链路生效值」日志行。"""
    stub_cli_main(monkeypatch, make_run_args("stdio"), config)
    messages: list[str] = []

    with capture_loguru_messages(messages, level="INFO"):
        assert server.cli_main() == 0

    effective_lines = [message for message in messages if "生成链路生效值" in message]
    assert len(effective_lines) == 1
    return effective_lines[0]


@pytest.mark.parametrize(
    ("config_kwargs", "expected_source", "expected_probes"),
    [
        ({}, "derived", ["node", "getuser"]),
        ({"model_id": "ep-20260101-test"}, "unsupported", []),
        ({"safety_identifier_enabled": False}, "disabled", []),
        ({"safety_identifier": _EXPLICIT_IDENTIFIER}, "explicit", []),
    ],
    ids=["derived", "unsupported", "disabled", "explicit"],
)
def test_startup_logs_safety_identifier_source_without_value(
    monkeypatch: pytest.MonkeyPatch,
    config_kwargs: dict[str, Any],
    expected_source: str,
    expected_probes: list[str],
) -> None:
    """启动「生成链路生效值」行按配置落 safety_identifier 来源标签且不含标识值，身份探测仅在派生时发生。"""
    probes = stub_identity_probes(monkeypatch, host=_STUB_HOST, user=_STUB_USER)

    line = _startup_effective_line(monkeypatch, SeedreamConfig(api_key="k", **config_kwargs))

    assert re.search(rf"\bsafety_identifier={expected_source}\b", line)
    assert probes == expected_probes
    assert _STUB_DERIVED_IDENTIFIER not in line
    assert _EXPLICIT_IDENTIFIER not in line


def test_startup_resolution_preheats_derived_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """启动期派生后请求路径取缓存，不再探测 OS 身份。"""
    probes = stub_identity_probes(monkeypatch, host=_STUB_HOST, user=_STUB_USER)
    _startup_effective_line(monkeypatch, SeedreamConfig(api_key="k"))
    assert probes == ["node", "getuser"]

    assert safety.derive_safety_identifier() == _STUB_DERIVED_IDENTIFIER
    assert probes == ["node", "getuser"]
