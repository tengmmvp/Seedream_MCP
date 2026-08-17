"""共享测试 fixture。

提供基础配置与工作区根目录 fixture，供需要 SeedreamConfig 或工作区隔离的测试复用，
避免各测试重复构造；需要差异化字段时直接以构造 kwargs 覆盖或用 dataclasses.replace。
"""

from __future__ import annotations

import asyncio
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


@pytest.fixture(autouse=True)
def _reset_global_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """每测试重置全局配置与可变模块状态，防止跨测试污染。"""
    from seedream_mcp.server import _reset_lifespan_state
    from seedream_mcp.utils.images import image_validation as image_validation_module

    # HEIC 解码器注册标志为模块全局，重置以隔离注册时序相关用例
    monkeypatch.setattr(image_validation_module, "_heif_opener_registered", False)
    # lifespan 共享单例、活动配置、全局配置懒加载缓存、asyncio.Lock、自动保存清理状态
    # 与目录扫描缓存等模块级可变状态统一经复位协议重建到干净态，避免跨事件循环复用
    # 与跨用例缓存污染；SDK 2.0 起传输配置直传 streamable_http_app 构造，settings
    # 不再持有 stateless_http 等传输字段，无需复位；复位清单见 _reset_lifespan_state
    _reset_lifespan_state()
