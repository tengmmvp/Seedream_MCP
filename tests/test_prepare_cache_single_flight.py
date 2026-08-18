"""守护测试：_prepare_image_input 对同一 cache_key 的并发 miss 复用在途任务。

防止 single-flight 去重退化的回归：当两个并发调用同时 miss 缓存时，必须共享
_prepare_inflight 中的同一 asyncio.Task，使底层 prepare_image_input 仅被调用一次。
若去重失效，并发请求会对同一参考图重复读取与编码，丧失该优化的核心价值。
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

    arm_unretrieved_exception_logging 登记回调时经 logs 模块全局解析目标函数，对象式
    遮蔽即生效。替身检索异常保持 "Task exception was never retrieved" 静默。返回已
    触发回调的 task 列表，供断言登记时序。
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

    供兜底日志用例以真实回调链路观测记录次数：monkeypatch logs.logger 后，
    log_unretrieved_task_exception 的 warning 全部落入本替身，opt 携带的堆栈
    参数被丢弃，只断言消息与次数。
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


@pytest.mark.asyncio
async def test_prepare_failure_consumed_by_waiters_not_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共享 inflight 抛错且由等待者正常消费时不登记"未取回异常"回调。

    常规失败路径的异常经 shield 交还等待者、由调用方错误通道记录；若仍无条件挂
    回调，同一异常会以"后台共享任务失败"重复入日志。回调仅在消费方放弃等待时登记。
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_prepare_rechecks_cache_after_semaphore_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """获取信号量后的缓存复查：等待窗口内先完成者已写缓存时，获槽者直接命中。

    并发满载时后到者在信号量上排队，等待期间同键先完成者已写入缓存并清在途
    登记；获槽后若只复查在途注册表，后到者会重复执行全量读盘与编码。以持槽
    阻塞构造等待窗口，窗口内预置缓存条目，断言获槽后底层预处理不再执行。
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


@pytest.mark.asyncio
async def test_waiter_cancel_then_creator_consumes_failure_no_fallback_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """等待者放弃后创建者仍消费失败：不登记兜底回调，异常仅经既有错误通道入日志。

    旧缺陷：等待者取消即登记 done 回调，回调只要 task.exception() 非 None 就记录
    warning，无法感知该异常随后被创建者经 shield 正常消费，同一失败带堆栈两次
    进入日志。消费者计数下，等待者放弃时创建者仍持有计数，不登记兜底；创建者
    消费异常后计数归零且结果已送抵消费者一侧，亦不登记。
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


@pytest.mark.asyncio
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
