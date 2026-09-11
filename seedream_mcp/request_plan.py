"""并行批次的共享请求计划：批内构建、序列化与公共参数校验各只执行一次。

经 ``shared_request_plan_scope`` 绑定到当前上下文后由 client 的生成方法与
``_call_api`` 读取，批次结束随作用域退出释放；未绑定的直连调用走独立构建
与序列化路径。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Iterator

from .utils.core.validators import ValidatedCommonParams


class SharedRequestPlan:
    """单次工具调用内并行请求的共享计划。

    同一批次的多并行请求共享同一份 request_data 与序列化 body，构建与序列化各
    恰好发生一次。

    Attributes:
        request_data: 共享的请求参数字典，构建完成后批内只读。
        body: 共享的序列化请求体，同批请求复用同一 bytes 对象。
        validated_common_params: 批内公共参数校验缓存，输入快照与校验结果组成的二元组。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.request_data: dict[str, Any] | None = None
        self.body: bytes | None = None
        self._body_key: str | None = None
        self.validated_common_params: tuple[tuple[Any, ...], ValidatedCommonParams] | None = None
        self._build_key: str | None = None
        self._build_error: tuple[str, Exception] | None = None

    async def get_or_build(
        self,
        key: str,
        builder: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """返回共享 request_data：同键首个到者执行 builder 构建，其余复用。

        key 为调用点标识，同一计划内键不匹配即重建，防止跨方法复用时静默
        错发前一方法的请求体。builder 在锁内执行；构建抛出时缓存首个异常并
        重放给同键后到者。批内输入相同，重试构建只会重复读盘与解码等全量
        副作用，故重放同一异常对象。失败缓存随 release 清空，下一批次重新
        构建。
        """
        if self.request_data is not None and self._build_key == key:
            return self.request_data
        async with self._lock:
            if self.request_data is None or self._build_key != key:
                if self._build_error is not None and self._build_error[0] == key:
                    raise self._build_error[1]
                try:
                    self.request_data = await builder()
                    self._build_key = key
                    self._build_error = None
                    # 键切换重建后旧 body 与新 request_data 不再对应，一并失效。
                    self.body = None
                except Exception as exc:
                    self._build_error = (key, exc)
                    raise
            return self.request_data

    async def get_or_serialize(
        self,
        key: str,
        request_data: dict[str, Any],
        serializer: Callable[[dict[str, Any]], bytes],
    ) -> bytes:
        """返回共享 body：同键首个到者序列化一次，其余复用同一 bytes 对象。

        request_data 须为 ``get_or_build`` 返回的同一共享 dict；body 归属键与
        当前键不一致时重新序列化，防止交错复用错发。
        """
        if self.body is not None and self._body_key == key:
            return self.body
        async with self._lock:
            if self.body is None or self._body_key != key:
                self.body = await asyncio.to_thread(serializer, request_data)
                self._body_key = key
            return self.body

    def release(self) -> None:
        """清除共享引用，避免 body 滞留至批次之后的阶段。"""
        self.request_data = None
        self.body = None
        self._body_key = None
        self.validated_common_params = None
        self._build_key = None
        self._build_error = None

    async def get_or_validate(
        self,
        inputs: tuple[Any, ...],
        validator: Callable[[], Awaitable[ValidatedCommonParams]],
    ) -> ValidatedCommonParams:
        """返回公共参数校验缓存：输入快照一致时复用，否则锁内单飞校验。"""
        cached = self.validated_common_params
        if cached is not None and cached[0] == inputs:
            return cached[1]
        async with self._lock:
            cached = self.validated_common_params
            if cached is None or cached[0] != inputs:
                validated = await validator()
                self.validated_common_params = (inputs, validated)
                return validated
            return cached[1]


# 当前批次的共享计划绑定，None 表示未绑定。
_ACTIVE_REQUEST_PLAN: ContextVar[SharedRequestPlan | None] = ContextVar(
    "seedream_active_request_plan", default=None
)


@contextmanager
def shared_request_plan_scope() -> Iterator[SharedRequestPlan]:
    """绑定新的共享请求计划至当前上下文，退出时复位绑定并释放计划引用。

    异常与取消路径同样经 finally 复位。
    """
    plan = SharedRequestPlan()
    token = _ACTIVE_REQUEST_PLAN.set(plan)
    try:
        yield plan
    finally:
        _ACTIVE_REQUEST_PLAN.reset(token)
        plan.release()


async def _build_request_data(
    plan: SharedRequestPlan | None,
    key: str,
    builder: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """按共享计划构建 request_data：无计划直接构建，有计划经单飞复用同批结果。"""
    if plan is None:
        return await builder()
    return await plan.get_or_build(key, builder)
