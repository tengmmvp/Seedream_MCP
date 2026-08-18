"""生成工具结果的缩略图预览测试。

覆盖三层：image_thumbnail 的缩略图生成（尺寸收敛、透明通道白底、失败归 None 与
顺序保持）；execute_generation_handler 流水线按配置与保存结果装配 ImageContent；
SEEDREAM_PREVIEW_ENABLED 环境变量解析。集成用例经 run_text_to_image 触发，mock
client 与自动保存，自动保存返回真实落盘的 PNG 以驱动真实缩略图编码。
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from mcp.types import ImageContent, TextContent

from seedream_mcp.client import SeedreamClient
from seedream_mcp.tools.core.schemas import TextToImageInput
from seedream_mcp.tools.runners import run_text_to_image
from seedream_mcp.utils.images.image_thumbnail import (
    PREVIEW_MAX_IMAGES,
    THUMBNAIL_MAX_EDGE,
    build_preview_contents,
    build_thumbnail_bytes,
)
from seedream_mcp.utils.io import io_save


def _write_png(path: Path, size: tuple[int, int], mode: str = "RGB") -> Path:
    """按给定尺寸与模式写一张纯色 PNG 到临时目录。"""
    image = Image.new(mode, size, (200, 30, 30))
    image.save(path, format="PNG")
    return path


def test_build_thumbnail_bytes_downsamples_large_image(tmp_path: Path) -> None:
    """大于上限的图片按长边收敛到 768 且保持纵横比。"""
    source = _write_png(tmp_path / "large.png", (1600, 1000))

    thumbnail = build_thumbnail_bytes(source)

    assert thumbnail is not None
    with Image.open(BytesIO(thumbnail)) as decoded:
        assert decoded.format == "JPEG"
        assert max(decoded.size) == THUMBNAIL_MAX_EDGE
        assert decoded.size[0] / decoded.size[1] == pytest.approx(1.6, abs=0.01)


def test_build_thumbnail_bytes_keeps_small_image_unchanged(tmp_path: Path) -> None:
    """小于上限的图片不放大，按原尺寸编码。"""
    source = _write_png(tmp_path / "small.png", (100, 80))

    thumbnail = build_thumbnail_bytes(source)

    assert thumbnail is not None
    with Image.open(BytesIO(thumbnail)) as decoded:
        assert decoded.size == (100, 80)


def test_build_thumbnail_bytes_flattens_alpha_onto_white(tmp_path: Path) -> None:
    """带透明通道的图片合成白底编码为无 alpha 的 JPEG。"""
    source = _write_png(tmp_path / "alpha.png", (200, 200), mode="RGBA")

    thumbnail = build_thumbnail_bytes(source)

    assert thumbnail is not None
    with Image.open(BytesIO(thumbnail)) as decoded:
        assert decoded.mode == "RGB"


def test_build_thumbnail_bytes_returns_none_for_invalid_input(tmp_path: Path) -> None:
    """损坏数据与不存在的路径统一归一为 None。"""
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image at all")

    assert build_thumbnail_bytes(corrupt) is None
    assert build_thumbnail_bytes(tmp_path / "missing.png") is None


@pytest.mark.asyncio
async def test_build_preview_contents_preserves_order_and_skips_failures(
    tmp_path: Path,
) -> None:
    """多张并发生成时保持输入顺序，失败路径跳过且不影响其余项。"""
    first = _write_png(tmp_path / "first.png", (900, 900))
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"broken")
    second = _write_png(tmp_path / "second.png", (300, 200))

    contents = await build_preview_contents([first, corrupt, second])

    assert len(contents) == 2
    assert all(isinstance(content, ImageContent) for content in contents)
    assert all(content.mime_type == "image/jpeg" for content in contents)
    # base64 可解码且两张尺寸可区分，证明顺序与输入一一对应。
    first_decoded = Image.open(BytesIO(base64.b64decode(contents[0].data)))
    second_decoded = Image.open(BytesIO(base64.b64decode(contents[1].data)))
    assert first_decoded.size == (768, 768)
    assert second_decoded.size == (300, 200)


@pytest.mark.asyncio
async def test_build_preview_contents_empty_input_returns_empty() -> None:
    """空输入直接返回空列表，不进入线程调度。"""
    assert await build_preview_contents([]) == []


def _patch_client_success(monkeypatch: pytest.MonkeyPatch) -> None:
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
    """mock 批量保存成功且落盘真实 PNG，返回其路径供断言复用。"""
    saved = _write_png(tmp_path / "saved.png", (1200, 800))
    result_cls = io_save.AutoSaveResult

    async def fake_save_multiple(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, tool_name
        return [
            result_cls(
                success=True,
                original_url=images[0]["url"],
                local_path=str(saved),
                markdown_ref="![image](saved.png)",
            )
        ]

    monkeypatch.setattr(io_save.AutoSaveManager, "save_multiple_images", fake_save_multiple)
    return saved


@pytest.mark.asyncio
async def test_generation_result_carries_preview_after_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """预览开启且保存成功时，content 为文本在前、缩略图在后的 ImageContent。"""
    _patch_client_success(monkeypatch)
    _patch_save_real_file(monkeypatch, tmp_path)

    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    result = await run_text_to_image(TextToImageInput(prompt="a cat"), config, ctx=None)

    assert result.is_error is False
    assert isinstance(result.content[0], TextContent)
    image_blocks = [content for content in result.content if isinstance(content, ImageContent)]
    assert len(image_blocks) == 1
    assert image_blocks[0].mime_type == "image/jpeg"
    with Image.open(BytesIO(base64.b64decode(image_blocks[0].data))) as decoded:
        assert decoded.format == "JPEG"
        assert max(decoded.size) <= THUMBNAIL_MAX_EDGE
    # structuredContent 不因预览改变。
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert structured["data"][0]["local_path"].endswith("saved.png")


@pytest.mark.asyncio
async def test_generation_result_truncates_preview_beyond_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """保存张数超过预览上限时缩略图恰为上限张，文本含截断说明，结构化数据完整。"""
    total = PREVIEW_MAX_IMAGES + 2

    async def fake_text_to_image_many(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        return {
            "success": True,
            "data": [
                {"url": f"https://example.com/generated-{index}.png"} for index in range(total)
            ],
            "usage": {"generated_images": total},
            "status": "completed",
        }

    monkeypatch.setattr(SeedreamClient, "text_to_image", fake_text_to_image_many)

    saved_paths = [_write_png(tmp_path / f"saved-{index}.png", (64, 48)) for index in range(total)]
    result_cls = io_save.AutoSaveResult

    async def fake_save_multiple(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, tool_name
        return [
            result_cls(
                success=True,
                original_url=images[index]["url"],
                local_path=str(saved_paths[index]),
                markdown_ref=f"![image](saved-{index}.png)",
            )
            for index in range(len(images))
        ]

    monkeypatch.setattr(io_save.AutoSaveManager, "save_multiple_images", fake_save_multiple)

    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    result = await run_text_to_image(TextToImageInput(prompt="a cat"), config, ctx=None)

    assert result.is_error is False
    # 缩略图张数收敛到上限，超出的保存项不再进入 content。
    image_blocks = [content for content in result.content if isinstance(content, ImageContent)]
    assert len(image_blocks) == PREVIEW_MAX_IMAGES
    response_text = next(
        content.text for content in result.content if isinstance(content, TextContent)
    )
    assert f"共已保存 {total} 张" in response_text
    assert f"仅附前 {PREVIEW_MAX_IMAGES} 张缩略图预览" in response_text
    # 截断只作用于预览，structuredContent.data 仍包含全部保存条目。
    structured = result.structured_content
    assert isinstance(structured, dict)
    assert len(structured["data"]) == total
    assert all(entry.get("local_path") for entry in structured["data"])


@pytest.mark.asyncio
async def test_generation_result_preview_disabled_keeps_text_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """preview_enabled=False 时 content 仅含文本，行为与本功能引入前一致。"""
    _patch_client_success(monkeypatch)
    _patch_save_real_file(monkeypatch, tmp_path)

    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(
        api_key="test_key",
        auto_save_base_dir=str(tmp_path),
        preview_enabled=False,
    )
    result = await run_text_to_image(TextToImageInput(prompt="a cat"), config, ctx=None)

    assert result.is_error is False
    assert all(isinstance(content, TextContent) for content in result.content)


@pytest.mark.asyncio
async def test_generation_result_no_preview_when_save_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """自动保存整体失败降级时无本地文件，content 不附带预览。"""
    _patch_client_success(monkeypatch)

    async def failing_save_multiple(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, images, tool_name
        raise RuntimeError("下载失败")

    monkeypatch.setattr(io_save.AutoSaveManager, "save_multiple_images", failing_save_multiple)

    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    result = await run_text_to_image(TextToImageInput(prompt="a cat"), config, ctx=None)

    assert result.is_error is False
    assert all(isinstance(content, TextContent) for content in result.content)


@pytest.mark.asyncio
async def test_generation_result_no_preview_when_generation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """生成失败时不进入自动保存与预览，content 仅错误文本。"""

    async def fake_failed(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        return {
            "success": False,
            "data": [],
            "error": {"code": "boom", "message": "生成失败"},
        }

    monkeypatch.setattr(SeedreamClient, "text_to_image", fake_failed)

    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path))
    result = await run_text_to_image(TextToImageInput(prompt="a cat"), config, ctx=None)

    assert result.is_error is True
    assert all(isinstance(content, TextContent) for content in result.content)


def test_preview_enabled_env_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """SEEDREAM_PREVIEW_ENABLED 环境变量按 bool 语义解析进配置。"""
    from seedream_mcp.config import build_config_from_sources

    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")

    monkeypatch.setenv("SEEDREAM_PREVIEW_ENABLED", "false")
    config = build_config_from_sources(overrides={"api_key": "test_key"}, env_file=str(empty_env))
    assert config.preview_enabled is False

    monkeypatch.setenv("SEEDREAM_PREVIEW_ENABLED", "true")
    config = build_config_from_sources(overrides={"api_key": "test_key"}, env_file=str(empty_env))
    assert config.preview_enabled is True
