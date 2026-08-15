"""stateful streamable-http 会话级 lifespan 重入测试。

mcp 1.28 的会话管理器在 stateful 模式下为每个会话独立运行低层 Server.run，每次
运行都进入一次 app_lifespan，会话退出即触发对应 lifespan teardown。以嵌套的
app_lifespan 复现双会话并发与先后退出场景，锁定 teardown 清理的引用计数门控：
任一会话退出不得关闭其余会话仍在使用的共享资源，全部在途引用归零后才清理。
"""

import pytest

from seedream_mcp import config as config_module
import seedream_mcp.resources as resources
import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig


@pytest.fixture
async def reset_lifespan_singletons():
    """重置模块级单例与传输模式，测试后关闭残留实例并再次复位，避免跨测试污染。"""
    server._reset_lifespan_state()
    yield
    active = resources._active_resource
    if active is not None:
        await active.client.close()
        await active.download_manager.close()
    for retired in list(resources._retired_resources):
        await retired.client.close()
        await retired.download_manager.close()
    server._reset_lifespan_state()


def _activate_config(monkeypatch: pytest.MonkeyPatch, config: SeedreamConfig) -> None:
    """注入活动配置并按 stateful 模式设置传输标志。"""
    monkeypatch.setattr(config_module, "_active_config", config)
    monkeypatch.setattr(server.mcp.settings, "stateless_http", False)


async def test_second_session_exit_keeps_shared_resource_for_first(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """双会话并发时会话 2 退出仅递减引用，会话 1 的连接池保持可用。"""
    _activate_config(monkeypatch, SeedreamConfig(api_key="test_key"))

    async with server.app_lifespan(server.mcp) as first_state:
        shared_client = first_state["client"]
        shared_download_manager = first_state["download_manager"]
        async with server.app_lifespan(server.mcp) as second_state:
            assert second_state["client"] is shared_client
            assert second_state["download_manager"] is shared_download_manager

        active = resources._active_resource
        assert active is not None
        assert active.refcount == 1
        # close 会把 client._client 与 download_manager._session 置 None，
        # 两者非 None 即证明会话 2 的 teardown 未关闭共享资源。
        assert shared_client._client is not None
        assert shared_download_manager._session is not None

    # 最后一个会话退出后引用归零，teardown 清理生效
    assert resources._active_resource is None
    assert shared_client._client is None
    assert shared_download_manager._session is None


async def test_exit_skips_cleanup_while_retired_resource_in_flight(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """退役资源仍有在途会话时，活动槽位归零也不触发清理。

    会话 1 持有旧 config 的资源期间 config 变更，会话 2 进入使旧资源退役。
    会话 2 先退出的时刻活动资源引用已归零，但退役资源仍被会话 1 使用，清理须
    继续搁置至会话 1 退出。
    """
    _activate_config(monkeypatch, SeedreamConfig(api_key="key_a"))

    async with server.app_lifespan(server.mcp) as first_state:
        old_client = first_state["client"]
        _activate_config(monkeypatch, SeedreamConfig(api_key="key_b"))
        async with server.app_lifespan(server.mcp) as second_state:
            new_client = second_state["client"]
        assert new_client is not old_client

        active = resources._active_resource
        assert active is not None
        assert active.client is new_client
        # 退役资源仍被会话 1 持有，清理被门控搁置
        assert resources._retired_resources
        assert old_client._client is not None
        assert new_client._client is not None

    # 会话 1 退出后退役与活动资源全部清理
    assert resources._active_resource is None
    assert resources._retired_resources == []
    assert old_client._client is None
    assert new_client._client is None


async def test_new_session_rebuilds_after_all_sessions_exit(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """全部会话退出触发清理后，新会话进入重建共享资源而非复用已关闭实例。"""
    _activate_config(monkeypatch, SeedreamConfig(api_key="test_key"))

    async with server.app_lifespan(server.mcp) as first_state:
        first_client = first_state["client"]
    assert resources._active_resource is None

    async with server.app_lifespan(server.mcp) as second_state:
        assert second_state["client"] is not first_client
        assert second_state["client"]._client is not None
