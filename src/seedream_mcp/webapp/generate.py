"""Web 操作台生成端点：四个工具经 runners 层复用完整 MCP 流水线。

请求体由 schemas.py 的 *Input 模型校验，字段与 MCP 工具同源，响应为工具的
structured_content 字典；生成链路不伪造会话 Roots，文件边界由 runner 内的
环境变量回退链处理，与客户端未声明 roots capability 的 MCP 会话同构。data
与 auto_save.results 条目的 local_path 改写为图片目录相对形态并附 web_path
供前端拼接图片端点，越出图片目录的删除该键（Web 文件端点仅服务图片目录内文件）；
markdown_ref 前端不消费，整体删除；错误文本原样透传，
端点自有失败分支与 runner 失败同为完整结构化结果。共享 client 经 context
替身借用，鉴权由外层 Bearer 中间件承担；端点仅消费 structuredContent，
预览与镜像通道经 output_channels_scope 在作用域边界关闭。
解析与响应体序列化达阈值时下沉工作线程执行。
与 MCP 工具不同，图片目录不可用时端点即回 400 images_root_unavailable：
Web 展示闭环依赖 web_path，不存在工具侧 auto_save 降级后的纯 URL 成功形态。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar, cast, overload

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import SeedreamConfig, get_active_config
from ..definitions.conventions import (
    IMAGE_TO_IMAGE_TOOL_NAME,
    MAX_PARALLEL_REQUEST_COUNT,
    MAX_SEQUENTIAL_TOTAL_IMAGES,
    MULTI_IMAGE_FUSION_TOOL_NAME,
    SEQUENTIAL_GENERATION_TOOL_NAME,
    TEXT_TO_IMAGE_TOOL_NAME,
)
from ..tools.outputs.channels import output_channels_scope
from ..tools.schemas import (
    BaseGenerationInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from ..tools.runners import (
    run_image_to_image,
    run_multi_image_fusion,
    run_sequential_generation,
    run_text_to_image,
)
from ..utils.core.http_int import strict_http_int
from ..utils.core.json_envelope import SECURE_JSON_HEADERS
from ..utils.core.logs import get_logger
from ..utils.core.retry_budget import api_retry_budget_seconds
from ..utils.core.validators import resolve_effective_auto_save, resolve_wave_divisor
from ..utils.io.download import worst_case_download_budget_ms
from ..utils.io.stream import data_items
from ..utils.model.capabilities import SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
from . import _responses
from .context import build_web_request_context

logger = get_logger()

_GenerationInputT = TypeVar("_GenerationInputT", bound=BaseGenerationInput)

# 泛型绑定具体输入模型，避免 Callable 逆变要求 runner 接受任意基类实现。
# Web 入口不传 workspace_roots，真实 runner 的该可选尾参不影响可赋值性。
_GenerationRunner = Callable[
    [_GenerationInputT, SeedreamConfig, Context | None],
    Awaitable[CallToolResult],
]


def _rewrite_item_path(item: dict[str, object], images_root: Path) -> None:
    """改写单个结果条目的路径字段，产出前端可消费的 web_path 相对形态。

    相对化与越界删除经 _responses.converge_path_entry 单点维护。
    local_path 恒为保存链已 resolve 的绝对路径，词法相对化与图库端同契约。
    markdown_ref 前端不消费，无条件删除。
    """
    item.pop("markdown_ref", None)
    _responses.converge_path_entry(item, "local_path", images_root)


def augment_generation_payload(structured: dict[str, object], images_root: Path) -> None:
    """改写 data 与 auto_save.results 条目的路径字段并附 web_path。

    供前端拼接图片端点；save_path 越出图片目录时其条目同样经 _rewrite_item_path
    收敛为 Web 文件端点可服务的形态。data 的条目形态经 data_items 归一，dict
    形态计单条目不漏收敛。
    """
    for item in data_items(structured.get("data")):
        if isinstance(item, dict):
            _rewrite_item_path(item, images_root)
    auto_save = structured.get("auto_save")
    results = auto_save.get("results") if isinstance(auto_save, dict) else None
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                _rewrite_item_path(item, images_root)


# 生成调度预算的固定余量毫秒数，只覆盖图像预处理等单请求固定开销，不含自动保存下载，
# 也不含进程级生成准入排队——并发下第 N 单可能在排到前被客户端按预算掐断，预检是
# 单请求启发式。缓解：服务端继续执行、结果自动落图库，超时文案导引用户转查图库。
GENERATE_OVERHEAD_MS = 300_000


def _generate_scheduling_budget_ms(
    config: SeedreamConfig, request_count: int, *, parallelism: int | None = None
) -> int:
    """按波次推导生成调度预算毫秒：波次完成预算加固定余量，不含下载尾巴。

    推导按单请求独占进程级生成准入展开；显式 parallelism 压低单请求并发时波次按比例增多，
    并发生成请求共享准入时实际波次可更多，前端超时消息已提示服务端可能仍在执行。
    """
    budget_ms = int(api_retry_budget_seconds(config.api_timeout) * 1000)
    divisor = resolve_wave_divisor(
        request_count, config.generate_concurrency, parallelism=parallelism
    )
    return math.ceil(request_count / divisor) * budget_ms + GENERATE_OVERHEAD_MS


def worst_case_auto_save_tail_ms(
    config: SeedreamConfig, *, total_images: int, auto_save: bool | None
) -> int:
    """自动保存下载尾巴的最坏情形毫秒数，生效值关闭时恒为 0。

    生成准入在 API 调用返回即释放、早于保存批下载，下载在进程级共享的
    save_batch 信号量上排队，FIFO 公平性只保证队首最终获得许可位、不保证
    单请求份额，尾巴按最坏逐图串行折算：总图数 × 单次最坏下载预算。
    字段即执行链构造保存管理器时消费的同名 config 字段，
    auto_save 三态经 resolve_effective_auto_save 与执行链同口径合成。
    """
    if not resolve_effective_auto_save(auto_save, config.auto_save_enabled):
        return 0
    return total_images * worst_case_download_budget_ms(
        config.auto_save_download_timeout, config.auto_save_max_retries
    )


# 前端 setTimeout 的毫秒值域上限（2^31-1）：超出经 ToInt32 回绕为非正值立即触发。
JS_SET_TIMEOUT_MAX_MS = 2_147_483_647


def _wait_budget_ms(
    config: SeedreamConfig,
    *,
    request_count: int,
    per_request_images: int,
    parallelism: int | None = None,
    auto_save: bool | None = None,
) -> int:
    """等待预算毫秒：波次调度预算加最坏情形下载尾巴，封顶 JS setTimeout 值域。

    auto_save 三态：None 回退全局开关，非 None 为请求级覆盖。
    """
    return min(
        _generate_scheduling_budget_ms(config, request_count, parallelism=parallelism)
        + worst_case_auto_save_tail_ms(
            config,
            total_images=request_count * per_request_images,
            auto_save=auto_save,
        ),
        JS_SET_TIMEOUT_MAX_MS,
    )


def generate_fallback_timeout_ms(config: SeedreamConfig) -> int:
    """满档等待预算：表单上限请求数加图层拆分满额产图的最坏推导。

    config-info 下发此值作前端 preflight 失败的等待兜底，随部署配置走；预算
    本体仍由生成端点 preflight 按输入单源推导。
    """
    return _wait_budget_ms(
        config,
        request_count=MAX_PARALLEL_REQUEST_COUNT,
        per_request_images=ImageToImageInput.per_request_image_count(
            layer_decomposition=True, max_images=None, image_count=0
        ),
    )


@overload
def _parse_clamped_query_int(value: str | None, lower: int, upper: int, default: int) -> int: ...


@overload
def _parse_clamped_query_int(
    value: str | None, lower: int, upper: int, default: None = None
) -> int | None: ...


def _parse_clamped_query_int(
    value: str | None, lower: int, upper: int, default: int | None = None
) -> int | None:
    """query 整数的解析加钳位，缺失或解析失败返回 default，成功值钳位到 [lower, upper]。"""
    if value is None:
        return default
    parsed = strict_http_int(value)
    if parsed is None:
        return default
    return min(max(parsed, lower), upper)


def _preflight_timeout_ms(
    request: Request, config: SeedreamConfig, model_cls: type[BaseGenerationInput]
) -> int:
    """预检按 query 投影推导前端等待预算（波次调度预算加自动保存下载尾巴），不读请求体与图片目录。

    读取键集与前端 preflightTimeoutMs 构造键集的对账由契约测试锁定；body 携带
    parallelism 或显式 auto_save 的客户端须将其投影进 query。
    """
    query = request.query_params
    # 预检恒有应答：投影失真不报错也不放大预算。
    request_count = _parse_clamped_query_int(
        query.get("request_count"), 1, MAX_PARALLEL_REQUEST_COUNT, default=1
    )
    # download_tail 三态投影请求级 auto_save 覆盖：0/1 对应 body 显式 false/true。
    download_tail = query.get("download_tail")
    return _wait_budget_ms(
        config,
        request_count=request_count,
        per_request_images=model_cls.per_request_image_count(
            layer_decomposition=query.get("layer_decomposition") == "1",
            max_images=_parse_clamped_query_int(
                query.get("max_images"), 1, MAX_SEQUENTIAL_TOTAL_IMAGES
            ),
            image_count=_parse_clamped_query_int(
                query.get("image_count"), 0, SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES, default=0
            ),
        ),
        parallelism=_parse_clamped_query_int(
            query.get("parallelism"), 1, MAX_PARALLEL_REQUEST_COUNT
        ),
        auto_save=None if download_tail not in ("0", "1") else download_tail == "1",
    )


async def _run_web_generation(
    request: Request,
    tool_name: str,
    model_cls: type[_GenerationInputT],
    runner: _GenerationRunner[_GenerationInputT],
) -> Response:
    """解析请求体并执行生成 runner，按结果形态映射响应。

    请求体解析、输入模型校验与图片目录解析经 _responses.entry_preamble；
    响应体为工具的结构化结果字典，端点自有失败分支与 runner 失败同为完整结构化结果
    供前端展示错误详情，状态码均按 error.type 经单源映射推导。
    query 参数 preflight=1 时预检在序言前返回，按 query 投影推导前端等待预算，
    download_tail 等键的投影语义见 _preflight_timeout_ms；
    大请求体并发准入与读体窗口限时由传输层准入中间件承担，许可终帧即释放；
    解析与响应体序列化达到卸载阈值时下沉 CPU 卸载池，低于阈值同步执行。
    """
    config = get_active_config()
    if request.query_params.get("preflight") == "1":
        # 预检先于序言：推导预算只需 query 投影，免上传含参考图的整请求体。
        return JSONResponse(
            {"success": True, "timeout_ms": _preflight_timeout_ms(request, config, model_cls)},
            headers=SECURE_JSON_HEADERS,
        )
    preamble = await _responses.entry_preamble(request, tool_name, model_cls)
    if isinstance(preamble, Response):
        return preamble
    params, images_root = preamble
    # 不伪造会话 Roots 传入 runner：Web 请求无客户端声明可用，UNC 工作区根也
    # 在 file URI 转换层丢失。边界交由 runner 内的环境变量回退链处理，与客户
    # 端未声明 roots 的会话同构。
    ctx = cast("Context | None", build_web_request_context())
    # runner 契约保证不抛，异常已归约为 is_error 结果，兜底仅防流水线全捕回归。
    # 类型化异常→错误码的分派归流水线全捕层，Web 兜底不分派、不回显异常原文。
    try:
        with output_channels_scope(preview=False, mirror=False):
            result = await runner(params, config, ctx)
    except Exception:
        logger.exception("Web 生成请求执行异常")
        return await _responses.INTERNAL_ERROR_INFO.as_response(tool_name)

    structured = await _responses.runner_result_payload(tool_name, result)

    await asyncio.to_thread(augment_generation_payload, structured, images_root)

    status = 200 if not result.is_error else _responses.structured_status(structured)
    return await _responses.respond_structured_json(structured, status)


async def web_generate_text_to_image(request: Request) -> Response:
    """文生图端点。"""
    return await _run_web_generation(
        request, TEXT_TO_IMAGE_TOOL_NAME, TextToImageInput, run_text_to_image
    )


async def web_generate_image_to_image(request: Request) -> Response:
    """图文生图端点。"""
    return await _run_web_generation(
        request, IMAGE_TO_IMAGE_TOOL_NAME, ImageToImageInput, run_image_to_image
    )


async def web_generate_multi_image_fusion(request: Request) -> Response:
    """多图融合端点。"""
    return await _run_web_generation(
        request,
        MULTI_IMAGE_FUSION_TOOL_NAME,
        MultiImageFusionInput,
        run_multi_image_fusion,
    )


async def web_generate_sequential_generation(request: Request) -> Response:
    """组图生成端点。"""
    return await _run_web_generation(
        request,
        SEQUENTIAL_GENERATION_TOOL_NAME,
        SequentialGenerationInput,
        run_sequential_generation,
    )
