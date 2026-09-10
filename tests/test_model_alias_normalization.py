"""MODEL_ALIASES 全别名归一化守护。

参数化遍历全部别名，新增别名自动覆盖。
"""

import pytest

from seedream_mcp._config_sources import normalize_model_selector
from seedream_mcp.config import MODEL_ALIASES, SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamConfigError


@pytest.mark.parametrize(
    "alias,model_id",
    list(MODEL_ALIASES.items()),
    ids=list(MODEL_ALIASES),
)
def test_normalize_model_selector_resolves_every_alias(alias: str, model_id: str) -> None:
    """normalize_model_selector 将每个别名展开为映射目标 model_id。"""
    assert normalize_model_selector(alias) == model_id


@pytest.mark.parametrize(
    "variant",
    [
        "DOUBAO-SEEDREAM-5.0",
        "Doubao-Seedream-5.0",
        " doubao-seedream-5.0 ",
    ],
    ids=["upper", "mixed", "padded"],
)
def test_normalize_model_selector_ignores_case(variant: str) -> None:
    """大写与混合大小写别名同样展开，与家族解析层的小写口径一致。"""
    assert normalize_model_selector(variant) == MODEL_ALIASES["doubao-seedream-5.0"]


def test_normalize_model_selector_lowercases_unmatched_value() -> None:
    """未命中别名的选择器（如 Endpoint ID）也返回小写形态。"""
    assert normalize_model_selector("ep-20241001-ABCDE") == "ep-20241001-abcde"


@pytest.mark.parametrize(
    "alias,model_id",
    list(MODEL_ALIASES.items()),
    ids=list(MODEL_ALIASES),
)
def test_seedream_config_normalizes_every_alias(alias: str, model_id: str) -> None:
    """SeedreamConfig 构造时将每个别名归一化为映射目标 model_id。"""
    config = SeedreamConfig(api_key="k", model_id=alias)

    assert config.model_id == model_id


def test_seedream_config_normalizes_mixed_case_alias() -> None:
    """混合大小写别名经 SeedreamConfig 构造同样展开为完整 Model ID。"""
    config = SeedreamConfig(api_key="k", model_id="Doubao-Seedream-4.5")

    assert config.model_id == "doubao-seedream-4-5-251128"


def test_seedream_config_rejects_mixed_case_deprecated_selector() -> None:
    """混合大小写的下线模型形态在构造期拒绝，不推迟到 API 调用期失败。"""
    with pytest.raises(SeedreamConfigError, match="已下线"):
        SeedreamConfig(api_key="k", model_id="Doubao-Seedream-3.0")


def test_alias_table_includes_seedream_50_lite() -> None:
    """回归守护：5.0-lite 别名映射到与 5.0 相同的 model_id。"""
    assert "doubao-seedream-5.0-lite" in MODEL_ALIASES
    assert MODEL_ALIASES["doubao-seedream-5.0-lite"] == "doubao-seedream-5-0-260128"
