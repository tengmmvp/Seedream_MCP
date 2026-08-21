"""app_lifespan 注入测试：验证 yield 含 config 与可用 client 的字典。

SeedreamClient 与 DownloadManager 为模块级单例，修复 stateless_http 模式下每请求
重入 lifespan 导致的连接池退化；同时守护单例的跨重入复用语义。
"""

import asyncio
from typing import Any

import pytest

from seedream_mcp import config as config_module
import seedream_mcp.resources as resources
import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.schemas import TextToImageInput

# lifespan 复位 fixture reset_lifespan_singletons 由 tests/conftest.py 共享提供


async def test_app_lifespan_yields_config_and_client(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """lifespan yield 的状态字典含活动配置与已就绪的 client 与 download_manager。"""
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)

    async with server.app_lifespan(server.mcp) as state:
        assert isinstance(state, dict)
        assert state["config"] is config
        client = state["client"]
        assert client is not None
        # client 在 lifespan 期内应已持有可用的 httpx 客户端
        assert client._client is not None
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

    清理语句位于 finally 内，写在 finally 之后的语句会因异常继续传播被跳过。
    """
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)

    with pytest.raises(RuntimeError, match="boom"):
        async with server.app_lifespan(server.mcp):
            raise RuntimeError("boom")

    assert resources._active_resource is None


def test_get_lifespan_resource_swallows_value_error_from_request_context() -> None:
    """ctx.request_context 抛 ValueError 时守卫返回 None 而非异常逃逸。

    mcp 的 request_context 无请求上下文时抛 ValueError 而非 AttributeError，
    仅捕后者会令异常从本应回退 None 的守卫路径逃逸。
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

    events: list[str] = []
    real_drain = auto_save_module.drain_background_cleanup_tasks

    async def recording_drain() -> None:
        events.append("drain")
        await real_drain()

    monkeypatch.setattr(auto_save_module, "drain_background_cleanup_tasks", recording_drain)
    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)

    async with server.app_lifespan(server.mcp) as state:
        client = state["client"]
        download_manager = state["download_manager"]
        real_client_close = client.close
        real_manager_close = download_manager.close

        async def recording_client_close() -> None:
            events.append("close:client")
            await real_client_close()

        async def recording_manager_close() -> None:
            events.append("close:download_manager")
            await real_manager_close()

        monkeypatch.setattr(client, "close", recording_client_close)
        monkeypatch.setattr(download_manager, "close", recording_manager_close)

    assert events[0] == "drain", "drain 须先于全部共享资源 close 执行"
    assert "drain" not in events[1:]
    assert set(events[1:]) == {"close:client", "close:download_manager"}
    assert resources._active_resource is None


async def test_cleanup_shared_resources_unconditional_closes_inflight(
    monkeypatch: pytest.MonkeyPatch,
    reset_lifespan_singletons,
) -> None:
    """idle_only=False 的进程退出兜底无视在途引用，无条件关闭全部资源。

    streamable-http 退出清理走本分支，覆盖关闭时仍有在途会话的场景；误按引用
    计数门控会阻止连接池关闭。
    """

    class _Closeable:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class _FakeResource:
        def __init__(self, refcount: int) -> None:
            self.client = _Closeable()
            self.download_manager = _Closeable()
            self.refcount = refcount

    active = _FakeResource(refcount=2)
    retired = _FakeResource(refcount=1)
    monkeypatch.setattr(resources, "_active_resource", active)
    monkeypatch.setattr(resources, "_retired_resources", [retired])

    await resources._cleanup_shared_resources(idle_only=False)

    assert active.client.closed and active.download_manager.closed
    assert retired.client.closed and retired.download_manager.closed
    assert resources._active_resource is None
    assert resources._retired_resources == []


