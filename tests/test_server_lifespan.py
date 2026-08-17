"""app_lifespan 注入测试：验证 yield 含 config 与可用 client 的字典。

SeedreamClient 与 DownloadManager 为模块级单例，以修复 stateless_http
模式下每请求重入 lifespan 导致连接池退化的问题。此文件同时守护单例的跨重入复用语义。
"""

import asyncio
from typing import Any

import pytest

from seedream_mcp import config as config_module
import seedream_mcp.resources as resources
import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.schemas import TextToImageInput


@pytest.fixture
async def reset_lifespan_singletons():
    """重置模块级单例与传输模式，测试后关闭本测试创建的实例，避免跨测试污染与资源泄漏。"""
    server._reset_lifespan_state()
    yield
    active = resources._active_resource
    if active is not None:
        await active.client.close()
        await active.download_manager.close()
    server._reset_lifespan_state()


async def test_app_lifespan_yields_config_and_client(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)

    async with server.app_lifespan(server.mcp) as state:
        assert isinstance(state, dict)
        assert state["config"] is config
        client = state["client"]
        assert client is not None
        # client 在 lifespan 期内应已持有可用的 httpx 客户端
        assert client._client is not None
        # download_manager 同样注入
        assert state["download_manager"] is not None


async def test_app_lifespan_stdio_cleans_up_on_teardown(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """stdio 模式 lifespan 退出时在同事件循环清理单例，实现进程级优雅关闭。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)

    async with server.app_lifespan(server.mcp) as state:
        assert state["client"] is not None

    # teardown 后活动资源已清理，执行 close 并置 None
    assert resources._active_resource is None


async def test_app_lifespan_cleans_up_on_exception_teardown(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """yield 体抛异常的 teardown 同样执行共享资源清理，防止异常退出泄漏连接池。

    清理语句位于 finally 内：asynccontextmanager 的异常经 athrow 注入并在 finally
    后继续向外传播，写在 finally 之后的语句会被跳过。
    """
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)

    with pytest.raises(RuntimeError, match="boom"):
        async with server.app_lifespan(server.mcp):
            raise RuntimeError("boom")

    assert resources._active_resource is None


def test_get_lifespan_resource_swallows_value_error_from_request_context() -> None:
    """ctx.request_context 抛 ValueError 时守卫返回 None 而非异常逃逸。

    mcp 的 Context.request_context 在无请求上下文时抛 ValueError 而非 AttributeError，
    仅捕 AttributeError 会令异常从本应回退 None 的守卫路径逃逸。
    """

    class _ValueErrorCtx:
        @property
        def request_context(self) -> object:
            raise ValueError("Context is not available outside of a request")

    from seedream_mcp.config import LIFESPAN_KEY_CLIENT
    from seedream_mcp.tools.core.common import get_lifespan_resource

    assert get_lifespan_resource(_ValueErrorCtx(), LIFESPAN_KEY_CLIENT, object) is None


async def test_cleanup_shared_resources_drains_background_cleanup_first(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """进程级清理先等待在途后台清理任务收尾，再关闭共享资源。"""
    from seedream_mcp.utils.io import io_save as auto_save_module

    drain_calls: list[bool] = []

    async def fake_drain() -> None:
        drain_calls.append(True)

    monkeypatch.setattr(auto_save_module, "drain_background_cleanup_tasks", fake_drain)
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)

    async with server.app_lifespan(server.mcp):
        pass

    assert drain_calls == [True]
    assert resources._active_resource is None


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
    monkeypatch.setattr(server, "get_active_config", lambda: fallback)

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
    """并发进入 lifespan 应复用同一单例，验证 _shared_init_lock 防竞态。

    两个进入协程在 lifespan 体内以屏障会合，保证真正并发在途而非依赖 gather 的
    调度时序；若锁内二次判定失效，第二个进入会构造新 client 使断言失败。
    """
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)

    both_entered = asyncio.Event()
    entered_count = 0
    count_guard = asyncio.Lock()

    async def enter() -> Any:
        nonlocal entered_count
        async with server.app_lifespan(server.mcp) as state:
            async with count_guard:
                entered_count += 1
                if entered_count == 2:
                    both_entered.set()
            await both_entered.wait()
            return state["client"]

    client_a, client_b = await asyncio.gather(enter(), enter())
    assert client_a is client_b


async def test_app_lifespan_rebuilds_on_config_change(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """config 身份变化后下次进入 lifespan 重建单例，使热重载生效。"""
    config_a = SeedreamConfig(api_key="key_a")
    monkeypatch.setattr(config_module, "_active_config", config_a)
    async with server.app_lifespan(server.mcp) as state:
        client_a = state["client"]

    config_b = SeedreamConfig(api_key="key_b")
    monkeypatch.setattr(config_module, "_active_config", config_b)
    async with server.app_lifespan(server.mcp) as state:
        client_b = state["client"]

    assert client_b is not client_a
    assert client_b.config is config_b


async def test_app_lifespan_applies_download_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """共享 DownloadManager 经构造参数施加进程级下载并发上限到会话连接器。

    AutoSaveManager 每次批量保存局部构造的信号量只约束单次调用，跨请求叠加的下载
    并发须由共享会话的连接器统一限制，否则全进程连接数可达调用次数乘以配置上限。
    """
    config = SeedreamConfig(api_key="test_key", auto_save_max_concurrent=3)
    monkeypatch.setattr(config_module, "_active_config", config)

    async with server.app_lifespan(server.mcp) as state:
        download_manager = state["download_manager"]
        session = download_manager._session
        assert session is not None
        assert session.connector.limit == 3


async def test_download_manager_connection_limit_survives_session_rebuild() -> None:
    """close 后重建的会话连接器保持构造期注入的并发上限。

    并发上限经 DownloadManager 构造参数传入，_ensure_session 每次构造连接器均施加
    该值；若依赖会话建立后二次注入，会话重建分支会静默失去上限。
    """
    from seedream_mcp.utils.io.io_download import DownloadManager

    manager = DownloadManager(connection_limit=2)
    session = await manager._ensure_session()
    assert session.connector.limit == 2
    await manager.close()
    rebuilt = await manager._ensure_session()
    assert rebuilt is not session
    assert rebuilt.connector.limit == 2
    await manager.close()


def test_reset_lifespan_state_clears_global_config() -> None:
    """复位协议覆盖 config._global_config，用例触发的懒加载配置不跨用例残留。

    set_active_config(None) 只清除 CLI 注入的活动配置；全局配置懒加载缓存若不在
    复位清单内，上个用例按其环境构建的配置会被后续用例经 get_active_config 读到。
    复位与断言均经函数内 import 取 sys.modules 的当前 config 模块对象，与延迟
    消费方（io_path 等）的调用时解析保持同一目标。
    """
    from seedream_mcp import config as current_config_module

    current_config_module._global_config = SeedreamConfig(api_key="stale_key")
    server._reset_lifespan_state()
    assert current_config_module._global_config is None


# ==================== Lifespan 共享资源复用测试 ====================


class _FakeLifespanCtx:
    """模拟 MCP Context，仅提供 lifespan_context 访问路径与 no-op 进度/日志方法。

    execute_generation_handler 内的 _safe_report_progress / _safe_ctx_log 会调用
    ctx.report_progress / ctx.info 等方法；此处提供空实现使流水线不报错。
    """

    def __init__(self, lifespan_context: Any) -> None:
        class _FakeRequestContext:
            pass

        self.request_context = _FakeRequestContext()
        self.request_context.lifespan_context = lifespan_context

    async def report_progress(self, **kwargs: Any) -> None:
        pass

    async def info(self, message: str) -> None:
        pass

    async def debug(self, message: str) -> None:
        pass

    async def warning(self, message: str) -> None:
        pass

    async def error(self, message: str) -> None:
        pass


async def test_try_get_shared_client_returns_lifespan_instance() -> None:
    """_try_get_shared_client / _try_get_shared_download_manager 返回注入的实例。"""
    from seedream_mcp.client import SeedreamClient
    from seedream_mcp.tools.core.common import (
        _try_get_shared_client,
        _try_get_shared_download_manager,
    )
    from seedream_mcp.utils.io.io_download import DownloadManager

    config = SeedreamConfig(api_key="test_key")
    shared_client = SeedreamClient(config)
    shared_dm = DownloadManager()
    try:
        ctx = _FakeLifespanCtx({"client": shared_client, "download_manager": shared_dm})
        assert _try_get_shared_client(ctx) is shared_client
        assert _try_get_shared_download_manager(ctx) is shared_dm
    finally:
        await shared_client.close()
        await shared_dm.close()


def test_try_get_shared_client_returns_none_for_invalid_context() -> None:
    """ctx 为 None、lifespan 非 dict、值类型不匹配时均返回 None。"""
    from seedream_mcp.tools.core.common import (
        _try_get_shared_client,
        _try_get_shared_download_manager,
    )

    # ctx=None 恒返回 None，调用方据此回退新建
    assert _try_get_shared_client(None) is None
    assert _try_get_shared_download_manager(None) is None

    # lifespan 非 dict
    assert _try_get_shared_client(_FakeLifespanCtx("not a dict")) is None
    assert _try_get_shared_download_manager(_FakeLifespanCtx("not a dict")) is None

    # 值类型不匹配（非 SeedreamClient / DownloadManager）
    bad_ctx = _FakeLifespanCtx({"client": "fake", "download_manager": 123})
    assert _try_get_shared_client(bad_ctx) is None
    assert _try_get_shared_download_manager(bad_ctx) is None

    # dict 中缺 key
    empty_ctx = _FakeLifespanCtx({})
    assert _try_get_shared_client(empty_ctx) is None
    assert _try_get_shared_download_manager(empty_ctx) is None


async def test_execute_generation_handler_reuses_lifespan_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_generation_handler 优先复用 lifespan 注入的共享 client 而非新建。"""
    from unittest.mock import MagicMock

    from seedream_mcp.client import SeedreamClient
    from seedream_mcp.tools.core.common import execute_generation_handler

    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)
    shared_client = SeedreamClient(config)

    captured_client: Any = None

    async def fake_executor(client: Any, context: Any) -> dict:
        nonlocal captured_client
        captured_client = client
        return {"success": True, "data": [], "usage": {}, "status": "completed"}

    ctx = _FakeLifespanCtx({"client": shared_client})
    try:
        result = await execute_generation_handler(
            params=TextToImageInput(prompt="test", auto_save=False),
            config=config,
            module_logger=MagicMock(),
            tool_name="text_to_image",
            completion_title="文生图完成",
            failure_prefix="文生图",
            start_log_message="",
            start_log_values_builder=lambda c: (),
            request_executor=fake_executor,
            ctx=ctx,
        )
        # executor 收到的 client 即 lifespan 注入的共享实例
        assert captured_client is shared_client
        assert not result.is_error
    finally:
        await shared_client.close()


