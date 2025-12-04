import asyncio
import json
import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig


class FakeResponse:
    def __init__(self, chunks, headers):
        self.status_code = 200
        self._chunks = chunks
        self.headers = headers

    async def aread(self):
        return b"".join(self._chunks)

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class FakeStreamCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeClient:
    def __init__(self, response):
        self._response = response

    def stream(self, method, url, json=None, timeout=None):
        return FakeStreamCM(self._response)

    async def post(self, url, json=None, timeout=None):
        return None


@pytest.mark.asyncio
async def test_stream_sse_parsing():
    cfg = SeedreamConfig(api_key="dummy_key")
    client = SeedreamClient(cfg)

    evt1 = {
        "type": "image_generation.partial_succeeded",
        "model": "doubao-seedream-4-5-251128",
        "created": 1700000000,
        "image_index": 0,
        "url": "https://example.com/a.png",
        "size": "2048×2048",
    }
    evt2 = {
        "type": "image_generation.partial_succeeded",
        "model": "doubao-seedream-4-5-251128",
        "created": 1700000001,
        "image_index": 1,
        "b64_json": "YmFzZTY0",
        "size": "2048×2048",
    }
    evt3 = {
        "type": "image_generation.completed",
        "model": "doubao-seedream-4-5-251128",
        "created": 1700000002,
        "usage": {
            "generated_images": 2,
            "output_tokens": 10,
            "total_tokens": 10,
        },
    }

    chunks = [
        ("data: " + json.dumps(evt1) + "\n\n").encode("utf-8"),
        ("data: " + json.dumps(evt2) + "\n\n").encode("utf-8"),
        ("data: " + json.dumps(evt3) + "\n\n").encode("utf-8"),
    ]
    headers = {"content-type": "text/event-stream"}

    fake_response = FakeResponse(chunks, headers)
    client._client = FakeClient(fake_response)

    result = await client._call_api("text_to_image", {"stream": True})

    assert result["success"] is True
    assert isinstance(result["data"], list)
    assert len(result["data"]) == 2
    assert result["data"][0]["url"] == "https://example.com/a.png"
    assert result["data"][1]["b64_json"] == "YmFzZTY0"
    assert result["usage"]["generated_images"] == 2
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_stream_sse_event_and_data_lines():
    cfg = SeedreamConfig(api_key="dummy_key")
    client = SeedreamClient(cfg)

    evt1 = {
        "type": "image_generation.partial_succeeded",
        "image_index": 0,
        "url": "https://example.com/a.png",
        "size": "2048×2048",
    }
    evt2 = {
        "type": "image_generation.completed",
        "usage": {
            "generated_images": 1,
            "output_tokens": 8,
            "total_tokens": 8,
        },
    }

    chunks = [
        ("event: image_generation.partial_succeeded\ndata: " + json.dumps(evt1) + "\n\n").encode("utf-8"),
        ("event: image_generation.completed\ndata: " + json.dumps(evt2) + "\n\n").encode("utf-8"),
    ]
    headers = {"content-type": "text/event-stream"}

    fake_response = FakeResponse(chunks, headers)
    client._client = FakeClient(fake_response)

    result = await client._call_api("text_to_image", {"stream": True})

    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["url"] == "https://example.com/a.png"
    assert result["usage"]["generated_images"] == 1
    assert result["status"] == "completed"
