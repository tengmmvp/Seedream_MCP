"""MODEL_ALIASES 全别名归一化守护。

参数化遍历全部别名，新增别名自动覆盖；补齐 test_config_accepts_current_models
仅对 Pro 断言归一化的缺口。
"""

import pytest

from seedream_mcp.config import MODEL_ALIASES, SeedreamConfig, normalize_model_selector


@pytest.mark.parametrize(
    "alias,model_id",
    list(MODEL_ALIASES.items()),
    ids=list(MODEL_ALIASES),
)
def test_normalize_model_selector_resolves_every_alias(alias: str, model_id: str) -> None:
    """normalize_model_selector 应将每个别名展开为映射目标 model_id。"""
    assert normalize_model_selector(alias) == model_id


@pytest.mark.parametrize(
    "alias,model_id",
    list(MODEL_ALIASES.items()),
    ids=list(MODEL_ALIASES),
)
def test_seedream_config_normalizes_every_alias(alias: str, model_id: str) -> None:
    """SeedreamConfig 构造时应将每个别名归一化为映射目标 model_id。"""
    config = SeedreamConfig(api_key="k", model_id=alias)

    assert config.model_id == model_id


def test_alias_table_includes_seedream_50_lite() -> None:
    """回归守护：5.0-lite 别名映射到与 5.0 相同的 model_id。"""
    assert "doubao-seedream-5.0-lite" in MODEL_ALIASES
    assert MODEL_ALIASES["doubao-seedream-5.0-lite"] == "doubao-seedream-5-0-260128"
