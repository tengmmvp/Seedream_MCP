"""并行批次请求体共享语义测试。

同一工具调用的多个并行请求经共享请求计划只构建一次 request_data、只序列化一次
body，各请求复用同一 bytes 对象；批次结束后计划释放，跨调用互不共享。网络层经
monkeypatch 注入，不触达真实 API。
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any

import httpx
import pytest

from _lib.cpu_offload_spy import CpuOffloadSpy

from seedream_mcp.client import SeedreamClient
from seedream_mcp.client.request_plan import SharedRequestPlan, shared_request_plan_scope
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.schemas import TextToImageInput
from seedream_mcp.tools.handlers.text_to_image import handle_text_to_image
from seedream_mcp.utils.core.errors import (
    SeedreamValidationError,
    format_error_for_user,
    resolve_error_profile,
)
from seedream_mcp.utils.core.inflight import InflightTaskCancelledError

import seedream_mcp.client._api as client_module


def _build_config() -> SeedreamConfig:
    # 关闭自动保存：测试 mock 返回占位 URL，避免对 example.com 发起真实下载拖慢 CI
    return SeedreamConfig(api_key="test_key", max_retries=1, auto_save_enabled=False)


def _install_serialize_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """在类上替换 _serialize_request 为计数替身，返回计数器字典。"""
    calls = {"serialize": 0}
    original = SeedreamClient._serialize_request

    def _spy(request_data: dict[str, Any]) -> bytes:
        calls["serialize"] += 1
        return original(request_data)

    monkeypatch.setattr(SeedreamClient, "_serialize_request", staticmethod(_spy))
    return calls


def _install_build_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """在类上替换 _build_common_request 为计数替身，返回计数器字典。"""
    calls = {"build": 0}
    original = SeedreamClient._build_common_request

    def _spy(self: Any, **kwargs: Any) -> dict[str, Any]:
        calls["build"] += 1
        return original(self, **kwargs)

    monkeypatch.setattr(SeedreamClient, "_build_common_request", _spy)
    return calls


def _install_send_capture(monkeypatch: pytest.MonkeyPatch, bodies: list[bytes]) -> None:
    """在类上替换 _send_standard_request，记录每个请求实际发送的 body 对象。"""

    async def fake_send(
        self: Any,
        *,
        client: Any,
        url: str,
        request_body: bytes,
        request_timeout: Any,
        attempt_timeout_seconds: float,
    ) -> dict[str, Any]:
        del self, client, url, request_timeout, attempt_timeout_seconds
        bodies.append(request_body)
        # 在途窗口：让同批请求先后进入序列化阶段再完成，模拟真实网络并发
        await asyncio.sleep(0)
        return {
            "success": True,
            "data": [{"url": f"https://example.com/{len(bodies)}.png"}],
            "usage": {},
            "status": "completed",
        }

    monkeypatch.setattr(SeedreamClient, "_send_standard_request", fake_send)


async def test_parallel_batch_builds_and_serializes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 个并行请求恰好构建一次、序列化一次，且 4 份发送复用同一 bytes 对象。"""
    serialize_calls = _install_serialize_spy(monkeypatch)
    build_calls = _install_build_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    result = await handle_text_to_image(
        TextToImageInput(prompt="parallel", request_count=4, parallelism=4),
        _build_config(),
    )

    assert result.is_error is False
    # 4 个请求都真实发出，共享 body 不代表去重 API 调用
    assert len(sent_bodies) == 4
    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1
    # 各请求复用同一 bytes 对象，而非各持一份等大拷贝
    assert all(body is sent_bodies[0] for body in sent_bodies)
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["batch"]["success_requests"] == 4


async def test_staggered_parallel_batch_still_serializes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parallelism 小于 request_count 的错峰批次同样只序列化一次。

    共享计划在批次 gather 结束前不释放，不得退化为重复构建与序列化。
    """
    serialize_calls = _install_serialize_spy(monkeypatch)
    build_calls = _install_build_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    result = await handle_text_to_image(
        TextToImageInput(prompt="staggered", request_count=4, parallelism=2),
        _build_config(),
    )

    assert result.is_error is False
    assert len(sent_bodies) == 4
    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1
    assert all(body is sent_bodies[0] for body in sent_bodies)


async def test_single_request_path_behavior_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单请求路径行为不变：构建一次、序列化一次、发送一份 body。"""
    serialize_calls = _install_serialize_spy(monkeypatch)
    build_calls = _install_build_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    result = await handle_text_to_image(TextToImageInput(prompt="single"), _build_config())

    assert result.is_error is False
    assert len(sent_bodies) == 1
    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1