async def test_execute_generation_handler_passes_shared_download_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handler 将 lifespan 注入的 download_manager 透传给 auto_save_from_urls。"""
    from unittest.mock import MagicMock

    from seedream_mcp.client import SeedreamClient
    from seedream_mcp.tools.core import common as common_module
    from seedream_mcp.utils.io.io_download import DownloadManager

    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)
    shared_client = SeedreamClient(config)
    shared_dm = DownloadManager()

    captured_dm: Any = None

    async def fake_executor(client: Any, context: Any) -> dict:
        return {
            "success": True,
            "data": [{"url": "http://x/1.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }

    async def fake_auto_save_from_urls(
        result: Any,
        prompt: Any,
        config: Any,
        save_path: Any,
        custom_name: Any,
        tool_name: Any,
        download_manager: Any = None,
        images: Any = None,
    ) -> tuple:
        nonlocal captured_dm
        captured_dm = download_manager
        return [], []

    monkeypatch.setattr(common_module, "auto_save_from_urls", fake_auto_save_from_urls)

    ctx = _FakeLifespanCtx({"client": shared_client, "download_manager": shared_dm})
    try:
        await common_module.execute_generation_handler(
            params=TextToImageInput(prompt="test", auto_save=True, response_format="url"),
            config=config,
            module_logger=MagicMock(),
            tool_name="text_to_image",
            completion_title="文生图完成",
            failure_prefix="文生图",
            start_log_message="",
            start_log_values_builder=lambda c: (),
            request_executor=fake_executor,
            ctx=ctx,
        )
        # auto_save 收到的 download_manager 即 lifespan 注入的共享实例
        assert captured_dm is shared_dm
    finally:
        await shared_client.close()
        await shared_dm.close()


def test_reset_lifespan_state_no_longer_touches_results_module() -> None:
    """复位协议不再清理 results 的净化哨兵：模块级哨兵已随显式传参重构移除。

    results 模块不再持有可变模块级状态，_reset_lifespan_state 的复位清单相应
    收敛为 io_save 清理节流状态与 io_scan 目录扫描缓存；results 侧若重新引入
    模块级可变状态，须在 _reset_lifespan_state 与本守护同步登记。
    """
    from seedream_mcp.tools.core import results as results_module

    assert not hasattr(results_module, "_last_sanitized_images")
    assert not hasattr(results_module, "reset_last_sanitized_images")
    server._reset_lifespan_state()


# ==================== 平铺 inputSchema 收紧版本守护 ====================


def test_tighten_flat_tool_schemas_private_surface_guard() -> None:
    """五工具经 SDK 私有面全部命中并完成收紧，SDK 升级改动私有 API 时本测试转红。

    _tighten_flat_tool_schemas 依赖 mcp._tool_manager.get_tool 与 Tool.fn_metadata.
    arg_model 两个私有入口，get_tool 返回 None 时仅告警跳过，fail-open 会让封闭性
    静默缺失。逐工具断言命中与两处收紧到位，私有面漂移即失败，提示维护者适配
    SDK 变化而非静默失去 inputSchema 封闭性。
    """
    assert len(server._FLAT_SCHEMA_TOOL_NAMES) == 5

    for name in server._FLAT_SCHEMA_TOOL_NAMES:
        tool = server.mcp._tool_manager.get_tool(name)
        assert tool is not None, f"SDK 私有面未命中工具: {name}"
        assert tool.parameters.get("additionalProperties") is False, name
        arg_model = tool.fn_metadata.arg_model
        assert arg_model.model_config.get("extra") == "forbid", name
