"""守护测试：ImagePreparer 预处理并发上限为进程级约束，单图与批量入口共用。

防止信号量退化为每次调用各建一份或仅覆盖批量入口：并发调用不共享同一信号量时，
全局并发随调用数放大，突破 SEEDREAM_IMAGE_PREPARE_CONCURRENCY 上限。
"""

import asyncio
from collections.abc import Callable
from typing import Any, cast

import pytest

from seedream_mcp.utils.core.loop_bound import LoopKeyedInflight, loop_bound_semaphore
from seedream_mcp.utils.images import prepare as image_prepare

from _lib.client_fakes import _make_client


def _prepare_semaphore(limit: int) -> asyncio.Semaphore:
    """取 preparer 实际使用的进程级信号量，供槽位计值断言。"""
    return loop_bound_semaphore(limit, key="image_prepare")


async def test_concurrent_parallel_calls_share_instance_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 preparer 上两个并发批量调用共享信号量，任意时刻并发 prepare 数不超过上限。"""
    client = _make_client()
    preparer = client._image_preparer
    limit = client.config.image_prepare_concurrency

    current = 0
    peak = 0
    call_count = 0
    arrival = 0
    release = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        nonlocal current, peak, call_count, arrival
        call_count += 1
        current += 1
        peak = max(peak, current)
        # 会合式放行：在途任务数到达并发上限即放行，不依赖 sleep 时序。
        arrival += 1
        if arrival == limit:
            release.set()
        try:
            await asyncio.wait_for(release.wait(), timeout=5)
        finally:
            current -= 1
        return f"prepared:{image}"

    # 对象式 monkeypatch：直接作用于模块对象，规避 utils __getattr__ 延迟加载
    monkeypatch.setattr(image_prepare, "prepare_image_input", fake_prepare)

    # 每批图片数超过上限：退化实现下两批各自的信号量上限会叠加突破全局上限
    batch_a = [f"https://example.com/a{i}.png" for i in range(limit + 1)]
    batch_b = [f"https://example.com/b{i}.png" for i in range(limit + 1)]

    results_a, results_b = await asyncio.gather(
        preparer.prepare_images_in_parallel(batch_a),
        preparer.prepare_images_in_parallel(batch_b),
    )

    assert peak <= limit, "并发批量调用共享的信号量不得突破配置的全局并发上限"
    assert call_count == 2 * (limit + 1)
    assert results_a == [f"prepared:{image}" for image in batch_a]
    assert results_b == [f"prepared:{image}" for image in batch_b]


async def test_concurrent_single_image_calls_share_instance_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发单图入口即 client 直连路径同样受共享信号量约束，峰值并发不超过上限。"""
    client = _make_client()
    preparer = client._image_preparer
    limit = client.config.image_prepare_concurrency

    current = 0
    peak = 0
    arrival = 0
    release = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        nonlocal current, peak, arrival
        current += 1
        peak = max(peak, current)
        # 会合式放行：在途任务数到达并发上限即放行，不依赖 sleep 时序。
        arrival += 1
        if arrival == limit:
            release.set()
        try:
            await asyncio.wait_for(release.wait(), timeout=5)
        finally:
            current -= 1
        return f"prepared:{image}"

    monkeypatch.setattr(image_prepare, "prepare_image_input", fake_prepare)

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
    client = _make_client()
    preparer = client._image_preparer
    limit = client.config.image_prepare_concurrency

    current = 0
    release = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        nonlocal current
        del image
        current += 1
        await release.wait()
        current -= 1
        return "prepared"

    monkeypatch.setattr(image_prepare, "prepare_image_input", fake_prepare)

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
    assert _prepare_semaphore(limit)._value == 0

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
    client = _make_client()
    preparer = client._image_preparer
    limit = client.config.image_prepare_concurrency

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

    monkeypatch.setattr(image_prepare, "prepare_image_input", fake_prepare)

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
        before = _prepare_semaphore(limit)._value
        await asyncio.sleep(0)
        settled = settled + 1 if _prepare_semaphore(limit)._value == before else 0
    assert _prepare_semaphore(limit)._value == limit


