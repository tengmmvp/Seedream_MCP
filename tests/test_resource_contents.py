"""seedream://server/info 与 seedream://models/info 资源内容测试。

经 monkeypatch 替换 mcp.get_context 注入携带 lifespan 配置的上下文替身后直接调用
资源函数，参照 workspace_roots_resource 的既有测法。断言 server/info 的字段集与
取值，以及 models/info 的模型清单、能力字段集与关键能力值，能力对照
model_capabilities 的能力表。
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from seedream_mcp.config import LIFESPAN_KEY_CONFIG, SeedreamConfig
from seedream_mcp.resources import SERVER_NAME, SERVER_VERSION, mcp
from seedream_mcp.server import models_info_resource, server_info_resource
from seedream_mcp.utils.model import model_capabilities


class _FakeRequestContext:
    """持有 lifespan 状态字典的最小请求上下文替身。"""

    def __init__(self, lifespan_context: dict) -> None:
        self.lifespan_context = lifespan_context


class _FakeContext:
    """仅暴露 request_context 的最小 MCP 上下文替身。"""

    def __init__(self, lifespan_context: dict) -> None:
        self.request_context = _FakeRequestContext(lifespan_context)


def _test_config() -> SeedreamConfig:
    """构造带可辨识取值的配置，模型别名与尺寸经构造校验规范化。"""
    return SeedreamConfig(
        api_key="test-key",
        model_id="doubao-seedream-4.5",
        default_size="4K",
        auto_save_enabled=False,
    )


# ==================== seedream://server/info ====================


async def test_server_info_resource_reports_config_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """server/info 输出固定字段集，取值来自 lifespan 注入的配置。"""
    config = _test_config()
    monkeypatch.setattr(mcp, "get_context", lambda: _FakeContext({LIFESPAN_KEY_CONFIG: config}))

    data = json.loads(await server_info_resource())

    assert set(data) == {"name", "version", "model_id", "default_size", "auto_save_enabled"}
    assert data["name"] == SERVER_NAME
    assert data["version"] == SERVER_VERSION
    # 构造校验把别名 doubao-seedream-4.5 展开为完整 Model ID 后写回
    assert data["model_id"] == "doubao-seedream-4-5-251128"
    assert data["default_size"] == "4K"
    assert data["auto_save_enabled"] is False


# ==================== seedream://models/info ====================


async def test_models_info_resource_lists_all_aliases_with_model_ids() -> None:
    """models/info 按能力表别名顺序列出全部模型及对应 Model ID。"""
    data = json.loads(await models_info_resource())

    entries = data["models"]
    assert [entry["alias"] for entry in entries] == list(model_capabilities.MODEL_ALIASES)
    for entry in entries:
        assert entry["model_id"] == model_capabilities.MODEL_ALIASES[entry["alias"]]


async def test_models_info_resource_matches_capability_table() -> None:
    """每条目完整携带能力声明字段，且取值与 get_model_capabilities 全等。"""
    data = json.loads(await models_info_resource())

    expected_fields = set(model_capabilities.ModelCapabilities.__dataclass_fields__)
    for entry in data["models"]:
        assert set(entry) == {"alias", "model_id"} | expected_fields, entry["alias"]
        capabilities = asdict(model_capabilities.get_model_capabilities(entry["model_id"]))
        capabilities["allowed_presets"] = sorted(capabilities["allowed_presets"])
        assert {key: entry[key] for key in capabilities} == capabilities, entry["alias"]
        assert entry["allowed_presets"] == sorted(entry["allowed_presets"]), entry["alias"]


async def test_models_info_resource_reports_key_capability_values() -> None:
    """关键能力值对照能力表锁定，防止资源侧字段映射漂移。"""
    data = json.loads(await models_info_resource())
    by_alias = {entry["alias"]: entry for entry in data["models"]}

    pro = by_alias["doubao-seedream-5.0-pro"]
    assert pro["max_reference_images"] == model_capabilities.SEEDREAM_50PRO_MAX_REFERENCE_IMAGES
    assert pro["allowed_presets"] == ["1K", "2K"]
    assert pro["supports_stream"] is False
    assert pro["supports_tools"] is False
    assert pro["supports_sequential_generation"] is False
    assert pro["size_pixel_multiple"] == model_capabilities.SEEDREAM_50PRO_SIZE_PIXEL_MULTIPLE
    assert pro["min_size_pixels"] == model_capabilities.SEEDREAM_50PRO_MIN_SIZE_PIXELS
    assert pro["max_size_pixels"] == model_capabilities.SEEDREAM_50PRO_MAX_SIZE_PIXELS

    for alias in ("doubao-seedream-5.0", "doubao-seedream-5.0-lite"):
        entry = by_alias[alias]
        assert entry["model_id"] == "doubao-seedream-5-0-260128", alias
        assert entry["allowed_presets"] == ["2K", "3K", "4K"], alias
        assert (
            entry["max_reference_images"]
            == model_capabilities.SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
        ), alias
        assert entry["supports_tools"] is True, alias
        assert entry["supports_stream"] is True, alias
        assert entry["size_pixel_multiple"] is None, alias

    lite_45 = by_alias["doubao-seedream-4.5"]
    assert lite_45["allowed_presets"] == ["2K", "4K"]
    assert lite_45["supports_output_format"] is False

    model_40 = by_alias["doubao-seedream-4.0"]
    assert model_40["allowed_presets"] == ["1K", "2K", "4K"]
    assert model_40["max_reference_images"] == (
        model_capabilities.SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
    )
