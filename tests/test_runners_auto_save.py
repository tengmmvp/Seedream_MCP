"""runners → tools/core/auto_save 端到端测试。

通过 run_text_to_image 验证完整生成流水线：mock SeedreamClient 返回含 url 的结果，
经 execute_generation_handler 触发 auto_save_from_urls，最终产出含 auto_save 字段的
CallToolResult；另测自动保存阶段抛错时降级——结果仍为 success 且保留原始 url。

客户端与自动保存管理器类在调用时经 importlib 动态获取，避免 test_package_lazy_import
重载 seedream_mcp.client 后留下指向旧类对象的失效引用，导致 monkeypatch 失效。
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from seedream_mcp.tools.core.schemas import TextToImageInput
from seedream_mcp.tools.runners import run_text_to_image

GENERATED_URL = "https://example.com/generated.png"


def _client_result() -> dict[str, Any]:
    return {
        "success": True,
        "data": [{"url": GENERATED_URL}],
        "usage": {"generated_images": 1},
        "status": "completed",
    }


def _patch_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client_cls = importlib.import_module("seedream_mcp.client").SeedreamClient

    async def fake_text_to_image(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        return _client_result()

    monkeypatch.setattr(client_cls, "text_to_image", fake_text_to_image)


def _patch_save_success(monkeypatch: pytest.MonkeyPatch) -> None:
    auto_save_module = importlib.import_module("seedream_mcp.utils.auto_save")
    mgr_cls = auto_save_module.AutoSaveManager
    result_cls = auto_save_module.AutoSaveResult

    async def fake_save_multiple(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, tool_name
        return [
            result_cls(
                success=True,
                original_url=images[0]["url"],
                local_path="/saved/generated.png",
                markdown_ref="![image](generated.png)",
            )
        ]

    monkeypatch.setattr(mgr_cls, "save_multiple_images", fake_save_multiple)


def _patch_save_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr_cls = importlib.import_module("seedream_mcp.utils.auto_save").AutoSaveManager

    async def failing_save_multiple(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, images, tool_name
        raise RuntimeError("下载失败")

    monkeypatch.setattr(mgr_cls, "save_multiple_images", failing_save_multiple)


@pytest.mark.asyncio
async def test_run_text_to_image_includes_auto_save_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """生成成功且自动保存成功时，结果含 auto_save 字段与本地路径。"""
    _patch_client_success(monkeypatch)
    _patch_save_success(monkeypatch)

    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    params = TextToImageInput(prompt="a cat")

    result = await run_text_to_image(params, config, ctx=None)

    assert result.isError is False
    structured = result.structuredContent
    assert isinstance(structured, dict)
    assert structured["auto_save"]["enabled"] is True
    save_results = structured["auto_save"]["results"]
    assert len(save_results) == 1
    assert save_results[0]["local_path"] == "/saved/generated.png"
    data = structured["data"]
    assert data[0]["url"] == GENERATED_URL
    assert data[0]["local_path"] == "/saved/generated.png"


@pytest.mark.asyncio
async def test_run_text_to_image_degrades_when_auto_save_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """自动保存阶段抛错时降级：结果仍 success、保留原始 url、记录 error。"""
    _patch_client_success(monkeypatch)
    _patch_save_failure(monkeypatch)

    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    params = TextToImageInput(prompt="a cat")

    result = await run_text_to_image(params, config, ctx=None)

    assert result.isError is False
    structured = result.structuredContent
    assert isinstance(structured, dict)
    assert structured["auto_save"]["enabled"] is True
    assert structured["auto_save"]["results"] == []
    assert structured["auto_save"]["error"] is not None
    assert "下载失败" in structured["auto_save"]["error"]
    data = structured["data"]
    assert data[0]["url"] == GENERATED_URL
    assert "local_path" not in data[0]
