"""守护测试：_prepare_image_input 对同一 cache_key 的并发 miss 复用在途任务。

并发 miss 须共享 _prepare_inflight 中的同一 asyncio.Task，使底层
prepare_image_input 仅被调用一次。
"""

import asyncio
from typing import Any

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.images import image_input


def _patch_unretrieved_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> list["asyncio.Task[Any]"]:
    """把 logs.log_unretrieved_task_exception 替换为记录 task 并检索异常的替身。

    回调经 logs 模块全局解析，对象式遮蔽即生效；替身检索异常避免 "Task exception
    was never retrieved" 噪声。返回已触发回调的 task 列表。
    """
    from seedream_mcp.utils.core import logs

    fired: list[asyncio.Task[Any]] = []

    def record(task: asyncio.Task[Any]) -> None:
        fired.append(task)
        if not task.cancelled():
            task.exception()

    monkeypatch.setattr(logs, "log_unretrieved_task_exception", record)
    return fired


class _WarningCapture:
    """捕获 warning 调用的 loguru 替身，按模板参数格式化后记录消息文本。

    monkeypatch logs.logger 后兜底 warning 落入本替身，bind/opt 的附加参数被
    丢弃，供用例断言消息与次数。
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def bind(self, **kwargs: Any) -> "_WarningCapture":
        # 客户端构造会经 get_logger 调 bind，替身须容忍该链路
        del kwargs
        return self

    def opt(self, *args: Any, **kwargs: Any) -> "_WarningCapture":
        del args, kwargs
        return self

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append(message.format(*args))


async def test_prepare_image_input_concurrent_miss_shares_single_inflight_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 cache_key 的并发 miss 复用同一在途 task，底层仅调用一次。"""
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

    assert call_count == 1
    assert first == second == "prepared:https://example.com/ref.png"
    # 在途 task 完成后清空在途登记；HTTP URL 跳过缓存，缓存为空
    assert len(client._image_preparer._prepare_inflight) == 0
    assert len(client._image_preparer._prepare_cache) == 0


async def test_prepare_image_input_creator_cancel_does_not_cancel_other_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建者 await task 被取消时仅退出自身，底层 inflight task 与其他等待者不受影响。

    _prepare_inflight 由 task 完成时的 finally 清理，保护共享同一 task 的等待者
    不被连带取消。
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
        # 置位时 creator 与 waiter 均已挂起在共享 task 上，fake 仅执行一次即证明复用。
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

    # 等待底层 task 启动，此时 creator 与 waiter 均已挂起在共享 task 上。
    await inner_started.wait()
    assert len(client._image_preparer._prepare_inflight) == 1

    # 取消创建者，底层 inflight task 不受连带取消。
    creator.cancel()

    # 等待两个 task 终结。
    done, pending = await asyncio.wait({creator, waiter})
    assert pending == set()
    assert creator.cancelled()
    assert waiter in done

    # 等待者仍从共享 inflight task 拿到结果，不被连带取消
    assert not waiter.cancelled(), "等待者不应被创建者取消连带取消"
    assert waiter.result() == "prepared:https://example.com/ref.png"
    assert call_count == 1
    # task 完成后 _prepare_inflight 已清空；HTTP URL 跳过缓存，缓存为空
    assert len(client._image_preparer._prepare_inflight) == 0
    assert len(client._image_preparer._prepare_cache) == 0


async def test_prepare_image_input_waiter_cancel_keeps_inflight_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """等待者被取消时仅退出自身；共享的 inflight task 继续运行至完成。

    等待者 await asyncio.shield(inflight)，取消仅作用于其自身的外层 await，
    底层 task 由完成时的 finally 清理在途登记。
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

    # 等待底层 task 启动，此时 creator 与 waiter 均已挂起在共享 task 上。
    await inner_started.wait()
    assert len(client._image_preparer._prepare_inflight) == 1

    # 取消等待者：shield 隔离使底层 inflight task 不受连带取消
    waiter.cancel()

    done, pending = await asyncio.wait({creator, waiter})
    assert pending == set()
    assert waiter.cancelled()

    # 创建者仍从共享 inflight task 拿到正确结果
    assert creator.result() == "prepared:https://example.com/ref.png"
    assert call_count == 1
    # inflight 完成后已清空；HTTP URL 跳过缓存，缓存为空
    assert len(client._image_preparer._prepare_inflight) == 0
    assert len(client._image_preparer._prepare_cache) == 0


async def test_prepare_image_input_error_propagates_to_all_sharers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """底层抛错时所有共享同一 inflight 的等待者收到该异常，且不写入缓存。

    异常在缓存写入前抛出，_prepare_inflight 由 task 完成时的 finally 清空。
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


