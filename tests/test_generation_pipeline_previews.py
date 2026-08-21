"""生成流水线预览开关测试：include_previews 关闭态跳过缩略图预览装配。

Web 端点只消费 structuredContent，经 runner 的 include_previews=False 沿异步
上下文关闭预览装配；mock client 与自动保存驱动真实流水线，以 build_preview_contents
计数 spy 与 content 形态双重锁定关闭态，并守护作用域退出后的开关复位。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.types import ImageContent
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core import common as common_module
from seedream_mcp.tools.core.common import execute_generation_handler
from seedream_mcp.tools.core.schemas import TextToImageInput
from seedream_mcp.tools.impl.text_to_image import TEXT_TO_IMAGE
from seedream_mcp.tools.runners import run_text_to_image
from seedream_mcp.utils.core.logs import get_logger
from seedream_mcp.utils.io import io_save


def _patch_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock 客户端文生图成功，返回单图结果。"""

    async def fake_text_to_image(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        return {
            "success": True,
            "data": [{"url": "https://example.com/generated.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }

    monkeypatch.setattr(SeedreamClient, "text_to_image", fake_text_to_image)


def _patch_save_real_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """mock 单图保存成功且落盘真实 PNG，返回其路径供断言复用。"""
    saved = tmp_path / "saved.png"
    Image.new("RGB", (1200, 800), (200, 30, 30)).save(saved, format="PNG")
    result_cls = io_save.AutoSaveResult

    async def fake_save_image(self: Any, **kwargs: Any) -> Any:
        del self
        return result_cls(
            success=True,
            original_url=kwargs.get("url", ""),
            local_path=str(saved),
            markdown_ref="![image](saved.png)",
        )

    monkeypatch.setattr(io_save.AutoSaveManager, "save_image", fake_save_image)
    return saved


def _patch_preview_spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """以计数 spy 顶替 common 门面内的 build_preview_contents，记录每次调用的张数。"""
    calls: list[int] = []

    async def _counting_spy(paths: Any) -> list[ImageContent]:
        calls.append(len(paths))
        return []

    monkeypatch.setattr(common_module, "build_preview_contents", _counting_spy)
    return calls


async def test_runner_include_previews_false_skips_preview_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """include_previews=False 时不调用缩略图构建，content 不含 ImageContent。"""
    _patch_client_success(monkeypatch)
    _patch_save_real_file(monkeypatch, tmp_path)
    calls = _patch_preview_spy(monkeypatch)
    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))

    result = await run_text_to_image(
        TextToImageInput(prompt="a cat"), config, include_previews=False
    )

    assert result.is_error is False
    assert calls == []
    assert not any(isinstance(content, ImageContent) for content in result.content)
    # structuredContent 不受开关影响，保存信息仍完整。
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["data"][0]["local_path"].endswith("saved.png")


async def test_preview_scope_resets_after_runner_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关闭态调用结束后开关复位：同任务的后续默认调用照常装配预览。"""
    _patch_client_success(monkeypatch)
    _patch_save_real_file(monkeypatch, tmp_path)
    calls = _patch_preview_spy(monkeypatch)
    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))

    await run_text_to_image(TextToImageInput(prompt="a cat"), config, include_previews=False)
    await run_text_to_image(TextToImageInput(prompt="a cat"), config)

    assert calls == [1]


async def test_execute_handler_explicit_include_previews_false_skips_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """execute_generation_handler 显式 include_previews=False 同样跳过预览装配。"""
    _patch_client_success(monkeypatch)
    _patch_save_real_file(monkeypatch, tmp_path)
    calls = _patch_preview_spy(monkeypatch)
    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))

    async def _executor(client: Any, context: Any) -> dict[str, Any]:
        del client, context
        return {
            "success": True,
            "data": [{"url": "https://example.com/generated.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }

    result = await execute_generation_handler(
        params=TextToImageInput(prompt="a cat"),
        config=config,
        metadata=TEXT_TO_IMAGE,
        module_logger=get_logger(),
        request_executor=_executor,
        ctx=None,
        include_previews=False,
    )

    assert result.is_error is False
    assert calls == []