async def test_consecutive_calls_do_not_share_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批次结束即释放计划：两次独立调用各自序列化，不命中上一批次的陈旧 body。"""
    serialize_calls = _install_serialize_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    first = await handle_text_to_image(TextToImageInput(prompt="first"), _build_config())
    second = await handle_text_to_image(TextToImageInput(prompt="second"), _build_config())

    assert first.is_error is False
    assert second.is_error is False
    assert serialize_calls["serialize"] == 2
    assert len(sent_bodies) == 2
    # 不同批次的 body 是各自序列化的独立对象，内容亦随 prompt 不同
    assert sent_bodies[0] is not sent_bodies[1]
    assert sent_bodies[0] != sent_bodies[1]


async def test_direct_client_call_without_plan_serializes_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未绑定共享计划的直连 client 调用走独立构建与序列化路径，公共 API 行为不变。"""
    serialize_calls = _install_serialize_spy(monkeypatch)
    build_calls = _install_build_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    async with SeedreamClient(_build_config()) as client:
        await client.text_to_image(prompt="direct")

    assert serialize_calls["serialize"] == 1
    assert build_calls["build"] == 1
    assert len(sent_bodies) == 1


def _install_serialize_thread_spy(monkeypatch: pytest.MonkeyPatch) -> CpuOffloadSpy:
    """在类上包装 _serialize_request 为线程名记录 spy，返回 spy 实例。"""
    spy = CpuOffloadSpy(SeedreamClient._serialize_request)
    monkeypatch.setattr(SeedreamClient, "_serialize_request", staticmethod(spy))
    return spy


async def test_direct_path_large_body_serialization_runs_in_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直连路径的大载荷出站序列化落 CPU 卸载专用池线程。"""
    spy = _install_serialize_thread_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    large_payload = {"prompt": "A" * (4 * 1024 * 1024)}
    async with SeedreamClient(_build_config()) as client:
        result = await client._call_api("text_to_image", large_payload)

    assert result["success"] is True
    assert len(sent_bodies) == 1
    spy.assert_ran_in_cpu_pool()


async def test_direct_path_cjk_body_over_byte_threshold_runs_in_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CJK 载荷按 UTF-8 字节口径判下沉：字符数低于阈值而字节数达阈值仍卸载。"""
    spy = _install_serialize_thread_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    cjk_payload = {"prompt": "汉" * 40_000}
    async with SeedreamClient(_build_config()) as client:
        result = await client._call_api("text_to_image", cjk_payload)

    assert result["success"] is True
    assert len(sent_bodies) == 1
    spy.assert_ran_in_cpu_pool()


