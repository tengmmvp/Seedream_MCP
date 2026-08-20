"""runners 到 tools/core/auto_save 的端到端测试。

mock SeedreamClient 返回含 url 的结果，经 run_text_to_image 走完整生成流水线，
产出含 auto_save 字段的 CallToolResult；另测自动保存抛错时降级为保留原始 url。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.types import TextContent

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.schemas import (
    ImageToImageInput,
    MultiImageFusionInput,
    ResponseFormat,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.tools.runners import (
    run_image_to_image,
    run_multi_image_fusion,
    run_sequential_generation,
    run_text_to_image,
)
from seedream_mcp.utils.io import io_save

GENERATED_URL = "https://example.com/generated.png"


def _client_result() -> dict[str, Any]:
    return {
        "success": True,
        "data": [{"url": GENERATED_URL}],
        "usage": {"generated_images": 1},
        "status": "completed",
    }


def _patch_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client_cls = SeedreamClient

    async def fake_text_to_image(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        return _client_result()

    monkeypatch.setattr(client_cls, "text_to_image", fake_text_to_image)


def _patch_save_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_save_multiple(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, tool_name
        return [
            io_save.AutoSaveResult(
                success=True,
                original_url=images[0]["url"],
                local_path="/saved/generated.png",
                markdown_ref="![image](generated.png)",
            )
        ]

    monkeypatch.setattr(io_save.AutoSaveManager, "save_multiple_images", fake_save_multiple)


def _patch_save_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_save_multiple(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, images, tool_name
        raise RuntimeError("下载失败")

    monkeypatch.setattr(io_save.AutoSaveManager, "save_multiple_images", failing_save_multiple)


def _patch_client_method(monkeypatch: pytest.MonkeyPatch, method_name: str) -> list[dict[str, Any]]:
    """monkeypatch SeedreamClient 的指定生成方法返回标准成功结果。

    Returns:
        被 patch 方法每次调用的关键字参数记录，供分发断言使用。
    """
    calls: list[dict[str, Any]] = []

    async def fake_method(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self
        calls.append(kwargs)
        return _client_result()

    monkeypatch.setattr(SeedreamClient, method_name, fake_method)
    return calls


async def test_run_text_to_image_includes_auto_save_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """生成成功且自动保存成功时，结果含 auto_save 字段与本地路径。"""
    _patch_client_success(monkeypatch)
    _patch_save_success(monkeypatch)

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    params = TextToImageInput(prompt="a cat")

    result = await run_text_to_image(params, config, ctx=None)

    assert result.is_error is False
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["auto_save"]["enabled"] is True
    save_results = structured["auto_save"]["results"]
    assert len(save_results) == 1
    assert save_results[0]["local_path"] == "/saved/generated.png"
    data = structured["data"]
    assert data[0]["url"] == GENERATED_URL
    assert data[0]["local_path"] == "/saved/generated.png"


async def test_run_text_to_image_b64_json_auto_save_branch_collects_and_backfills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """response_format=b64_json 时按 b64_json 收集并按原始索引回填，失败占位项不进保存队列。"""
    client_cls = SeedreamClient

    async def fake_text_to_image_b64(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        return {
            "success": True,
            "data": [
                {
                    "type": "image_generation.partial_failed",
                    "image_index": 1,
                    "error": {"code": "blocked", "message": "blocked"},
                },
                {
                    "type": "image_generation.completed",
                    "image_index": 2,
                    "b64_json": "QUJD",
                },
            ],
            "usage": {"generated_images": 1},
            "status": "partial",
        }

    captured: dict[str, Any] = {}

    async def fake_save_multiple_base64(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        captured["images"] = images
        captured["tool_name"] = tool_name
        return [
            io_save.AutoSaveResult(
                success=True,
                original_url="base64",
                local_path="/saved/decoded.png",
                markdown_ref="![Generated Image](decoded.png)",
            )
        ]

    monkeypatch.setattr(client_cls, "text_to_image", fake_text_to_image_b64)
    monkeypatch.setattr(
        io_save.AutoSaveManager,
        "save_multiple_base64_images",
        fake_save_multiple_base64,
    )

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    params = TextToImageInput(prompt="a cat", response_format=ResponseFormat.B64_JSON)

    result = await run_text_to_image(params, config, ctx=None)

    assert result.is_error is False
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["response_format"] == "b64_json"
    # 收集阶段只纳入携带 b64_json 的条目，失败占位项不进入保存队列。
    assert captured["images"] == [
        {
            "b64_json": "QUJD",
            "prompt": "a cat",
            "custom_name": None,
            "alt_text": "Generated image 1",
        }
    ]
    assert captured["tool_name"] == "text_to_image"
    # auto_save.results 回填保存结果。
    save_results = structured["auto_save"]["results"]
    assert len(save_results) == 1
    assert save_results[0]["local_path"] == "/saved/decoded.png"
    # data 按收集阶段记录的原始索引对位写回，失败占位项不获得本地路径。
    data = structured["data"]
    assert "local_path" not in data[0]
    assert data[1]["local_path"] == "/saved/decoded.png"
    assert data[1]["markdown_ref"] == "![Generated Image](decoded.png)"
    response_text = next(
        content.text for content in result.content if isinstance(content, TextContent)
    )
    assert "自动保存: 1/1 成功" in response_text
    assert "  Base64 数据: 4 字符" in response_text


async def test_run_text_to_image_degrades_when_auto_save_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """自动保存阶段抛错时降级：结果仍 success、保留原始 url、记录 error。"""
    _patch_client_success(monkeypatch)
    _patch_save_failure(monkeypatch)

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    params = TextToImageInput(prompt="a cat")

    result = await run_text_to_image(params, config, ctx=None)

    assert result.is_error is False
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["auto_save"]["enabled"] is True
    assert structured["auto_save"]["results"] == []
    assert structured["auto_save"]["error"] is not None
    assert "下载失败" in structured["auto_save"]["error"]
    data = structured["data"]
    assert data[0]["url"] == GENERATED_URL
    assert "local_path" not in data[0]


async def test_run_text_to_image_rejects_out_of_bounds_save_path_before_api_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """越界 save_path 在校验阶段失败：client 生成方法不被调用，请求不计费执行。

    旧行为：越界路径在自动保存阶段才抛异常并降级为软警告，请求已计费执行。
    """
    client_cls = SeedreamClient
    calls: list[dict[str, Any]] = []

    async def fake_text_to_image(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self
        calls.append(kwargs)
        return _client_result()

    monkeypatch.setattr(client_cls, "text_to_image", fake_text_to_image)

    base = tmp_path / "save_root"
    base.mkdir()
    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(base))
    params = TextToImageInput(prompt="a cat", save_path="../../outside")

    result = await run_text_to_image(params, config, ctx=None)

    assert result.is_error is True
    assert calls == [], "越界 save_path 须在 client 生成方法调用前被拒绝"
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["error"]["type"] == "validation_error"
    assert "超出允许范围" in structured["error"]["message"]


async def test_run_image_to_image_dispatches_via_composition_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_image_to_image 经 composition root 委托 handle_image_to_image。

    最终调用 client.image_to_image，错分发时被 patch 方法零调用即时显式失败。
    """
    calls = _patch_client_method(monkeypatch, "image_to_image")
    _patch_save_success(monkeypatch)

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    params = ImageToImageInput(prompt="edit", image="https://example.com/ref.png")

    result = await run_image_to_image(params, config, ctx=None)

    assert len(calls) == 1, "生成请求未分发到被 patch 的 client.image_to_image"
    assert result.is_error is False
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["data"][0]["url"] == GENERATED_URL


