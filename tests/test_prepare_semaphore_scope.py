"""守护测试：ImagePreparer 预处理并发上限为实例级约束，单图与批量入口共用。

防止信号量退化为每次调用各建一份或仅覆盖批量入口的回归：并行生成与
streamable-http 并发工具调用共享同一 preparer 时，若每个批量调用各持独立信号量，
或单图直连调用不经信号量，全局并发随调用数线性放大，突破
SEEDREAM_IMAGE_PREPARE_CONCURRENCY 声明的全局上限。
"""

import asyncio

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.images import image_input


@pytest.mark.asyncio
async def test_concurrent_parallel_calls_share_instance_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 preparer 上两个并发批量调用共享信号量，任意时刻并发 prepare 数不超过上限。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    preparer = client._image_preparer
    limit = config.image_prepare_concurrency

    current = 0
    peak = 0
    call_count = 0

    async def fake_prepare(image: str) -> str:
        nonlocal current, peak, call_count
        call_count += 1
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.02)
        current -= 1
        return f"prepared:{image}"

    # 对象式 monkeypatch：直接作用于模块对象，规避 utils __getattr__ 延迟加载
    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    # 每批图片数超过上限：退化实现下两批各自的信号量上限会叠加突破全局上限
    batch_a = [f"https://example.com/a{i}.png" for i in range(limit + 1)]
    batch_b = [f"https://example.com/b{i}.png" for i in range(limit + 1)]

    results_a, results_b = await asyncio.gather(
        preparer.prepare_images_in_parallel(batch_a),
        preparer.prepare_images_in_parallel(batch_b),
    )

    assert peak <= limit, "并发批量调用共享的实例信号量不得突破配置的全局并发上限"
    assert call_count == 2 * (limit + 1)
    assert results_a == [f"prepared:{image}" for image in batch_a]
    assert results_b == [f"prepared:{image}" for image in batch_b]


@pytest.mark.asyncio
async def test_concurrent_single_image_calls_share_instance_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发单图入口（client 直连路径）同样受实例级信号量约束，峰值并发不超过上限。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    preparer = client._image_preparer
    limit = config.image_prepare_concurrency

    current = 0
    peak = 0

    async def fake_prepare(image: str) -> str:
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.02)
        current -= 1
        return f"prepared:{image}"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    # 并发单图调用数数倍于上限：若信号量仅在批量入口生效，峰值会随调用数放大
    images = [f"https://example.com/s{i}.png" for i in range(limit * 3)]

    results = await asyncio.gather(*(preparer.prepare_image_input(image) for image in images))

    assert peak <= limit, "并发单图调用不得突破配置的全局并发上限"
    assert results == [f"prepared:{image}" for image in images]


def test_instance_semaphore_rebuilds_across_event_loops() -> None:
    """同一 preparer 跨事件循环依次使用时不因信号量绑定旧循环而报错。

    asyncio.Semaphore 首次使用时绑定事件循环，跨循环复用会抛 RuntimeError；实例
    信号量须以循环身份守卫按需重建。URL 输入的预处理为纯校验路径，无本地 I/O。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    preparer = client._image_preparer
    # 图片数超过上限一位，迫使信号量产生等待者：等待 future 在创建时绑定事件循环，
    # 无守卫的实例信号量在第二次 asyncio.run 会因跨循环复用抛 RuntimeError
    batch = [f"https://example.com/x{i}.png" for i in range(config.image_prepare_concurrency + 1)]

    async def _run_once() -> list[str]:
        return await preparer.prepare_images_in_parallel(batch)

    first = asyncio.run(_run_once())
    second = asyncio.run(_run_once())
    assert first == second == batch
