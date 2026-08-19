"""共享测试 fixture。

提供基础配置与工作区根目录 fixture，供需要 SeedreamConfig 或工作区隔离的测试复用，
避免各测试重复构造；需要差异化字段时直接以构造 kwargs 覆盖或用 dataclasses.replace。
lifespan 复位类 fixture 经 _lifespan_state_guard 参数化收敛，各测试文件不再自持副本。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from seedream_mcp.config import SeedreamConfig


@pytest.fixture
def seedream_config() -> SeedreamConfig:
    """基础测试配置，api_key 固定为 test_key。

    差异化字段以构造 kwargs 或 dataclasses.replace 覆盖。
    """
    return SeedreamConfig(api_key="test_key")


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """屏蔽 asyncio.sleep，避免重试退避测试因真实等待而变慢。

    需要观察退避时长的测试不应使用此 fixture，改为自行捕获 sleep 参数。
    """

    async def _sleep(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(asyncio, "sleep", _sleep)


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时工作区根目录，并注入 SEEDREAM_WORKSPACE_ROOT 环境变量。"""
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


@asynccontextmanager
async def _lifespan_state_guard(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inject_config: bool,
    clear_session_manager: bool,
) -> AsyncIterator[None]:
    """lifespan 复位类 fixture 的共享协议。

    进入时重置模块级单例，按选项注入默认测试配置与清空 SDK 会话管理器引用；
    退出时关闭活动与退役共享资源后再次复位，连接池不跨用例泄漏。

    Args:
        monkeypatch: pytest 补丁入口，注入的活动配置随用例结束自动还原。
        inject_config: 是否注入 api_key 为 test_key 的默认测试配置。
        clear_session_manager: 是否清空 streamable-http 会话管理器引用，供每次
            streamable_http_app 调用重建它的用例做跨测试隔离。
    """
    import seedream_mcp.resources as resources
    import seedream_mcp.server as server
    from seedream_mcp import config as config_module

    def _clear_session_manager() -> None:
        server.mcp._lowlevel_server._session_manager = None

    if clear_session_manager:
        _clear_session_manager()
    server._reset_lifespan_state()
    if inject_config:
        monkeypatch.setattr(config_module, "_active_config", SeedreamConfig(api_key="test_key"))
    try:
        yield
    finally:
        if clear_session_manager:
            _clear_session_manager()
        active = resources._active_resource
        if active is not None:
            await active.client.close()
            await active.download_manager.close()
        for retired in list(resources._retired_resources):
            await retired.client.close()
            await retired.download_manager.close()
        server._reset_lifespan_state()


@pytest.fixture
async def reset_lifespan_singletons(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """重置 lifespan 单例并注入默认测试配置，退出时关闭残留资源并复位。

    需要差异化配置的用例自行覆盖 config._active_config。
    """
    async with _lifespan_state_guard(monkeypatch, inject_config=True, clear_session_manager=False):
        yield


@pytest.fixture
async def reset_http_app_state(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """在 reset_lifespan_singletons 之上额外清空 streamable-http 会话管理器引用。

    streamable_http_app 每次调用无条件新建并覆盖 _session_manager，前置与收尾
    置 None 实现跨测试隔离。
    """
    async with _lifespan_state_guard(monkeypatch, inject_config=True, clear_session_manager=True):
        yield


@pytest.fixture(autouse=True)
def _reset_global_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """每测试重置全局配置与可变模块状态，防止跨测试污染。"""
    from seedream_mcp.server import _reset_lifespan_state
    from seedream_mcp.utils.images import image_validation as image_validation_module
    from seedream_mcp.utils.io.io_path import clear_resolved_env_root_cache

    # PIL 已导入时快照解压炸弹阈值，收尾恢复：HEIC 注册分支经 Image.MAX_IMAGE_PIXELS
    # 做进程级覆写且不自行恢复；未导入时不快照，避免复位本身触发 PIL 的惰性导入。
    pil_image_module = sys.modules.get("PIL.Image")
    max_pixels_before = pil_image_module.MAX_IMAGE_PIXELS if pil_image_module is not None else None

    # HEIC 解码器注册标志为模块全局，重置以隔离注册时序相关用例
    monkeypatch.setattr(image_validation_module, "_heif_opener_registered", False)
    # lifespan 共享单例、活动配置、全局配置懒加载缓存、asyncio.Lock、自动保存清理状态
    # 与目录扫描缓存等模块级可变状态统一经复位协议重建到干净态，避免跨事件循环复用
    # 与跨用例缓存污染；SDK 2.0 起传输配置直传 streamable_http_app 构造，settings
    # 不再持有 stateless_http 等传输字段，无需复位；复位清单见 _reset_lifespan_state
    _reset_lifespan_state()
    # io_path 回退根 resolve 缓存与上述复位项同属复位协议，在此直接登记
    clear_resolved_env_root_cache()
    yield
    if pil_image_module is not None:
        pil_image_module.MAX_IMAGE_PIXELS = max_pixels_before
