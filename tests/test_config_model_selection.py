import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.errors import SeedreamConfigError


def test_config_accepts_endpoint_id() -> None:
    # 文档允许用 Endpoint ID 替代 Model ID，不得被下线模型黑名单拒绝
    config = SeedreamConfig(api_key="k", model_id="ep-20241001-abcde")
    assert config.model_id == "ep-20241001-abcde"


def test_config_rejects_deprecated_seedream_3_0() -> None:
    with pytest.raises(SeedreamConfigError, match="已下线"):
        SeedreamConfig(api_key="k", model_id="doubao-seedream-3-0-t2i-250515")


def test_config_rejects_deprecated_seedream_3_0_alias() -> None:
    with pytest.raises(SeedreamConfigError, match="已下线"):
        SeedreamConfig(api_key="k", model_id="doubao-seedream-3.0")


def test_config_rejects_deprecated_seededit_3_0() -> None:
    with pytest.raises(SeedreamConfigError, match="已下线"):
        SeedreamConfig(api_key="k", model_id="doubao-seededit-3.0-i2i-250515")


def test_config_accepts_current_models() -> None:
    for model in (
        "doubao-seedream-5.0-pro",
        "doubao-seedream-5.0",
        "doubao-seedream-4.5",
        "doubao-seedream-4.0",
    ):
        config = SeedreamConfig(api_key="k", model_id=model)
        assert config.model_id


def test_config_normalizes_seedream_50_pro_alias() -> None:
    config = SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro")
    assert config.model_id == "doubao-seedream-5-0-pro-260628"
