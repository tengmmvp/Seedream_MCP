"""守护测试：_prepare_image_input 对同一 cache_key 的并发 miss 复用在途任务。

防止 single-flight 去重退化的回归：当两个并发调用同时 miss 缓存时，必须共享
_prepare_inflight 中的同一 asyncio.Task，使底层 prepare_image_input 仅被调用一次。
若去重失效，并发请求会对同一参考图重复读取与编码，丧失该优化的核心价值。
"""

import asyncio

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.images import image_input


@pytest.mark.asyncio
async def test_prepare_image_input_concurrent_miss_shares_single_inflight_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 cache_key 的并发 miss 复用同一在途 task。

    底层 prepare_image_input 仅调用一次。
    """
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
        client._image_preparer.prepare_image_input(image_url, roots_key),
        client._image_preparer.prepare_image_input(image_url, roots_key),
    )

    # 两个并发 miss 共享同一 task，底层仅调用一次
    assert call_count == 1
    assert first == second == "prepared:https://example.com/ref.png"
    # 在途 task 完成后应被清理；HTTP URL 跳过缓存，缓存为空
    assert len(client._image_preparer._prepare_inflight) == 0
    assert len(client._image_preparer._prepare_cache) == 0


@pytest.mark.asyncio
async def test_prepare_image_input_creator_cancel_does_not_cancel_other_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建者 await task 被取消时仅退出自身，底层 inflight task 与其他等待者不受影响。

    single-flight 取消隔离契约：创建者被取消时，_prepare_and_cache task 应继续运行直至
    完成，_prepare_inflight 由 task 完成时的 finally 清理，保护共享同一 task 的其他
    等待者不被连带取消。两个并发调用经 ensure_future 同时启动；fake 置位事件后，FIFO
    调度下 creator 先创建 inflight 并 await task，waiter 随后命中 inflight 并 await 同一
    task。取消 creator 后断言 waiter 仍拿到结果、fake 仅调用一次；HTTP URL 跳过缓存。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    # 显式传入 roots_key，使 cache_key 不依赖工作区根目录上下文
    roots_key = ("test-roots",)

    call_count = 0
    inner_started = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        call_count += 1
        # 通知测试：creator 已创建 inflight task 并 await 它。FIFO 调度下此时 creator、
        # waiter 均已挂起在共享 task 上。fake 仅执行一次即证明二者复用同一 task。
        inner_started.set()
        await asyncio.sleep(0.1)
        return f"prepared:{image}"

    # 对象式 monkeypatch：直接作用于模块对象，规避 utils __getattr__ 延迟加载
    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    # URL 输入的 _local_file_signature 恒为 (0.0, 0)，两次 cache_key 完全一致
    image_url = "https://example.com/ref.png"
    creator = asyncio.ensure_future(
        client._image_preparer.prepare_image_input(image_url, roots_key)
    )
    waiter = asyncio.ensure_future(client._image_preparer.prepare_image_input(image_url, roots_key))

    # 等待底层 task 启动：creator 先创建 inflight 并 await task，waiter 随后命中 inflight
    # 并 await 同一 task，最后底层 task 执行 fake 置位事件。
    await inner_started.wait()
    assert len(client._image_preparer._prepare_inflight) == 1

    # 取消创建者：按契约仅退出其 await task，底层 inflight task 不应被连带取消。
    creator.cancel()

    # asyncio.wait 等待两个 task 终结：creator 因取消终结；waiter 因底层 task 完成终结。
    done, pending = await asyncio.wait({creator, waiter})
    assert pending == set()
    assert creator.cancelled()
    assert waiter in done

    # 第二个等待者仍从共享的 inflight task 拿到正确结果，不应被连带取消
    assert not waiter.cancelled(), "等待者不应被创建者取消连带取消"
    assert waiter.result() == "prepared:https://example.com/ref.png"
    # fake 底层仅调用一次：两个并发调用共享同一 task
    assert call_count == 1
    # task 完成后 _prepare_inflight 已清空；HTTP URL 跳过缓存，缓存为空
    assert len(client._image_preparer._prepare_inflight) == 0
    assert len(client._image_preparer._prepare_cache) == 0


@pytest.mark.asyncio
async def test_prepare_image_input_waiter_cancel_keeps_inflight_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """等待者被取消时仅退出自身；共享的 inflight task 继续运行至完成。

    single-flight 取消隔离契约的另一侧：等待者 await asyncio.shield(inflight) 被取消时，
    shield 仅取消其自身的外层 await，底层共享 task 不受连带取消，继续运行直至完成，
    _prepare_inflight 由 task 完成时的 finally 清理；HTTP URL 跳过缓存。两个并发调用经
    ensure_future 同时启动；fake 置位事件后，FIFO 调度下 creator 先创建 inflight 并
    await task，waiter 随后命中 inflight 并 await 同一 task。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    roots_key = ("test-roots",)

    call_count = 0
    inner_started = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        call_count += 1
        inner_started.set()
        await asyncio.sleep(0.1)
        return f"prepared:{image}"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    image_url = "https://example.com/ref.png"
    creator = asyncio.ensure_future(
        client._image_preparer.prepare_image_input(image_url, roots_key)
    )
    waiter = asyncio.ensure_future(client._image_preparer.prepare_image_input(image_url, roots_key))

    # 等待底层 task 启动：creator 先创建 inflight 并 await task，waiter 随后命中 inflight
    # 并 await 同一 task，最后底层 task 执行 fake 置位事件。
    await inner_started.wait()
    assert len(client._image_preparer._prepare_inflight) == 1

    # 取消等待者：shield 隔离使底层 inflight task 不受连带取消
    waiter.cancel()

    done, pending = await asyncio.wait({creator, waiter})
    assert pending == set()
    assert waiter.cancelled()

    # 创建者共享同一 inflight task，仍拿到正确结果；底层仅调用一次
    assert creator.result() == "prepared:https://example.com/ref.png"
    assert call_count == 1
    # inflight 完成后已清空；HTTP URL 跳过缓存，缓存为空
    assert len(client._image_preparer._prepare_inflight) == 0
    assert len(client._image_preparer._prepare_cache) == 0


