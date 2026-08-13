"""get_active_config 优先级测试：CLI 注入配置优先于全局配置。"""

from seedream_mcp import config as config_module
from seedream_mcp.config import SeedreamConfig, get_active_config


def test_get_active_config_falls_back_to_global_config(monkeypatch) -> None:
    fallback_config = SeedreamConfig(api_key="test_key")

    monkeypatch.setattr(config_module, "_active_config", None)
    monkeypatch.setattr(config_module, "get_global_config", lambda: fallback_config)

    assert get_active_config() is fallback_config


def test_get_active_config_prefers_cli_injected_config(monkeypatch) -> None:
    cli_config = SeedreamConfig(api_key="cli_key")
    fallback_config = SeedreamConfig(api_key="global_key")

    monkeypatch.setattr(config_module, "_active_config", cli_config)
    monkeypatch.setattr(config_module, "get_global_config", lambda: fallback_config)

    assert get_active_config() is cli_config
