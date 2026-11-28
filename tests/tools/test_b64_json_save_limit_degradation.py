"""b64_json 图片超过自动保存文件上限时的交付守护。

auto_save_max_file_size 只约束落盘：超限图片保存失败但不影响已计费响应的交付，
structuredContent 仍携带完整 b64_json。响应经 MockTransport 驱动真实客户端读取，
读体上限一旦与保存上限耦合，该响应会以「响应体过大」被整体拒绝。
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import httpx
import pytest
from mcp.types import TextContent
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.tools.runners import run_text_to_image
from seedream_mcp.tools.schemas import ResponseFormat, TextToImageInput

# 保存上限取 1KiB，载荷取约 2MiB 的不可压缩 PNG：
# 读体上限若按保存上限推导仅约 9.5KB，装不下该载荷的 base64 展开。
_MAX_FILE_SIZE = 1024
_PAYLOAD_SIDE = 724


def _incompressible_png_b64() -> str:
    """生成随机噪声 RGBA PNG 并返回其 base64 文本。"""
    image = Image.frombytes(
        "RGBA", (_PAYLOAD_SIDE, _PAYLOAD_SIDE), os.urandom(_PAYLOAD_SIDE**2 * 4)
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _patch_mock_transport(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    """客户端首次建连时装配返回给定载荷的 MockTransport。"""

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    async def _ensure_mock_client(self: SeedreamClient) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    monkeypatch.setattr(SeedreamClient, "_ensure_client", _ensure_mock_client)


async def test_b64_json_over_max_file_size_keeps_payload_and_reports_save_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """b64_json 图片超过 auto_save_max_file_size 时结构化结果保留完整载荷，保存项 success=False。"""
    b64_text = _incompressible_png_b64()
    body = json.dumps({"data": [{"b64_json": b64_text}], "usage": {}, "status": "completed"})
    _patch_mock_transport(monkeypatch, body.encode())
    config = SeedreamConfig(
        api_key="test_key", data_root=str(tmp_path), auto_save_max_file_size=_MAX_FILE_SIZE
    )
    set_active_config(config)

    result = await run_text_to_image(
        TextToImageInput(prompt="a cat", response_format=ResponseFormat.B64_JSON), config, ctx=None
    )

    assert result.is_error is False
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["success"] is True
    assert structured["data"][0]["b64_json"] == b64_text
    assert "local_path" not in structured["data"][0]
    save_results = structured["auto_save"]["results"]
    assert len(save_results) == 1
    assert save_results[0]["success"] is False
    assert "过大" in save_results[0]["error"]
    assert not list(tmp_path.rglob("*.png"))
    response_text = next(
        content.text for content in result.content if isinstance(content, TextContent)
    )
    assert "自动保存: 0/1 成功" in response_text
