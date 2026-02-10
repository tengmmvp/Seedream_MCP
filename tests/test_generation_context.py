import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import build_generation_context, update_result_with_auto_save
from seedream_mcp.utils.auto_save import AutoSaveResult
from seedream_mcp.utils.errors import SeedreamValidationError


def _build_config() -> SeedreamConfig:
    return SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-4-0-250828",
        default_size="2K",
    )


def test_build_generation_context_uses_default_size_when_omitted() -> None:
    config = _build_config()
    context = build_generation_context({"prompt": "test"}, config)

    assert context.size == "2K"


def test_build_generation_context_rejects_explicit_empty_size() -> None:
    config = _build_config()

    with pytest.raises(SeedreamValidationError, match="图像尺寸不能为空"):
        build_generation_context({"prompt": "test", "size": ""}, config)


def test_update_result_with_auto_save_aligns_with_saveable_images_only() -> None:
    result = {
        "success": True,
        "data": [
            {
                "type": "image_generation.partial_failed",
                "image_index": 1,
                "error": {"code": "blocked", "message": "content blocked"},
            },
            {
                "type": "image_generation.partial_succeeded",
                "image_index": 2,
                "url": "https://example.com/ok.png",
            },
        ],
    }
    auto_save_results = [
        AutoSaveResult(
            success=True,
            original_url="https://example.com/ok.png",
            local_path="images/ok.png",
            markdown_ref="![ok](images/ok.png)",
        )
    ]

    updated = update_result_with_auto_save(result, auto_save_results)

    failed_item = updated["data"][0]
    success_item = updated["data"][1]

    assert "local_path" not in failed_item
    assert "markdown_ref" not in failed_item
    assert success_item["local_path"] == "images/ok.png"
    assert success_item["markdown_ref"] == "![ok](images/ok.png)"