async def test_run_multi_image_fusion_dispatches_via_composition_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_multi_image_fusion 经 composition root 委托 handle_multi_image_fusion。

    最终调用 client.multi_image_fusion，错分发时被 patch 方法零调用即时显式失败。
    """
    calls = _patch_client_method(monkeypatch, "multi_image_fusion")
    _patch_save_success(monkeypatch)

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    params = MultiImageFusionInput(
        prompt="fuse",
        image=["https://example.com/a.png", "https://example.com/b.png"],
    )

    result = await run_multi_image_fusion(params, config, ctx=None)

    assert len(calls) == 1, "生成请求未分发到被 patch 的 client.multi_image_fusion"
    assert result.is_error is False
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["data"][0]["url"] == GENERATED_URL


async def test_run_sequential_generation_dispatches_via_composition_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_sequential_generation 经 composition root 委托 handle_sequential_generation。

    最终调用 client.sequential_generation，错分发时被 patch 方法零调用即时显式失败。
    """
    calls = _patch_client_method(monkeypatch, "sequential_generation")
    _patch_save_success(monkeypatch)

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    params = SequentialGenerationInput(prompt="sequence", max_images=2)

    result = await run_sequential_generation(params, config, ctx=None)

    assert len(calls) == 1, "生成请求未分发到被 patch 的 client.sequential_generation"
    assert result.is_error is False
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["data"][0]["url"] == GENERATED_URL
