import asyncio
import json
import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.logging import setup_logging


class MockResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self._headers = headers or {}
        self._body = body
        self.text = body.decode("utf-8", errors="ignore")

    @property
    def headers(self):
        return self._headers

    async def aread(self):
        return self._body

    def json(self):
        return json.loads(self._body.decode("utf-8"))


class MockStream:
    def __init__(self, response: MockResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MockClient:
    def __init__(self, response: MockResponse):
        self._response = response

    async def aclose(self):
        return None

    def stream(self, method, url, json=None, timeout=None):
        return MockStream(self._response)

    async def post(self, url, json=None, timeout=None):
        return self._response


@pytest.mark.asyncio
async def test_stream_flag_parsing_via_event_stream(monkeypatch):
    payload = {"data": [], "usage": {}}
    body = b"data: chunk\n\n" + json.dumps(payload).encode("utf-8")
    response = MockResponse(status_code=200, headers={"content-type": "text/event-stream"}, body=body)

    cfg = SeedreamConfig(api_key="test", model_id="doubao-seedream-4-5-251128")
    client = SeedreamClient(cfg)

    async def fake_ensure(self):
        self._client = MockClient(response)

    monkeypatch.setattr(SeedreamClient, "_ensure_client", fake_ensure)

    result = await client.text_to_image("a", stream=True)
    assert result["success"] is True
    assert "data" in result


def test_setup_logging_error_level():
    setup_logging("ERROR")
    import logging as _logging
    assert _logging.getLogger("urllib3").level == _logging.WARNING