async def test_build_active_resource_closes_client_when_manager_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """download_manager 初始化失败时补偿关闭已创建的 client，不泄漏半初始化资源。"""
    from seedream_mcp.client import SeedreamClient
    from seedream_mcp.utils.io import io_download

    close_calls: list[str] = []

    async def _failing_manager_aenter(self: object) -> object:
        raise RuntimeError("manager init failed")

    async def _record_client_close(self: SeedreamClient) -> None:
        close_calls.append("client")
        if self._client is not None:
            await self._client.aclose()

    monkeypatch.setattr(io_download.DownloadManager, "__aenter__", _failing_manager_aenter)
    monkeypatch.setattr(SeedreamClient, "close", _record_client_close)

    config = SeedreamConfig(api_key="test_key")
    with pytest.raises(RuntimeError, match="manager init failed"):
        await resources._build_active_resource(config)

    assert close_calls == ["client"]
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
    """lifespan 状态非 dict 时回退活动配置。"""
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

    两个进入协程以屏障会合保证真正并发在途；锁内二次判定失效时第二个进入会
    构造新 client。
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

    批量保存局部构造的信号量只约束单次调用，跨请求叠加的并发须由共享会话的
    连接器统一限制。
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

    并发上限经构造参数传入，_ensure_session 每次构造连接器均施加；依赖会话
    建立后二次注入会在重建时静默失去上限。
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
    """复位协议覆盖 config._global_config，懒加载配置不跨用例残留。

    set_active_config(None) 只清活动配置，全局懒加载缓存须经复位清单清理。复位与
    断言经函数内 import 取 sys.modules 当前模块对象，与延迟消费方同目标。
    """
    from seedream_mcp import config as current_config_module

    current_config_module._global_config = SeedreamConfig(api_key="stale_key")
    server._reset_lifespan_state()
    assert current_config_module._global_config is None


# ==================== Lifespan 共享资源复用测试 ====================


class _FakeLifespanCtx:
    """模拟 MCP Context，仅提供 lifespan_context 访问路径与 no-op 进度/日志方法。

    execute_generation_handler 会调用 ctx.report_progress / ctx.info 等方法，
    此处提供空实现使流水线不报错。
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

    # 值类型不匹配，非 SeedreamClient / DownloadManager
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
    from seedream_mcp.tools.core.common import ToolMetadata, execute_generation_handler

    config = SeedreamConfig(api_key="test_key")
    monkeypatch.setattr(config_module, "_active_config", config)
    shared_client = SeedreamClient(config)

    captured_client: Any = None

    async def fake_executor(client: Any, context: Any) -> dict:
        nonlocal captured_client
        captured_client = client
        return {"success": True, "data": [], "usage": {}, "status": "completed"}

    metadata = ToolMetadata(
        tool_name="text_to_image",
        completion_title="文生图完成",
        failure_prefix="文生图",
        start_log_message="",
        start_log_values_builder=lambda c: (),
    )
    ctx = _FakeLifespanCtx({"client": shared_client})
    try:
        result = await execute_generation_handler(
            params=TextToImageInput(prompt="test", auto_save=False),
            config=config,
            module_logger=MagicMock(),
            metadata=metadata,
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
    from seedream_mcp.tools.core.common import ToolMetadata
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

    metadata = ToolMetadata(
        tool_name="text_to_image",
        completion_title="文生图完成",
        failure_prefix="文生图",
        start_log_message="",
        start_log_values_builder=lambda c: (),
    )
    ctx = _FakeLifespanCtx({"client": shared_client, "download_manager": shared_dm})
    try:
        await common_module.execute_generation_handler(
            params=TextToImageInput(prompt="test", auto_save=True, response_format="url"),
            config=config,
            module_logger=MagicMock(),
            metadata=metadata,
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

    results 侧若重新引入模块级可变状态，须在 _reset_lifespan_state 与本守护
    同步登记。
    """
    from seedream_mcp.tools.core import results as results_module

    assert not hasattr(results_module, "_last_sanitized_images")
    assert not hasattr(results_module, "reset_last_sanitized_images")
    server._reset_lifespan_state()


# ==================== 平铺 inputSchema 收紧版本守护 ====================


def test_tighten_flat_tool_schemas_private_surface_guard() -> None:
    """五工具经 SDK 私有面全部命中并完成收紧，SDK 升级改动私有 API 时本测试转红。

    依赖 mcp._tool_manager.get_tool 与 Tool.fn_metadata.arg_model 两个私有入口，
    get_tool 未命中仅告警跳过，fail-open 会使封闭性静默缺失。
    """
    assert len(server._FLAT_SCHEMA_TOOL_NAMES) == 5

    for name in server._FLAT_SCHEMA_TOOL_NAMES:
        tool = server.mcp._tool_manager.get_tool(name)
        assert tool is not None, f"SDK 私有面未命中工具: {name}"
        assert tool.parameters.get("additionalProperties") is False, name
        arg_model = tool.fn_metadata.arg_model
        assert arg_model.model_config.get("extra") == "forbid", name