@pytest.mark.asyncio
async def test_prepare_image_input_error_propagates_to_all_sharers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_prepare_and_cache 抛错时所有共享同一 inflight 的等待者收到该异常。

    且不写入缓存。single-flight 错误传播契约：底层 prepare_image_input 抛错时，
    共享同一 inflight task 的创建者与等待者均经 asyncio.shield 收到该异常；
    _prepare_inflight 由 task 完成时的 finally 清空；因异常在缓存写入前抛出，
    _prepare_cache 不写入任何条目。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    roots_key = ("test-roots",)

    call_count = 0
    inner_started = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        del image
        call_count += 1
        inner_started.set()
        await asyncio.sleep(0.05)
        raise ValueError("prepare failed")

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    image_url = "https://example.com/ref.png"
    creator = asyncio.ensure_future(
        client._image_preparer.prepare_image_input(image_url, roots_key)
    )
    waiter = asyncio.ensure_future(client._image_preparer.prepare_image_input(image_url, roots_key))

    await inner_started.wait()
    # 两者 await 同一 inflight task，task 抛错时均收到该异常
    done, pending = await asyncio.wait({creator, waiter})
    assert pending == set()

    for task in (creator, waiter):
        exc = task.exception()
        assert isinstance(exc, ValueError)
        assert "prepare failed" in str(exc)

    # 底层仅调用一次；task 完成后 inflight 已清空；出错不写入缓存
    assert call_count == 1
    assert len(client._image_preparer._prepare_inflight) == 0
    assert len(client._image_preparer._prepare_cache) == 0