async def test_shared_plan_large_body_serialization_runs_in_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共享计划路径的大载荷出站序列化同样落 CPU 卸载专用池线程。"""
    spy = _install_serialize_thread_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    large_payload = {"prompt": "B" * (4 * 1024 * 1024)}
    async with SeedreamClient(_build_config()) as client:
        with shared_request_plan_scope():
            result = await client._call_api("text_to_image", large_payload)

    assert result["success"] is True
    assert len(sent_bodies) == 1
    spy.assert_ran_in_cpu_pool()


async def test_direct_path_small_body_serialization_runs_outside_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """低于下沉阈值的出站序列化在调用线程同步完成，不付线程往返开销。"""
    spy = _install_serialize_thread_spy(monkeypatch)
    sent_bodies: list[bytes] = []
    _install_send_capture(monkeypatch, sent_bodies)

    async with SeedreamClient(_build_config()) as client:
        result = await client._call_api("text_to_image", {"prompt": "small"})

    assert result["success"] is True
    assert len(sent_bodies) == 1
    spy.assert_ran_outside_cpu_pool()


# ==================== 共享计划失败路径 ====================


def _count_traceback_frames(exc: BaseException) -> int:
    """统计异常 traceback 的帧数，供断言共享异常对象不随重抛增长。"""
    return len(traceback.extract_tb(exc.__traceback__))


async def test_shared_plan_builder_failure_cached_and_replayed() -> None:
    """builder 抛错时首个异常被缓存并重放给同键后到者，构建只执行一次。"""
    plan = SharedRequestPlan()
    builder_failure = RuntimeError("prepare failed")
    attempts = {"count": 0}

    async def failing_builder() -> dict[str, Any]:
        attempts["count"] += 1
        await asyncio.sleep(0)
        raise builder_failure

    outcomes = await asyncio.gather(
        *(plan.get_or_build("text_to_image", failing_builder) for _ in range(3)),
        return_exceptions=True,
    )

    # 锁内执行 builder 的首个到者收原异常，其余后到者收同型同文案的独立副本
    assert sum(outcome is builder_failure for outcome in outcomes) == 1
    assert len({id(outcome) for outcome in outcomes}) == len(outcomes)
    assert all(type(outcome) is RuntimeError for outcome in outcomes)
    assert all(str(outcome) == "prepare failed" for outcome in outcomes)
    # 构建只执行一次：确定性失败重试只会重复读盘与解码等全量副作用
    assert attempts["count"] == 1
    # 计划未写入失败产物
    assert plan.request_data is None

    # 计划存续期内后到的同键构建请求同样被缓存异常短路
    async def working_builder() -> dict[str, Any]:
        return {"model": "m"}

    frames_before_replay = _count_traceback_frames(builder_failure)
    replayed: BaseException | None = None
    try:
        await plan.get_or_build("text_to_image", working_builder)
    except Exception as exc:
        replayed = exc

    # 后到者收独立副本，类型与文案一致
    assert replayed is not builder_failure
    assert type(replayed) is type(builder_failure)
    assert str(replayed) == str(builder_failure)
    assert attempts["count"] == 1
    # 重抛不向共享异常对象的 traceback 追加帧
    assert _count_traceback_frames(builder_failure) == frames_before_replay

    # 失败缓存随 release 清空，同一计划重新构建并成功写入
    plan.release()

    built = await plan.get_or_build("text_to_image", working_builder)

    assert built == {"model": "m"}
    assert plan.request_data == {"model": "m"}


async def test_shared_plan_transient_failure_not_frozen() -> None:
    """InflightTaskCancelledError 不冻结缓存：首个到者收瞬态失败，后到者重建成功。"""
    plan = SharedRequestPlan()
    attempts = {"count": 0}

    async def transient_then_working_builder() -> dict[str, Any]:
        attempts["count"] += 1
        await asyncio.sleep(0)
        if attempts["count"] == 1:
            raise InflightTaskCancelledError("共享在途任务已被取消")
        return {"model": "m"}

    outcomes = await asyncio.gather(
        *(plan.get_or_build("image_to_image", transient_then_working_builder) for _ in range(3)),
        return_exceptions=True,
    )

    # 首个到者收瞬态失败原样上抛，后到者经重建拿到结果并复用共享构建产物
    assert sum(isinstance(o, InflightTaskCancelledError) for o in outcomes) == 1
    assert [o for o in outcomes if isinstance(o, dict)] == [{"model": "m"}] * 2
    assert attempts["count"] == 2
    assert plan.request_data == {"model": "m"}


async def test_shared_plan_transient_marker_beyond_known_types_not_frozen() -> None:
    """计划层未枚举的瞬态类型经源头混入标记同样不冻结缓存，后到者重建成功。"""
    from seedream_mcp.utils.core.inflight import TransientInflightError

    class _ExternalTransientError(RuntimeError, TransientInflightError):
        """计划层未枚举、仅靠源头标记声明瞬态的替身异常。"""

    plan = SharedRequestPlan()
    attempts = {"count": 0}

    async def transient_then_working_builder() -> dict[str, Any]:
        attempts["count"] += 1
        await asyncio.sleep(0)
        if attempts["count"] == 1:
            raise _ExternalTransientError("外部瞬态失败")
        return {"model": "m"}

    outcomes = await asyncio.gather(
        *(plan.get_or_build("text_to_image", transient_then_working_builder) for _ in range(3)),
        return_exceptions=True,
    )

    assert sum(isinstance(o, _ExternalTransientError) for o in outcomes) == 1
    assert [o for o in outcomes if isinstance(o, dict)] == [{"model": "m"}] * 2
    assert attempts["count"] == 2
    assert plan.request_data == {"model": "m"}


async def test_shared_plan_prepare_sourced_transient_marker_survives_wrapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """prepare 链内抛出的标记携带者穿透 inputs 兜底重定型：计划层不冻结，后到者重建成功。"""
    from seedream_mcp.utils.core.inflight import TransientInflightError
    from seedream_mcp.utils.images import inputs as image_inputs_module
    from seedream_mcp.utils.images.inputs import prepare_image_input

    class _ExternalTransientError(RuntimeError, TransientInflightError):
        """prepare 链内抛出、仅靠源头标记声明瞬态的替身异常。"""

    call_count = 0

    def transient_then_pass(image: str) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _ExternalTransientError("外部瞬态失败")
        return image

    monkeypatch.setattr(image_inputs_module, "validate_image_input", transient_then_pass)

    plan = SharedRequestPlan()

    async def builder() -> dict[str, Any]:
        prepared = await prepare_image_input("https://example.com/ref.png")
        return {"image": prepared}

    outcomes = await asyncio.gather(
        *(plan.get_or_build("image_to_image", builder) for _ in range(3)),
        return_exceptions=True,
    )

    # 首个到者收的仍是标记携带者本身：兜底重定型为 SeedreamValidationError 会抹掉标记
    assert sum(isinstance(o, _ExternalTransientError) for o in outcomes) == 1
    assert [o for o in outcomes if isinstance(o, dict)] == [
        {"image": "https://example.com/ref.png"}
    ] * 2
    assert call_count == 2
    assert plan.request_data == {"image": "https://example.com/ref.png"}


async def test_shared_plan_prepare_exhaustion_frozen_and_replayed() -> None:
    """prepare 重试耗尽的终态失败冻结并重放独立副本：后到者不重建、不叠加第二层重试。"""
    from seedream_mcp.utils.images.prepare import PrepareInflightExhaustedError

    plan = SharedRequestPlan()
    exhaustion = PrepareInflightExhaustedError(
        "共享预处理任务被反复取消，已放弃重试", error_code="prepare_inflight_cancelled"
    )
    attempts = {"count": 0}

    async def exhausting_builder() -> dict[str, Any]:
        attempts["count"] += 1
        await asyncio.sleep(0)
        raise exhaustion

    outcomes = await asyncio.gather(
        *(plan.get_or_build("image_to_image", exhausting_builder) for _ in range(3)),
        return_exceptions=True,
    )

    # 首个到者收原异常，其余后到者收互不相同的独立副本，构建只执行一次
    raised: list[PrepareInflightExhaustedError] = []
    for outcome in outcomes:
        assert isinstance(outcome, PrepareInflightExhaustedError)
        raised.append(outcome)

    assert attempts["count"] == 1
    assert sum(exc is exhaustion for exc in raised) == 1
    assert len({id(exc) for exc in raised}) == 3
    for exc in raised:
        assert exc.message == exhaustion.message
        assert exc.error_code == exhaustion.error_code

    # 计划存续期内后到的同键构建请求被冻结异常短路，重建不能成功
    async def working_builder() -> dict[str, Any]:
        return {"model": "m"}

    late: BaseException | None = None
    try:
        await plan.get_or_build("image_to_image", working_builder)
    except Exception as exc:
        late = exc

    assert isinstance(late, PrepareInflightExhaustedError)
    assert late is not exhaustion
    assert attempts["count"] == 1
    assert plan.request_data is None


async def test_shared_plan_build_failure_copy_preserves_key_attributes() -> None:
    """校验异常构建失败的重放副本保留 message/field/value 等关键属性。"""
    plan = SharedRequestPlan()

    async def failing_builder() -> dict[str, Any]:
        raise SeedreamValidationError("参考图不可读", field="image", value=["bad"])

    original: SeedreamValidationError | None = None
    try:
        await plan.get_or_build("text_to_image", failing_builder)
    except SeedreamValidationError as exc:
        original = exc
    assert original is not None

    for _ in range(2):
        replayed: SeedreamValidationError | None = None
        try:
            await plan.get_or_build("text_to_image", failing_builder)
        except SeedreamValidationError as exc:
            replayed = exc

        assert replayed is not None
        assert replayed is not original
        assert type(replayed) is type(original)
        assert str(replayed) == str(original)
        assert replayed.message == original.message
        assert replayed.field == original.field
        assert replayed.value == original.value
        assert replayed.error_code == original.error_code


async def test_shared_plan_build_failure_copy_preserves_cause_chain() -> None:
    """raise ... from 的构建失败重放副本保留原异常的因果链，traceback 仍独立。"""
    plan = SharedRequestPlan()
    root_cause = ValueError("读盘失败")
    builder_failure = RuntimeError("prepare failed")
    attempts = {"count": 0}

    async def failing_builder() -> dict[str, Any]:
        attempts["count"] += 1
        await asyncio.sleep(0)
        try:
            raise root_cause
        except ValueError:
            raise builder_failure from root_cause

    outcomes = await asyncio.gather(
        *(plan.get_or_build("text_to_image", failing_builder) for _ in range(3)),
        return_exceptions=True,
    )
    raised = [exc for exc in outcomes if isinstance(exc, BaseException)]
    assert len(raised) == 3

    # 首个到者收原异常，其余后到者收互不相同的独立副本
    assert sum(exc is builder_failure for exc in raised) == 1
    assert len({id(exc) for exc in raised}) == 3
    # 各副本因果链与原异常一致，后到者的根因诊断不因重放降级
    for exc in raised:
        assert exc.__cause__ is root_cause
        assert exc.__context__ is root_cause
        assert exc.__suppress_context__ is True
    assert attempts["count"] == 1

    frames_before_replay = _count_traceback_frames(builder_failure)
    replayed: BaseException | None = None
    try:
        await plan.get_or_build("text_to_image", failing_builder)
    except Exception as exc:
        replayed = exc

    assert replayed is not None
    assert replayed is not builder_failure
    assert replayed.__cause__ is root_cause
    # 重抛不向共享异常对象的 traceback 追加帧
    assert _count_traceback_frames(builder_failure) == frames_before_replay

    # 隐式链（无 from）的副本 context 保留且不被 __cause__ 赋值隐式抑制
    plan.release()
    implicit_failure = RuntimeError("implicit failed")

    async def implicit_builder() -> dict[str, Any]:
        try:
            raise root_cause
        except ValueError:
            raise implicit_failure

    try:
        await plan.get_or_build("text_to_image", implicit_builder)
    except RuntimeError:
        pass
    implicit_clone: BaseException | None = None
    try:
        await plan.get_or_build("text_to_image", implicit_builder)
    except Exception as exc:
        implicit_clone = exc

    assert implicit_clone is not None
    assert implicit_clone is not implicit_failure
    assert implicit_clone.__cause__ is None
    assert implicit_clone.__context__ is root_cause
    assert implicit_clone.__suppress_context__ is False


async def test_shared_plan_replay_falls_back_to_shared_object_when_copy_fails() -> None:
    """copy.copy 失败的异常类型重放回退共享对象本身，真实失败不被 TypeError 掩盖。"""
    plan = SharedRequestPlan()
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(404, request=request)
    failure = httpx.HTTPStatusError("上游 404", request=request, response=response)

    async def failing_builder() -> dict[str, Any]:
        raise failure

    with pytest.raises(httpx.HTTPStatusError):
        await plan.get_or_build("text_to_image", failing_builder)

    replayed: BaseException | None = None
    try:
        await plan.get_or_build("text_to_image", failing_builder)
    except Exception as exc:
        replayed = exc

    # httpx.HTTPStatusError 的构造参数不能经构造器往返，copy.copy 抛 TypeError，
    # 回退重抛共享对象本身
    assert replayed is failure


async def test_shared_plan_rebuilds_when_key_changes() -> None:
    """同一计划内键不匹配即重建，防止跨方法复用错发前一方法的请求体。"""
    plan = SharedRequestPlan()

    async def builder_a() -> dict[str, Any]:
        return {"model": "a"}

    async def builder_b() -> dict[str, Any]:
        return {"model": "b"}

    built_a = await plan.get_or_build("text_to_image", builder_a)
    built_b = await plan.get_or_build("image_to_image", builder_b)

    assert built_a == {"model": "a"}
    assert built_b == {"model": "b"}
    assert plan.request_data == {"model": "b"}


async def test_prevalidate_failure_matches_single_request_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批次级预校验失败经 handle_text_to_image 降级的类型与消息与单请求路径一致。

    预校验在批次分发前上抛，整批统一降级，不进入逐请求错误聚合。
    """
    validation_failure = SeedreamValidationError("预校验拒绝", field="size", value="bad")

    def failing_validate(**kwargs: Any) -> Any:
        del kwargs
        raise validation_failure

    monkeypatch.setattr(client_module, "validate_common_generation_params", failing_validate)

    config = _build_config()

    # 单请求路径首请求失败：直连调用在生成方法入口校验公共参数并原样上抛
    single_exc: BaseException | None = None
    try:
        async with SeedreamClient(config) as client:
            await client.text_to_image(prompt="p")
    except Exception as exc:
        single_exc = exc

    assert isinstance(single_exc, SeedreamValidationError)
    assert single_exc is validation_failure

    # 批次路径：预校验失败在分发前上抛，经 handler 统一降级为整批错误结果
    batch_result = await handle_text_to_image(
        TextToImageInput(prompt="p", request_count=3, parallelism=3),
        config,
    )

    assert batch_result.is_error is True
    assert isinstance(batch_result.structured_content, dict)
    batch_error = batch_result.structured_content["error"]
    assert batch_error["message"] == format_error_for_user(single_exc)
    assert batch_error["type"] == resolve_error_profile(single_exc).error_code

    # 单请求经同一 handler 的降级结果与批次完全一致，消费方无需按批次数区分错误形态
    single_result = await handle_text_to_image(TextToImageInput(prompt="p"), config)

    assert single_result.is_error is True
    assert single_result.structured_content == batch_result.structured_content
