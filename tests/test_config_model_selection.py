"""模型 ID 配置守护：下线模型黑名单与别名归一化。"""

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamConfigError
from seedream_mcp.utils.model.model_capabilities import MODEL_ALIASES


def test_config_accepts_endpoint_id() -> None:
    """Endpoint ID 替代 Model ID 时接受，不得被下线模型黑名单拒绝。"""
    config = SeedreamConfig(api_key="k", model_id="ep-20241001-abcde")
    assert config.model_id == "ep-20241001-abcde"


def test_default_model_id_matches_alias_table() -> None:
    """config 默认模型为 5.0 别名展开的完整 Model ID，防别名表重指后漂移。

    默认值经 MODEL_ALIASES 派生为单一来源，此处以硬编码完整 ID 锁定具体取值，
    防止别名表被改指回别名自身使默认值不再是可直接请求的 Model ID；默认模型
    升级时应有意更新此处的锁定值。
    """
    config = SeedreamConfig(api_key="k")
    assert config.model_id == "doubao-seedream-5-0-260128"
    assert MODEL_ALIASES["doubao-seedream-5.0"] == config.model_id


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


def test_config_normalizes_seedream_50_pro_alias() -> None:
    """5.0 Pro 别名展开为完整 Model ID。"""
    config = SeedreamConfig(api_key="k", model_id="doubao-seedream-5.0-pro")
    assert config.model_id == "doubao-seedream-5-0-pro-260628"
