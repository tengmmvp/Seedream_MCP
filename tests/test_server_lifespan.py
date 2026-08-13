"""app_lifespan 注入测试：验证 yield 含 config 与可用 client 的字典。

SeedreamClient 与 DownloadManager 为模块级单例，以修复 stateless_http
模式下每请求重入 lifespan 导致连接池退化的问题。此文件同时守护单例的跨重入复用语义。
"""

import asyncio
from typing import Any

import pytest

import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig


@pytest.fixture
async def reset_lifespan_singletons():
    """重置模块级单例与传输模式，测试后关闭本测试创建的实例，避免跨测试污染与资源泄漏。"""
    server._reset_lifespan_state()
    yield
    client = server._shared_client
    download_manager = server._shared_download_manager
    if client is not None:
        await client.close()
    if download_manager is not None:
        await download_manager.close()
    server._reset_lifespan_state()


async def test_app_lifespan_yields_config_and_client(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(server, "_active_config", config)

    async with server.app_lifespan(server.mcp) as state:
        assert isinstance(state, dict)
        assert state["config"] is config
        client = state["client"]
        assert client is not None
        # client 在 lifespan 期内应可用，须持有 httpx 客户端或具备 close 方法
        assert getattr(client, "_client", None) is not None or hasattr(client, "close")
        # download_manager 同样注入
        assert state["download_manager"] is not None


async def test_app_lifespan_reuses_singleton_across_reentry(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """stateless 模式下 FastMCP 每请求重入 lifespan；单例须跨重入复用同一实例。

    若 lifespan 内直接创建并退出时关闭，第二次进入将拿到全新实例，丢失连接复用。
    """
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(server, "_active_config", config)
    # stateless_http 模式 teardown 不清理单例，跨重入复用
    monkeypatch.setattr(server.mcp.settings, "stateless_http", True)

    async with server.app_lifespan(server.mcp) as first_state:
        first_client = first_state["client"]
        first_download_manager = first_state["download_manager"]

    # 模拟第二请求重入 lifespan，应复用首次的实例而非重建
    async with server.app_lifespan(server.mcp) as second_state:
        assert second_state["client"] is first_client
        assert second_state["download_manager"] is first_download_manager


async def test_app_lifespan_stdio_cleans_up_on_teardown(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """stdio 模式 lifespan 退出时在同事件循环清理单例，实现进程级优雅关闭。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(server, "_active_config", config)
    monkeypatch.setattr(server.mcp.settings, "stateless_http", False)

    async with server.app_lifespan(server.mcp) as state:
        assert state["client"] is not None

    # teardown 后单例已清理，执行 close 并置 None
    assert server._shared_client is None
    assert server._shared_download_manager is None


def test_config_from_context_prefers_lifespan_config() -> None:
    """_config_from_context 优先取 lifespan 注入的 config，回退活动配置。"""
    config = SeedreamConfig(api_key="lifespan_key")

    class _FakeRequestContext:
        lifespan_context = {"config": config}

    class _FakeCtx:
        request_context = _FakeRequestContext()

    result = server._config_from_context(_FakeCtx())  # type: ignore[arg-type]
    assert result is config


def test_config_from_context_falls_back_when_state_not_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = SeedreamConfig(api_key="fallback_key")
    monkeypatch.setattr(server, "_get_active_config", lambda: fallback)

    class _FakeRequestContext:
        lifespan_context = "not a dict"

    class _FakeCtx:
        request_context = _FakeRequestContext()

    result = server._config_from_context(_FakeCtx())  # type: ignore[arg-type]
    assert result is fallback


async def test_app_lifespan_concurrent_reentry_creates_one_client(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """并发重入 lifespan 应复用同一单例，验证 _shared_init_lock 防竞态。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(server, "_active_config", config)
    monkeypatch.setattr(server.mcp.settings, "stateless_http", True)

    async def enter() -> Any:
        async with server.app_lifespan(server.mcp) as state:
            return state["client"]

    client_a, client_b = await asyncio.gather(enter(), enter())
    assert client_a is client_b


async def test_app_lifespan_rebuilds_on_config_change(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """config 身份变化后下次进入 lifespan 重建单例，使热重载生效。"""
    monkeypatch.setattr(server.mcp.settings, "stateless_http", True)
    config_a = SeedreamConfig(api_key="key_a")
    monkeypatch.setattr(server, "_active_config", config_a)
    async with server.app_lifespan(server.mcp) as state:
        client_a = state["client"]

    config_b = SeedreamConfig(api_key="key_b")
    monkeypatch.setattr(server, "_active_config", config_b)
    async with server.app_lifespan(server.mcp) as state:
        client_b = state["client"]

    assert client_b is not client_a
    assert client_b.config is config_b
