import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import build_generation_context
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
