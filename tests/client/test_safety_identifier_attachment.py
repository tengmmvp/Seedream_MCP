"""safety_identifier 自动附加的请求体级测试。

开关解析、模型门控与显式配置优先已有函数级覆盖，
此处经 _send_request 替身捕获实际发出的请求体并断言其 JSON 内容。
网络层经替身注入，不触达真实 API。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

import pytest

from _lib.client_fakes import _make_send_request_fake
from _lib.identity_fakes import stub_identity_probes
from _lib.log_fakes import capture_loguru_messages

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.safety import derive_safety_identifier

# 5.5 家族声明 supports_safety_identifier=True，5.0 家族声明 False。
_SUPPORTED_MODEL_ID = "doubao-seedream-5-5-261028"
_UNSUPPORTED_MODEL_ID = "doubao-seedream-5-0-260128"

# 非 UTF-8 原始字节的机器名：OS 层经 surrogateescape 解码得到含代理字符的字符串。
_SURROGATE_IDENTITY_RAW = b"caf\xe9-box\nuser"
_SURROGATE_HOST = b"caf\xe9-box".decode("utf-8", errors="surrogateescape")


async def _sent_request_body(
    monkeypatch: pytest.MonkeyPatch, config: SeedreamConfig
) -> dict[str, Any]:
    """以替身捕获 text_to_image 实际发出的请求体并解析为 JSON。"""
    bodies: list[bytes] = []
    fake_send, _ = _make_send_request_fake(bodies=bodies)
    monkeypatch.setattr(SeedreamClient, "_send_request", fake_send)

    async with SeedreamClient(config) as client:
        await client.text_to_image(prompt="p")

    assert len(bodies) == 1
    return cast("dict[str, Any]", json.loads(bodies[0]))


async def test_supported_model_attaches_derived_identifier_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认开关且模型支持时，请求体携带系统派生标识。"""
    body = await _sent_request_body(
        monkeypatch, SeedreamConfig(api_key="k", model_id=_SUPPORTED_MODEL_ID)
    )

    assert body["safety_identifier"] == derive_safety_identifier()


async def test_explicit_identifier_takes_priority_over_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式配置的标识优先于系统派生值进入请求体。"""
    body = await _sent_request_body(
        monkeypatch,
        SeedreamConfig(api_key="k", model_id=_SUPPORTED_MODEL_ID, safety_identifier="deploy-a"),
    )

    assert body["safety_identifier"] == "deploy-a"


async def test_unsupported_model_omits_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """能力表不支持的家族不附加，即使开关开启且显式配置了标识。"""
    body = await _sent_request_body(
        monkeypatch,
        SeedreamConfig(api_key="k", model_id=_UNSUPPORTED_MODEL_ID, safety_identifier="deploy-a"),
    )

    assert "safety_identifier" not in body


async def test_disabled_switch_omits_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开关关闭时不附加，即使模型支持且显式配置了标识。"""
    body = await _sent_request_body(
        monkeypatch,
        SeedreamConfig(
            api_key="k",
            model_id=_SUPPORTED_MODEL_ID,
            safety_identifier_enabled=False,
            safety_identifier="deploy-a",
        ),
    )

    assert "safety_identifier" not in body


async def test_surrogate_host_identifier_is_sent_by_original_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """机器名含 surrogateescape 代理字符时请求照常发出，标识按原始字节派生。"""
    stub_identity_probes(monkeypatch, host=_SURROGATE_HOST, user="user")

    body = await _sent_request_body(
        monkeypatch, SeedreamConfig(api_key="k", model_id=_SUPPORTED_MODEL_ID)
    )

    expected = hashlib.sha256(_SURROGATE_IDENTITY_RAW).hexdigest()[:32]
    assert body["safety_identifier"] == expected


async def test_unsupported_model_generates_without_identity_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不支持的模型生成成功，身份探测零调用且无派生告警。"""
    probes = stub_identity_probes(monkeypatch, host="", user="")
    messages: list[str] = []

    with capture_loguru_messages(messages, level="WARNING"):
        body = await _sent_request_body(
            monkeypatch, SeedreamConfig(api_key="k", model_id=_UNSUPPORTED_MODEL_ID)
        )

    assert "safety_identifier" not in body
    assert probes == []
    assert not [message for message in messages if "safety_identifier" in message]
