"""生成侧测试共享工厂与 monkeypatch 辅助。

make_generation_context 供 test_results_output_guards、test_core_pipeline_guards
与 test_parallel_cancellation 复用同一份 15 字段默认构造，差异字段经 **overrides
覆盖，字段增删时只改本工厂。_patch_client_success 与 _patch_save_real_file 供
test_generation_pipeline_previews 与 test_image_preview 复用文生图成功与单图
真实落盘的 mock 装配。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.tools.core.context import GenerationExecutionContext
from seedream_mcp.utils.io import io_save


def make_generation_context(**overrides: Any) -> GenerationExecutionContext:
    """构造生成执行上下文，未覆盖字段取与既有各测试一致的默认值。

    Args:
        **overrides: 覆盖默认值的字段，键须为 GenerationExecutionContext 的字段名。

    Returns:
        填充默认值与覆盖值后的生成执行上下文。
    """
    defaults: dict[str, Any] = {
        "prompt": "test",
        "optimize_prompt_options": None,
        "size": "2K",
        "watermark": False,
        "response_format": "url",
        "output_format": None,
        "stream": False,
        "tools": None,
        "layer_decomposition": False,
        "background": None,
        "max_images": None,
        "request_count": 1,
        "parallelism": 1,
        "enable_auto_save": True,
        "save_path": None,
        "custom_name": None,
    }
    defaults.update(overrides)
    return GenerationExecutionContext(**defaults)


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
    """mock 单图保存成功且落盘真实 PNG，返回其路径供断言复用。

    mock 挂在 save_image 而非 save_multiple_images：批量编排真实执行，其对
    self.save_image 的调用运行时解析命中补丁，保存动作替换为返回真实落盘的 PNG。
    """
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
