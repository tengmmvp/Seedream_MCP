"""MODEL_ALIASES 全别名归一化守护。

遍历 ``config.MODEL_ALIASES`` 全部条目，断言每个别名经 ``normalize_model_selector`` 与
``SeedreamConfig`` 构造后均解析到映射目标 model_id。补齐 test_config_model_selection 中
test_config_accepts_current_models 漏测 doubao-seedream-5.0-lite 且仅 Pro 有归一化断言的缺口。
新增别名时本参数化测试自动覆盖，无需手工补列。
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
