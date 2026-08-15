"""守护测试：_prepare_image_input 的缓存键以 workspace_roots 隔离。

防止不同工作区（MCP Roots）的请求因缓存键缺失 workspace 维度而跨租户命中，
导致本地图片被错误地按另一工作区的缓存结果返回。
"""

from pathlib import Path
from typing import Dict, List

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.images import image_input
from seedream_mcp.utils.io import io_path as path_utils


@pytest.mark.asyncio
async def test_prepare_image_input_cache_isolated_by_workspace_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspace_roots 变化时同一 image 输入不应命中缓存，底层 prepare 被重新调用。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)

    call_count = 0

    async def fake_prepare_image_input(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}"

    # 用对象式 monkeypatch 直接作用于模块对象，避免字符串路径解析在
    # seedream_mcp 顶层 __getattr__ lazy export 下受测试顺序影响
    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare_image_input)

    # 两次调用返回不同 roots，模拟不同请求上下文 / 租户
    roots_sequence: List[List[Path]] = [
        [Path("/workspace/tenant-a")],
        [Path("/workspace/tenant-b")],
    ]
    call_index: Dict[str, int] = {"i": 0}

    def fake_get_workspace_roots() -> List[Path]:
        idx = call_index["i"]
        call_index["i"] += 1
        return list(roots_sequence[idx % len(roots_sequence)])

    monkeypatch.setattr(path_utils, "get_workspace_roots", fake_get_workspace_roots)

    first = await client._image_preparer.prepare_image_input("same-image.png")
    second = await client._image_preparer.prepare_image_input("same-image.png")

    assert first == "prepared:same-image.png"
    assert second == "prepared:same-image.png"
    # workspace_roots 不同 → cache_key 不同 → 缓存未命中 → 底层被调用两次
    assert call_count == 2


@pytest.mark.asyncio
async def test_prepare_image_input_cache_hit_when_workspace_roots_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspace_roots 相同时，同一 image 第二次调用走缓存，底层仅调用一次。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)

    call_count = 0

    async def fake_prepare_image_input(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare_image_input)
    monkeypatch.setattr(path_utils, "get_workspace_roots", lambda: [Path("/workspace/same")])

    await client._image_preparer.prepare_image_input("img.png")
    await client._image_preparer.prepare_image_input("img.png")

    assert call_count == 1


def test_resolved_roots_cache_evicts_oldest_fifo(monkeypatch: pytest.MonkeyPatch) -> None:
    """roots 解析缓存超限时按 FIFO 淘汰最旧条目，上限恒定且最旧键先失效。

    淘汰经 OrderedDict.popitem(last=False) 单方法调用完成，规避「迭代取首键再
    pop」两步组合在并发写入下的 RuntimeError 逃逸。
    """
    from seedream_mcp.utils.images import image_prepare
    from seedream_mcp.utils.images.image_prepare import (
        _RESOLVED_ROOTS_CACHE_MAX_ENTRIES,
        _resolved_roots_cache,
    )
    from seedream_mcp.utils.images.image_prepare import ImagePreparer

    _resolved_roots_cache.clear()
    monkeypatch.setattr(image_prepare, "resolve_workspace_roots", lambda roots: [Path("/ws")])

    for i in range(_RESOLVED_ROOTS_CACHE_MAX_ENTRIES + 1):
        ImagePreparer._local_file_signature("local.png", (f"/ws/{i}",))

    assert len(_resolved_roots_cache) == _RESOLVED_ROOTS_CACHE_MAX_ENTRIES
    assert ("/ws/0",) not in _resolved_roots_cache
    assert ("/ws/1",) in _resolved_roots_cache
    assert (f"/ws/{_RESOLVED_ROOTS_CACHE_MAX_ENTRIES}",) in _resolved_roots_cache
    _resolved_roots_cache.clear()
