"""守护测试：ImagePreparer 预处理并发上限为实例级约束，单图与批量入口共用。

防止信号量退化为每次调用各建一份或仅覆盖批量入口：并发调用共享同一 preparer，
全局并发随调用数放大，突破 SEEDREAM_IMAGE_PREPARE_CONCURRENCY 上限。
"""

import asyncio

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.images import image_input


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


async def test_concurrent_single_image_calls_share_instance_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发单图入口即 client 直连路径同样受实例级信号量约束，峰值并发不超过上限。"""
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


async def test_waiters_do_not_occupy_semaphore_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中在途 task 的等待者在槽外等待，不占用预处理并发槽位。

    等待路径曾与执行路径共用槽位，纯等待者即可占满槽位使吞吐塌缩；不占槽后，
    其他键的执行者仍可用满 limit 个槽位。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    preparer = client._image_preparer
    limit = config.image_prepare_concurrency

    current = 0
    release = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        nonlocal current
        del image
        current += 1
        await release.wait()
        current -= 1
        return "prepared"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    shared_url = "https://example.com/shared.png"
    creator = asyncio.ensure_future(preparer.prepare_image_input(shared_url))
    # 等待在途 task 已登记且创建者持有的槽位进入 fake_prepare
    for _ in range(1000):
        if len(preparer._prepare_inflight) >= 1 and current >= 1:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("等待超限：在途 task 登记与创建者进入预处理未在 1000 次让出内出现")

    # limit + 3 个并发调用命中同一在途 task，成为纯等待者，数量刻意超过并发上限
    waiters = [
        asyncio.ensure_future(preparer.prepare_image_input(shared_url)) for _ in range(limit + 3)
    ]

    # 其他键的执行者，可用满剩余 limit - 1 个槽位
    others = [
        asyncio.ensure_future(preparer.prepare_image_input(f"https://example.com/other-{i}.png"))
        for i in range(limit - 1)
    ]

    for _ in range(1000):
        if current >= limit:
            break
        await asyncio.sleep(0)

    assert current == limit, "等待者不得占用并发槽位，空闲槽位须全部可供执行者使用"
    assert preparer._get_prepare_semaphore()._value == 0

    release.set()
    results = await asyncio.gather(creator, *waiters, *others)

    assert results == ["prepared"] * (2 * limit + 3)


async def test_cancelled_creators_do_not_break_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """串行取消创建者留下的脱缰 task 持续占用并发槽位，峰值并发不超过上限。

    创建者被取消时 shield 使共享 task 继续运行；若槽位随之释放，峰值并发随取消
    次数无界放大。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    preparer = client._image_preparer
    limit = config.image_prepare_concurrency

    current = 0
    peak = 0
    entered = 0
    release = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        nonlocal current, peak, entered
        del image
        entered += 1
        current += 1
        peak = max(peak, current)
        await release.wait()
        current -= 1
        return "prepared"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    # 串行发起 3 个请求并各自取消创建者：取消不传播到共享 task，3 个脱缰 task 继续在途。
    # 以 _prepare_inflight 条目数判定创建者已挂起在 shield 等待点，避免依赖时序。
    for index in range(3):
        creator = asyncio.ensure_future(
            preparer.prepare_image_input(f"https://example.com/cancelled-{index}.png")
        )
        for _ in range(1000):
            if len(preparer._prepare_inflight) >= index + 1:
                break
            await asyncio.sleep(0)
        else:
            pytest.fail(f"等待超限：第 {index} 个创建者的在途 task 未在 1000 次让出内登记")
        creator.cancel()
        with pytest.raises(asyncio.CancelledError):
            await creator

    # 等待脱缰 task 全部进入 fake_prepare，确保它们计入并发观测。
    for _ in range(1000):
        if entered >= 3:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("等待超限：脱缰 task 未在 1000 次让出内全部进入预处理")

    # 发起满额新请求：修复前 3+limit 个任务同时进入 fake_prepare，修复后仅 limit-3
    # 个新请求进入，其余阻塞在信号量。
    followers = [
        asyncio.ensure_future(
            preparer.prepare_image_input(f"https://example.com/follower-{index}.png")
        )
        for index in range(limit)
    ]
    settled = 0
    while settled < 4:
        before = entered
        await asyncio.sleep(0)
        settled = settled + 1 if entered == before else 0

    assert current <= limit, "取消产生的脱缰 task 不得突破配置的全局并发上限"
    assert peak <= limit

    release.set()
    results = await asyncio.gather(*followers)
    assert results == ["prepared"] * limit
    # 推进事件循环至脱缰 task 终结且回调执行完毕，再校验槽位未泄漏也未重复释放。
    settled = 0
    while settled < 4:
        before = preparer._get_prepare_semaphore()._value
        await asyncio.sleep(0)
        settled = settled + 1 if preparer._get_prepare_semaphore()._value == before else 0
    assert preparer._get_prepare_semaphore()._value == limit


def test_instance_semaphore_rebuilds_across_event_loops() -> None:
    """同一 preparer 跨事件循环依次使用时不因信号量绑定旧循环而报错。

    asyncio.Semaphore 首次使用绑定事件循环，跨循环复用抛 RuntimeError，须按循环
    身份重建；URL 输入的预处理无本地 I/O。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    preparer = client._image_preparer
    # 图片数超过上限一位，迫使信号量产生等待者
    batch = [f"https://example.com/x{i}.png" for i in range(config.image_prepare_concurrency + 1)]

    async def _run_once() -> list[str]:
        return await preparer.prepare_images_in_parallel(batch)

    first = asyncio.run(_run_once())
    second = asyncio.run(_run_once())
    assert first == second == batch
