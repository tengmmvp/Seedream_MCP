"""守护测试：_prepare_image_input 对同一 cache_key 的并发 miss 复用同一 asyncio.Task。

防止 single-flight 去重退化的回归：当两个并发调用同时 miss 缓存时，必须共享同一在途
task（_prepare_inflight），底层 prepare_image_input 仅被调用一次。若去重失效，并发
请求会对同一参考图重复读取与编码，丧失该优化的核心价值。
"""

import asyncio

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils import image_input


@pytest.mark.asyncio
async def test_prepare_image_input_concurrent_miss_shares_single_inflight_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 cache_key 的并发 miss 复用同一在途 task，底层 prepare_image_input 仅调用一次。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    # 显式传入 roots_key，使 cache_key 不依赖工作区根目录上下文
    roots_key = ("test-roots",)

    call_count = 0

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        call_count += 1
        # 让首个调用挂起足够久，确保第二个并发调用能观察到在途 task
        await asyncio.sleep(0.05)
        return f"prepared:{image}"

    # 对象式 monkeypatch：直接作用于模块对象，规避 utils __getattr__ 延迟加载
    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    # URL 输入的 _local_file_signature 恒为 (0.0, 0)，两次 cache_key 完全一致
    image_url = "https://example.com/ref.png"
    first, second = await asyncio.gather(
        client._prepare_image_input(image_url, roots_key),
        client._prepare_image_input(image_url, roots_key),
    )

    # 两个并发 miss 共享同一 task，底层仅调用一次
    assert call_count == 1
    assert first == second == "prepared:https://example.com/ref.png"
    # 在途 task 完成后应被清理；缓存写入一条结果
    assert len(client._prepare_inflight) == 0
    assert len(client._prepare_cache) == 1
