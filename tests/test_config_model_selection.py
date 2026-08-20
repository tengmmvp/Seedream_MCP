"""模型 ID 配置守护：下线模型黑名单、当前模型接受与别名归一化。"""

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamConfigError
from seedream_mcp.utils.model.model_capabilities import MODEL_ALIASES


def test_config_accepts_endpoint_id() -> None:
    """Endpoint ID 替代 Model ID 时接受，不得被下线模型黑名单拒绝。"""
    config = SeedreamConfig(api_key="k", model_id="ep-20241001-abcde")
    assert config.model_id == "ep-20241001-abcde"


def test_default_model_id_matches_alias_table() -> None:
    """config 默认模型与别名表同值，模型快照升级时两侧不静默分叉。

    默认值与 MODEL_ALIASES 同字面量双源维护，漏改一侧会使默认模型与别名展开
    结果指向不同版本。
    """
    config = SeedreamConfig(api_key="k")
    assert config.model_id == MODEL_ALIASES["doubao-seedream-5.0"]


def test_config_rejects_deprecated_seedream_3_0() -> None:
    """已下线的 3.0 完整 Model ID 构建期拒绝。"""
    with pytest.raises(SeedreamConfigError, match="已下线"):
        SeedreamConfig(api_key="k", model_id="doubao-seedream-3-0-t2i-250515")


def test_config_rejects_deprecated_seedream_3_0_alias() -> None:
    """已下线的 3.0 别名同样拒绝。"""
    with pytest.raises(SeedreamConfigError, match="已下线"):
        SeedreamConfig(api_key="k", model_id="doubao-seedream-3.0")


def test_config_rejects_deprecated_seededit_3_0() -> None:
    """已下线的 seededit 3.0 模型同样拒绝。"""
    with pytest.raises(SeedreamConfigError, match="已下线"):
        SeedreamConfig(api_key="k", model_id="doubao-seededit-3.0-i2i-250515")


def test_config_accepts_current_models() -> None:
    """当前模型别名经别名表展开为完整 Model ID，展开契约与 MODEL_ALIASES 锁定。"""
    for model in (
        "doubao-seedream-5.0-pro",
        "doubao-seedream-5.0",
        "doubao-seedream-4.5",
        "doubao-seedream-4.0",
    ):
        config = SeedreamConfig(api_key="k", model_id=model)
        assert config.model_id == MODEL_ALIASES[model]


def test_config_normalizes_seedream_50_pro_alias() -> None:
    """5.0 Pro 别名展开为完整 Model ID。"""
    config = SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro")
    assert config.model_id == "doubao-seedream-5-0-pro-260628"