async def test_orphan_task_survives_gc_and_returns_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全部消费者放弃后，挂无强根 future 的共享 task 不被循环 GC 回收，槽位照常归还。

    注册表对在途条目仅弱持，若无强引用集保活，task 挂裸 future 即成孤立环，
    被 gc 整环回收后完成回调不再触发，经 transfer_to_task 转移的并发槽位
    永久丢失；以弱引用探测 future 存活，锁定 task 经循环强引用集保活。
    """
    import gc
    import weakref

    client = _make_client()
    preparer = client._image_preparer
    limit = client.config.image_prepare_concurrency
    gate: dict[str, weakref.ref[asyncio.Future[None]]] = {}

    async def parked_prepare(image: str) -> str:
        del image
        # future 仅由 task 自身持有，构造失去一切外部强根的等待点。
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        gate["future_ref"] = weakref.ref(future)
        await future
        return "prepared:gc-survivor"

    monkeypatch.setattr(image_prepare, "prepare_image_input", parked_prepare)

    creator = asyncio.ensure_future(
        preparer.prepare_image_input(
            "https://example.com/gc-survivor.png", scope_key=("test-roots",)
        )
    )
    # 轮询推进至共享 task 挂上裸 future，此后其脱离循环就绪队列的强持。
    for _ in range(1000):
        if "future_ref" in gate:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("等待超限：共享 task 未在 1000 次让出内挂上等待 future")

    creator.cancel()
    try:
        await creator
    except asyncio.CancelledError:
        pass

    # 排空就绪队列兜底：shield 取消回调的闭包强持共享 task，正常时序下 await creator 已将其跑完。
    for _ in range(4):
        await asyncio.sleep(0)

    gc.collect()
    future = gate["future_ref"]()
    assert future is not None, "在途共享 task 不得被循环 GC 整环回收"

    future.set_result(None)
    # 推进循环至共享 task 终结：完成回调清登记并归还经转移的并发槽位。
    for _ in range(1000):
        if len(preparer._prepare_inflight) == 0 and _prepare_semaphore(limit)._value == limit:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("等待超限：共享 task 终结后的登记清理与槽位归还未在 1000 次让出内出现")
    assert len(preparer._prepare_inflight) == 0
    assert _prepare_semaphore(limit)._value == limit


async def test_register_survives_event_loop_without_attribute_storage() -> None:
    """在途登记不向循环对象挂属性：无 __dict__ 的扩展循环（uvloop）上 setattr 即 AttributeError。

    以无 __dict__ 的循环代理与 get_loop 返回该代理的 task 替身模拟扩展循环，
    登记与完成回调清理全链路照常，嵌入宿主的首个并发单飞不再因登记失败。
    """

    class _NoDictLoop:
        # 声明槽位且不含 __dict__：非槽名 setattr 即 AttributeError，等价扩展循环；
        # 保留 __weakref__ 与真实循环一致，弱键索引可用。
        __slots__ = ("_proxied", "__weakref__")

        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._proxied = loop

        def __getattr__(self, name: str) -> Any:
            return getattr(self._proxied, name)

    class _LoopProxiedTask:
        # get_loop 返回无 __dict__ 代理、完成回调以替身身份触发的 task 替身。
        def __init__(self, task: "asyncio.Task[str]") -> None:
            self._task = task

        def get_loop(self) -> Any:
            return _NoDictLoop(self._task.get_loop())

        def add_done_callback(self, callback: "Callable[[Any], None]") -> None:
            def _trigger(finished: "asyncio.Task[str]") -> None:
                del finished
                callback(self)

            self._task.add_done_callback(_trigger)

    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def _pending() -> str:
        await gate
        return "done"

    task = asyncio.ensure_future(_pending())
    inflight: LoopKeyedInflight[str, str] = LoopKeyedInflight()
    # 修复前登记在向循环对象 setattr 强引用集处以 AttributeError 崩溃。
    entry, _created = inflight.register_if_absent(
        "no-dict-loop", lambda: cast("asyncio.Task[str]", _LoopProxiedTask(task))
    )
    assert inflight.peek("no-dict-loop") is entry

    gate.set_result(None)
    assert await task == "done"
    # 推进事件循环跑完完成回调，登记随完成回调清理。
    await asyncio.sleep(0)
    assert len(inflight) == 0


async def test_register_if_absent_hit_keeps_existing_entry_without_factory_call() -> None:
    """键被占用时 register_if_absent 返回既有条目且不调工厂，不顶替在途条目。"""
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def _pending() -> str:
        await gate
        return "first"

    registry: LoopKeyedInflight[str, str] = LoopKeyedInflight()
    entry, _created = registry.register_if_absent(
        "occupied", lambda: asyncio.ensure_future(_pending())
    )

    factory_calls = 0

    def _factory() -> "asyncio.Task[str]":
        nonlocal factory_calls
        factory_calls += 1
        return asyncio.ensure_future(_pending())

    shared, created = registry.register_if_absent("occupied", _factory)
    assert created is False
    assert shared is entry
    assert factory_calls == 0

    gate.set_result(None)
    assert await shared.consume() == "first"
    # 推进事件循环跑完完成回调，登记随完成回调清理。
    await asyncio.sleep(0)
    assert len(registry) == 0


async def test_register_if_absent_miss_registers_factory_task() -> None:
    """键空闲时 register_if_absent 经工厂创建登记，后到同键命中共享该条目。"""
    gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def _pending() -> str:
        await gate
        return "fresh"

    registry: LoopKeyedInflight[str, str] = LoopKeyedInflight()
    entry, created = registry.register_if_absent("free", lambda: asyncio.ensure_future(_pending()))
    assert created is True
    assert registry.peek("free") is entry

    second, second_created = registry.register_if_absent(
        "free", lambda: asyncio.ensure_future(_pending())
    )
    assert second_created is False
    assert second is entry

    gate.set_result(None)
    assert await second.consume() == "fresh"
    await asyncio.sleep(0)
    assert len(registry) == 0


def test_loop_keyed_inflight_public_registration_entrypoint_unique() -> None:
    """LoopKeyedInflight 公开面唯一注册入口为 register_if_absent，不暴露非原子登记。"""
    public_names = {name for name in dir(LoopKeyedInflight) if not name.startswith("_")}
    assert public_names == {"peek", "register_if_absent", "consume_shared"}


def test_semaphore_rebuilds_across_event_loops() -> None:
    """同一 preparer 跨事件循环依次使用时不因信号量绑定旧循环而报错。

    asyncio.Semaphore 首次使用绑定事件循环，跨循环复用抛 RuntimeError，须按循环
    身份重建；URL 输入的预处理无本地 I/O。
    """
    client = _make_client()
    preparer = client._image_preparer
    # 图片数超过上限一位，迫使信号量产生等待者
    batch = [
        f"https://example.com/x{i}.png" for i in range(client.config.image_prepare_concurrency + 1)
    ]

    async def _run_once() -> list[str]:
        return await preparer.prepare_images_in_parallel(batch)

    first = asyncio.run(_run_once())
    second = asyncio.run(_run_once())
    assert first == second == batch


def test_stale_inflight_entries_isolated_across_event_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧循环登记的在途条目在新循环上不拦截同键调用，按 miss 重新执行完成。

    旧循环的共享 task 绑定旧循环且随其关闭永不完成，残留死条目会使新循环的
    同键等待永久挂起；wait_for 超时把挂起转为失败而非拖垮测试。创建者取消后
    等待收尾使协程正常终结，旧循环仅遗留共享 task；关闭时以空异常处理器抑制
    未完成 task 的销毁告警。
    """
    client = _make_client()
    preparer = client._image_preparer
    image_url = "https://example.com/ref.png"
    roots_key = ("test-roots",)

    async def hanging_prepare(image: str) -> str:
        del image
        await asyncio.Event().wait()
        return "prepared:never"

    monkeypatch.setattr(image_prepare, "prepare_image_input", hanging_prepare)

    old_loop = asyncio.new_event_loop()
    old_loop.set_exception_handler(lambda loop, context: None)
    try:

        async def register_stale_entry() -> None:
            creator = asyncio.ensure_future(
                preparer.prepare_image_input(image_url, scope_key=roots_key)
            )
            while not preparer._prepare_inflight:
                await asyncio.sleep(0)
            # 取消并等待收尾：共享 task 经 shield 不受连带取消，继续挂在旧循环上。
            creator.cancel()
            try:
                await creator
            except asyncio.CancelledError:
                pass
            # 旧循环遗留一条死条目，随循环关闭永无完成回调清理。
            assert len(preparer._prepare_inflight) == 1

        old_loop.run_until_complete(register_stale_entry())
    finally:
        old_loop.close()

    async def succeeding_prepare(image: str) -> str:
        del image
        return "prepared:fresh"

    monkeypatch.setattr(image_prepare, "prepare_image_input", succeeding_prepare)

    async def rerun_on_new_loop() -> str:
        result = await asyncio.wait_for(
            preparer.prepare_image_input(image_url, scope_key=roots_key), timeout=2
        )
        # 新循环的注册表独立成表，调用完成后不残留条目。
        assert len(preparer._prepare_inflight) == 0
        return result

    assert asyncio.run(rerun_on_new_loop()) == "prepared:fresh"