async def test_prepare_failure_consumed_by_waiters_not_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共享 inflight 抛错且由等待者正常消费时不登记「未取回异常」回调。

    回调仅在消费方放弃等待时登记，否则同一异常会重复入日志。
    """
    fired = _patch_unretrieved_callback(monkeypatch)

    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    roots_key = ("test-roots",)
    inner_started = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        del image
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
    done, pending = await asyncio.wait({creator, waiter})
    assert pending == set()
    for task in done:
        assert isinstance(task.exception(), ValueError)

    # 推进事件循环跑完可能排队的 done callback 后仍无回调触发
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fired == []


async def test_prepare_creator_cancel_arms_unretrieved_logging_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建者被取消且无其他等待者时，inflight 失败经登记的回调检索且仅触发一次。"""
    fired = _patch_unretrieved_callback(monkeypatch)

    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    roots_key = ("test-roots",)
    inner_started = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        del image
        inner_started.set()
        await asyncio.sleep(0.05)
        raise ValueError("prepare failed")

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    image_url = "https://example.com/ref.png"
    creator = asyncio.ensure_future(
        client._image_preparer.prepare_image_input(image_url, roots_key)
    )
    await inner_started.wait()
    inflight = next(iter(client._image_preparer._prepare_inflight.values())).task
    creator.cancel()

    done, pending = await asyncio.wait({creator, inflight})
    assert pending == set()
    assert done == {creator, inflight}
    assert creator.cancelled()
    # 推进事件循环跑完 inflight 完成时排队的 done callback
    await asyncio.sleep(0)

    assert fired == [inflight]
    assert isinstance(inflight.exception(), ValueError)


async def test_prepare_rechecks_cache_after_semaphore_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """获取信号量后的缓存复查：等待窗口内先完成者已写缓存时，获槽者直接命中。

    等待期间同键先完成者已写入缓存并清在途登记，获槽后若只查在途注册表会
    重复执行读盘与编码。
    """
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    preparer = client._image_preparer
    roots_key = ("test-roots",)

    call_count = 0

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        del image
        call_count += 1
        return "prepared:unexpected"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    # 预置实例级信号量为单槽并由本测试持槽，构造后到者在 acquire 上排队的窗口
    semaphore = asyncio.Semaphore(1)
    preparer._prepare_semaphore = semaphore
    preparer._prepare_semaphore_loop = asyncio.get_running_loop()
    await semaphore.acquire()

    image_data_uri = "data:image/png;base64,aGVsbG8="
    late = asyncio.ensure_future(preparer.prepare_image_input(image_data_uri, roots_key))
    # 推进事件循环：后到者完成键计算并挂起在信号量 acquire 上，尚未登记在途 task
    await asyncio.sleep(0)
    assert len(preparer._prepare_inflight) == 0

    # 等待窗口内同键先完成者写入缓存并清在途登记
    preparer._prepare_cache[(image_data_uri, roots_key, (0.0, 0))] = "prepared:cached"

    semaphore.release()
    assert await late == "prepared:cached"
    # 缓存复查命中后不再执行底层预处理，也不登记在途条目
    assert call_count == 0
    assert len(preparer._prepare_inflight) == 0


async def test_waiter_cancel_then_creator_consumes_failure_no_fallback_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """等待者放弃后创建者仍消费失败：不登记兜底回调，异常仅经既有错误通道入日志。

    旧行为：等待者取消即登记回调，同一失败带堆栈两次进入日志；消费者计数下
    创建者仍持有计数，不登记兜底。
    """
    from seedream_mcp.utils.core import logs

    capture = _WarningCapture()
    monkeypatch.setattr(logs, "logger", capture)

    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    roots_key = ("test-roots",)
    inner_started = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        del image
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
    waiter.cancel()

    done, pending = await asyncio.wait({creator, waiter})
    assert pending == set()
    assert waiter.cancelled()
    # 创建者经 shield 收到该异常，异常由其调用侧的既有错误通道负责记录
    assert isinstance(creator.exception(), ValueError)

    # 推进事件循环跑完可能排队的 done callback 后，兜底日志为零条
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert capture.warnings == []


async def test_all_consumers_abandon_failure_logs_fallback_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建者独自放弃且无等待者接手时，孤儿失败经兜底回调入日志且恰好一次。"""
    from seedream_mcp.utils.core import logs

    capture = _WarningCapture()
    monkeypatch.setattr(logs, "logger", capture)

    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    roots_key = ("test-roots",)
    inner_started = asyncio.Event()

    async def fake_prepare(image: str) -> str:
        del image
        inner_started.set()
        await asyncio.sleep(0.05)
        raise ValueError("prepare failed")

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    image_url = "https://example.com/ref.png"
    creator = asyncio.ensure_future(
        client._image_preparer.prepare_image_input(image_url, roots_key)
    )
    await inner_started.wait()
    inflight = next(iter(client._image_preparer._prepare_inflight.values())).task
    creator.cancel()

    done, pending = await asyncio.wait({creator, inflight})
    assert pending == set()
    assert creator.cancelled()

    # 推进事件循环跑完 inflight 完成时排队的 done callback
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # 同一孤儿失败仅一条兜底 warning，不因重复登记而多次记录
    assert capture.warnings == ["后台共享任务失败: prepare failed"]
