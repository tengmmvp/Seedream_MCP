"""_get_active_config 优先级测试：CLI 注入配置优先于全局配置。"""

import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig


def test_get_active_config_falls_back_to_global_config(monkeypatch) -> None:
    fallback_config = SeedreamConfig(api_key="test_key")

    monkeypatch.setattr(server, "_active_config", None)
    monkeypatch.setattr(server, "get_global_config", lambda: fallback_config)

    assert server._get_active_config() is fallback_config


def test_get_active_config_prefers_cli_injected_config(monkeypatch) -> None:
    cli_config = SeedreamConfig(api_key="cli_key")
    fallback_config = SeedreamConfig(api_key="global_key")

    monkeypatch.setattr(server, "_active_config", cli_config)
    monkeypatch.setattr(server, "get_global_config", lambda: fallback_config)

    assert server._get_active_config() is cli_config
