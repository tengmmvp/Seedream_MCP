"""扁平错误信封跨层 parity 守护：共享信封常量与 transport、webapp 两侧消费面。

被测主面为 utils/core/json_envelope 的共享形态及其两侧消费的形态一致；按 N8
首主题词（json_envelope）映射归 utils 域。
"""

from __future__ import annotations

import json

from starlette.types import Message

from seedream_mcp.server import transport as transport_module
from seedream_mcp.utils.core.json_envelope import (
    SECURE_JSON_HEADER_BYTES,
    SECURE_JSON_HEADERS,
    flat_error_body,
)
from seedream_mcp.webapp import _responses

# 两侧消费面共用同一错误码与描述，供逐字节与逐头比对。
_PARITY_ERROR = "not_found"
_PARITY_DESCRIPTION = "接口不存在"


def test_secure_json_headers_snapshot() -> None:
    """共享安全头快照：值漂移（如退化为可缓存）须显式改此处。"""
    assert SECURE_JSON_HEADERS == {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
    }
    assert SECURE_JSON_HEADER_BYTES == tuple(
        (name.encode("ascii"), value.encode("ascii")) for name, value in SECURE_JSON_HEADERS.items()
    )


def test_flat_error_body_two_key_shape() -> None:
    """共享错误体恒为 error 与 error_description 两键，非 ASCII 文案按原文 UTF-8 上线。"""
    body = flat_error_body(_PARITY_ERROR, _PARITY_DESCRIPTION)

    assert json.loads(body) == {
        "error": _PARITY_ERROR,
        "error_description": _PARITY_DESCRIPTION,
    }
    assert _PARITY_DESCRIPTION.encode("utf-8") in body


async def _rejection_response(
    status: int, code: str, description: str
) -> tuple[dict[bytes, bytes], bytes]:
    """经 transport 拒绝发送点收集响应头与响应体。"""
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await transport_module._send_rejection(send, status, code, description)
    headers = dict(sent[0]["headers"])
    body_msg = next(m for m in sent if m.get("type") == "http.response.body")
    body: bytes = body_msg["body"]
    return headers, body


async def test_transport_rejection_matches_shared_envelope() -> None:
    """transport 拒绝响应的安全头与错误体逐项等于共享信封。"""
    headers, body = await _rejection_response(404, _PARITY_ERROR, _PARITY_DESCRIPTION)

    assert dict(SECURE_JSON_HEADER_BYTES).items() <= headers.items()
    assert body == flat_error_body(_PARITY_ERROR, _PARITY_DESCRIPTION)


def test_webapp_flat_error_json_matches_shared_envelope() -> None:
    """webapp 扁平信封响应的安全头与错误体逐项等于共享信封。"""
    response = _responses.flat_error_json(
        _responses.WebErrorInfo(_PARITY_ERROR, _PARITY_DESCRIPTION, 404)
    )

    assert response.status_code == 404
    for name, value in SECURE_JSON_HEADERS.items():
        assert response.headers[name] == value
    assert bytes(response.body) == flat_error_body(_PARITY_ERROR, _PARITY_DESCRIPTION)


async def test_envelope_parity_between_transport_and_webapp() -> None:
    """两侧信封错误体两键形态一致且键值相等。"""
    _transport_headers, transport_body = await _rejection_response(
        404, _PARITY_ERROR, _PARITY_DESCRIPTION
    )
    webapp_response = _responses.flat_error_json(
        _responses.WebErrorInfo(_PARITY_ERROR, _PARITY_DESCRIPTION, 404)
    )

    transport_payload = json.loads(transport_body)
    webapp_payload = json.loads(bytes(webapp_response.body))
    assert set(transport_payload) == set(webapp_payload) == {"error", "error_description"}
    assert transport_payload == webapp_payload
