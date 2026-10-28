"""生成调度预算单值推导与真实调度容量组合的 parity 守护。

generate.py 以 ⌈request_count÷除数⌉×跨尝试预算加固定余量推导单值调度预算，
除数经共享 helper resolve_wave_divisor 单源推导：缺省并行度单源受进程级生成准入与
显式 parallelism 封顶，准入经 client/_http.py 取 config.generate_concurrency，单批并行
截断经 schema 校验器推导、由 context.parallelism 消费（pipeline/parallel.py）。
预检按 query 投影取值、不复刻调度语义，预算欠计真实波次会使在途批次被客户端
按派生截止时间提前中止。本文件以真实调度的两层容量经同一 helper 折算代表性配置下的
波次完成预算，与 generate.py 的单值函数逐档对账：服务端改调度语义或组合口径而预算
推导未同步时在此变红。
"""

from __future__ import annotations

import math
from typing import cast

import pytest
from pydantic import ValidationError

from _lib.generation_payloads import GENERATION_BASE_PAYLOADS
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.definitions.conventions import MAX_PARALLEL_REQUEST_COUNT
from seedream_mcp.tools.pipeline.context import build_generation_context
from seedream_mcp.tools.schemas import GenerationInputParams
from seedream_mcp.utils.core.retry_budget import api_retry_budget_seconds
from seedream_mcp.utils.core.validators import (
    resolve_default_parallelism,
    resolve_wave_divisor,
)
from seedream_mcp.webapp.generate import GENERATE_OVERHEAD_MS, _generate_scheduling_budget_ms

# 代表性配置：准入覆盖小于、等于与越过单批上限的档位。
_REPRESENTATIVE_CONCURRENCY_LEVELS = (
    1,
    2,
    3,
    5,
    MAX_PARALLEL_REQUEST_COUNT,
    MAX_PARALLEL_REQUEST_COUNT + 6,
)


def test_scheduling_budget_matches_server_scheduling() -> None:
    """代表性准入档位下调度预算逐档等于真实调度的波次完成预算加余量。

    服务端波次经共享 resolve_wave_divisor 由两层真实容量（schema 校验器推导的
    context.parallelism 与 config.generate_concurrency）折算；预算 = 波次×跨尝试
    预算 + 固定余量，锁定推导不欠计真实波次。
    """
    for concurrency in _REPRESENTATIVE_CONCURRENCY_LEVELS:
        config = SeedreamConfig(api_key="guard-key", generate_concurrency=concurrency)
        budget_ms = int(api_retry_budget_seconds(config.api_timeout) * 1000)
        for model, payload in GENERATION_BASE_PAYLOADS.items():
            for request_count in range(1, MAX_PARALLEL_REQUEST_COUNT + 1):
                context = build_generation_context(
                    cast(GenerationInputParams, model(**payload, request_count=request_count)),
                    config,
                )
                assert context.parallelism == resolve_default_parallelism(
                    request_count
                ), f"{model.__name__} 单批并行截断偏离缺省推导单源"
                waves = math.ceil(
                    request_count
                    / resolve_wave_divisor(
                        request_count, config.generate_concurrency, parallelism=context.parallelism
                    )
                )
                budget = _generate_scheduling_budget_ms(config, request_count)
                assert budget == waves * budget_ms + GENERATE_OVERHEAD_MS, (
                    f"{model.__name__} request_count={request_count} "
                    f"generate_concurrency={concurrency}: 预算 {budget} "
                    f"!= 服务端调度推导 {waves * budget_ms + GENERATE_OVERHEAD_MS}，"
                    "调度预算与服务端调度语义漂移"
                )


def test_scheduling_budget_explicit_parallelism_matches_context() -> None:
    """显式 parallelism 并入波次除数，波次按经 context.parallelism 消费的真实调度对账。"""
    for concurrency in _REPRESENTATIVE_CONCURRENCY_LEVELS:
        config = SeedreamConfig(api_key="guard-key", generate_concurrency=concurrency)
        budget_ms = int(api_retry_budget_seconds(config.api_timeout) * 1000)
        request_count = MAX_PARALLEL_REQUEST_COUNT
        parallelism = 2
        for model, payload in GENERATION_BASE_PAYLOADS.items():
            context = build_generation_context(
                cast(
                    GenerationInputParams,
                    model(**payload, request_count=request_count, parallelism=parallelism),
                ),
                config,
            )
            assert context.parallelism == parallelism
            waves = math.ceil(
                request_count
                / resolve_wave_divisor(
                    request_count, config.generate_concurrency, parallelism=context.parallelism
                )
            )
            assert (
                _generate_scheduling_budget_ms(config, request_count, parallelism=parallelism)
                == waves * budget_ms + GENERATE_OVERHEAD_MS
            ), f"{model.__name__} 显式 parallelism={parallelism} 未并入波次除数"


def test_request_count_domain_capped_at_batch_limit() -> None:
    """调用数合法域以单批上限封顶，预检投影的 request_count 按同一上限钳位。"""
    for model, payload in GENERATION_BASE_PAYLOADS.items():
        with pytest.raises(ValidationError):
            model(**payload, request_count=MAX_PARALLEL_REQUEST_COUNT + 1)
