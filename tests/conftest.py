"""共享测试 fixture。

提供基础配置与工作区根目录 fixture，供需要 SeedreamConfig 或工作区隔离的测试复用，
避免各测试重复构造；需要差异化字段时用 model_copy(update={...}) 覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedream_mcp.config import SeedreamConfig


@pytest.fixture
def seedream_config() -> SeedreamConfig:
    """基础测试配置（api_key=test_key）；差异化字段用 model_copy 覆盖。"""
    return SeedreamConfig(api_key="test_key")


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时工作区根目录，并注入 SEEDREAM_WORKSPACE_ROOT 环境变量。"""
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path
