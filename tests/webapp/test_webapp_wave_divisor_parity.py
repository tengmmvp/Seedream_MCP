"""生成等待预算推导与真实执行链组合口径的 parity 守护。

generate.py 以 ⌈request_count÷除数⌉×跨尝试预算加固定余量推导单值调度预算，
除数经共享 helper resolve_wave_divisor 单源推导：缺省并行度单源受进程级生成准入与
显式 parallelism 封顶，准入经 client/_http.py 取 config.generate_concurrency，单批并行
截断经 schema 校验器推导、由 context.parallelism 消费（pipeline/parallel.py）。
下载尾巴经 generate.py 估算器 worst_case_auto_save_tail_ms 单源推导：单请求产图数取
输入模型元数据（预检不按工具名分派），生成准入在 API 返回即释放、早于保存批下载，
下载在共享 save_batch 信号量上排队且公平性不保证单请求份额，尾巴按总图数 × 单次
最坏下载预算的串行折算（保存批信号量的真实接线由守护用例钉住），auto_save 三态与
build_generation_context 的 enable_auto_save 同口径合成。预检按 query 投影取值、
不复刻调度语义，预算欠计真实波次或总图数会使在途批次被客户端按派生截止时间提前
中止。本文件以真实执行链的调度与保存容量折算代表性配置下的期望值，与预算推导逐档
对账：服务端改调度语义或组合口径而预算推导未同步时在此变红。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from _lib.generation_payloads import GENERATION_BASE_PAYLOADS
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.definitions.conventions import (
    MAX_LAYER_DECOMPOSITION_IMAGES,
    MAX_PARALLEL_REQUEST_COUNT,
)
from seedream_mcp.tools.pipeline.auto_save import _build_auto_save_manager
from seedream_mcp.tools.pipeline.context import build_generation_context
from seedream_mcp.tools.schemas import (
    BaseGenerationInput,
    GenerationInputParams,
    ImageToImageInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.utils.core import loop_bound
from seedream_mcp.utils.core.retry_budget import api_retry_budget_seconds
from seedream_mcp.utils.core.validators import (
    resolve_default_parallelism,
    resolve_wave_divisor,
)
from seedream_mcp.utils.io.download import worst_case_download_budget_ms
from seedream_mcp.webapp.generate import (
    GENERATE_OVERHEAD_MS,
    _generate_scheduling_budget_ms,
    worst_case_auto_save_tail_ms,
)

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


def test_per_request_image_count_metadata_matches_pipeline_shape() -> None:
    """输入模型的单请求产图数元数据与流水线真实产图形态对账。

    组图生效值经 context.max_images 消费，图层拆分为上游固定满额形态，其余恒产一图。
    元数据偏离真实形态会使下载尾巴欠计总图数。
    """
    config = SeedreamConfig(api_key="guard-key")
    for model, payload in GENERATION_BASE_PAYLOADS.items():
        context = build_generation_context(cast(GenerationInputParams, model(**payload)), config)
        # 载荷表按 BaseModel 收纳生成模型，元数据经生成输入基类收窄访问。
        input_cls = cast("type[BaseGenerationInput]", model)
        if model is SequentialGenerationInput:
            expected = context.max_images
        else:
            expected = 1
            assert context.max_images is None, f"{model.__name__} 不应有组图生效上限"
        assert (
            input_cls.per_request_image_count(
                layer_decomposition=False, max_images=None, image_count=0
            )
            == expected
        ), f"{model.__name__} 缺省产图数元数据 != 流水线真实形态 {expected}"
    assert (
        ImageToImageInput.per_request_image_count(
            layer_decomposition=True, max_images=None, image_count=0
        )
        == MAX_LAYER_DECOMPOSITION_IMAGES
    ), "图层拆分产图数元数据偏离满额常量"
    # 组图投影键（max_images、image_count）的生效值合成与流水线消费值逐档一致。
    for max_images in (None, 1, 4):
        for image_count in (0, 1, 3):
            params = SequentialGenerationInput(
                prompt="组图",
                image=None if image_count == 0 else ["https://x/a.png"] * image_count,
                max_images=max_images,
            )
            context = build_generation_context(params, config)
            assert (
                SequentialGenerationInput.per_request_image_count(
                    layer_decomposition=False, max_images=max_images, image_count=image_count
                )
                == context.max_images
            ), f"max_images={max_images} image_count={image_count}: 产图数元数据与生效值漂移"


# 代表性容量组合（保存并发上限, 生成准入上限）：串行折算不随组合改变，
# 逐档对账钉住估算器不回退到任何并发折算形态。
_CAPACITY_MODEL_CASES = [
    (1, 1),
    (3, 3),
    (5, 3),
    (12, 1),
    (1, MAX_PARALLEL_REQUEST_COUNT),
    (MAX_PARALLEL_REQUEST_COUNT, MAX_PARALLEL_REQUEST_COUNT + 6),
]


@pytest.mark.parametrize(("max_concurrent", "generate_concurrency"), _CAPACITY_MODEL_CASES)
def test_download_tail_serial_model_ignores_save_capacity(
    max_concurrent: int, generate_concurrency: int
) -> None:
    """下载尾巴按逐图串行折算，与保存并发和生成准入档位解耦。"""
    config = SeedreamConfig(
        api_key="guard-key",
        generate_concurrency=generate_concurrency,
        auto_save_max_concurrent=max_concurrent,
    )
    per_download_ms = worst_case_download_budget_ms(
        config.auto_save_download_timeout, config.auto_save_max_retries
    )
    max_total_images = MAX_LAYER_DECOMPOSITION_IMAGES * MAX_PARALLEL_REQUEST_COUNT
    for total_images in range(1, max_total_images + 1):
        expected = total_images * per_download_ms
        assert (
            worst_case_auto_save_tail_ms(config, total_images=total_images, auto_save=None)
            == expected
        ), (
            f"保存并发 {max_concurrent} 准入 {generate_concurrency} "
            f"total_images={total_images}: 下载尾巴推导偏离逐图串行折算"
        )


@pytest.mark.parametrize("max_concurrent", [1, 3, 5, 12, MAX_PARALLEL_REQUEST_COUNT])
async def test_save_batch_semaphore_wiring_matches_save_capacity(
    tmp_path: Path, max_concurrent: int
) -> None:
    """真实构造路径的保存批信号量接线与保存容量对账。

    经 pipeline/auto_save 的 _build_auto_save_manager 以估算器同源 config 字段构造
    真实 AutoSaveManager，空批保存仍创建 loop 级 save_batch 信号量。
    limit 派生自 config.auto_save_max_concurrent，共享信号量即串行折算的前提。
    """
    config = SeedreamConfig(
        api_key="guard-key",
        auto_save_max_concurrent=max_concurrent,
    )
    manager = _build_auto_save_manager(config, tmp_path, None, None)
    assert manager.max_concurrent == config.auto_save_max_concurrent
    async with manager:
        await manager.save_multiple_base64_images([])
    cached = loop_bound._last_semaphore.get("save_batch")
    assert cached is not None, "真实保存路径未创建 save_batch 信号量"
    _, save_batch_limit, _ = cached
    assert save_batch_limit == manager.max_concurrent


@pytest.mark.parametrize(
    "explicit_auto_save", [None, True, False], ids=["default", "true", "false"]
)
@pytest.mark.parametrize("global_enabled", [True, False], ids=["global-on", "global-off"])
def test_download_tail_effective_auto_save_matches_context(
    global_enabled: bool, explicit_auto_save: bool | None
) -> None:
    """下载尾巴的 auto_save 生效值合成与 build_generation_context 的 enable_auto_save parity。"""
    config = SeedreamConfig(api_key="guard-key", auto_save_enabled=global_enabled)
    context = build_generation_context(
        TextToImageInput(prompt="parity", auto_save=explicit_auto_save), config
    )
    tail_ms = worst_case_auto_save_tail_ms(config, total_images=1, auto_save=explicit_auto_save)

    assert (tail_ms > 0) is context.enable_auto_save, (
        f"auto_save={explicit_auto_save} global={global_enabled}: "
        "尾巴生效值与执行链 enable_auto_save 漂移"
    )
