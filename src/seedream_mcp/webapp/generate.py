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
from pathlib import Path
from typing import Protocol, TypeVar, cast, overload

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ListRootsResult
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import SeedreamConfig, get_active_config
from ..definitions.conventions import (
    IMAGE_TO_IMAGE_TOOL_NAME,
    MAX_LAYER_DECOMPOSITION_IMAGES,
    MAX_PARALLEL_REQUEST_COUNT,
    MAX_SEQUENTIAL_TOTAL_IMAGES,
    MULTI_IMAGE_FUSION_TOOL_NAME,
    SEQUENTIAL_GENERATION_TOOL_NAME,
    TEXT_TO_IMAGE_TOOL_NAME,
)
from ..tools.outputs.channels import output_channels_scope
from ..tools.schemas import (
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
from ..utils.core.errors import (
    SeedreamConfigError,
    SeedreamValidationError,
    resolve_error_profile,
)
from ..utils.core.http_int import strict_http_int
from ..utils.core.logs import get_logger
from ..utils.core.retry_budget import api_retry_budget_seconds
from ..utils.core.validators import (
    resolve_sequential_max_images,
    resolve_wave_divisor,
)
from ..utils.io.download import worst_case_download_budget_ms
from ..utils.io.stream import data_items
from ..utils.model.capabilities import SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES
from . import _responses
from .context import build_web_request_context

logger = get_logger()

_InputT = TypeVar("_InputT", bound=BaseModel)
_RunnerInputT = TypeVar("_RunnerInputT", bound=BaseModel, contravariant=True)


class _GenerationRunner(Protocol[_RunnerInputT]):
    """生成 runner 的调用契约，与 tools.runners 的 run_* 签名保持对齐。"""

    async def __call__(
        self,
        params: _RunnerInputT,
        config: SeedreamConfig,
        ctx: Context | None = None,
        workspace_roots: ListRootsResult | None = None,
    ) -> CallToolResult: ...


def _rewrite_item_path(item: dict[str, object], images_root: Path) -> None:
    """改写单个结果条目的路径字段，产出前端可消费的 web_path 相对形态。

    相对化与越界删除经 _responses.converge_path_entry 单点维护；markdown_ref 前端
    不消费，无条件删除。
    """
    item.pop("markdown_ref", None)
    _responses.converge_path_entry(item, "local_path", images_root, resolve=True)


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


def _per_request_image_count(
    tool_name: str,
    *,
    layer_decomposition: bool,
    max_images: int | None,
    image_count: int,
) -> int:
    """单请求产图数：图层拆分取满额，组图按 max_images 与参考图张数推导，其余恒产一图为上游固定形态。"""
    if layer_decomposition:
        return MAX_LAYER_DECOMPOSITION_IMAGES
    if tool_name == SEQUENTIAL_GENERATION_TOOL_NAME:
        return resolve_sequential_max_images(max_images, image_count)
    return 1


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


def _wait_budget_ms(
    config: SeedreamConfig,
    tool_name: str,
    *,
    request_count: int,
    parallelism: int | None = None,
    layer_decomposition: bool = False,
    max_images: int | None = None,
    image_count: int = 0,
    download_tail: bool | None = None,
) -> int:
    """等待预算毫秒：波次调度预算加按需计入的自动保存下载尾巴。

    download_tail 三态：None 回退全局自动保存开关，True/False 为请求级覆盖
    （显式 True 覆盖全局关闭，与 pipeline 的生效值合成同口径）。
    """
    budget_ms = _generate_scheduling_budget_ms(config, request_count, parallelism=parallelism)
    include_tail = config.auto_save_enabled if download_tail is None else download_tail
    if not include_tail:
        return budget_ms
    total_images = request_count * _per_request_image_count(
        tool_name,
        layer_decomposition=layer_decomposition,
        max_images=max_images,
        image_count=image_count,
    )
    # 保存信号量为进程级共享（并发生成请求均分槽位），单请求按
    # ceil(max_concurrent / generate_concurrency) 个槽位折算下载波次；未满载时
    # 实际下载更快，预算取保守的满载均分假设。
    slots_per_request = math.ceil(config.auto_save_max_concurrent / config.generate_concurrency)
    tail_ms = math.ceil(total_images / slots_per_request) * worst_case_download_budget_ms(
        config.auto_save_download_timeout, config.auto_save_max_retries
    )
    return budget_ms + tail_ms


def generate_fallback_timeout_ms(config: SeedreamConfig) -> int:
    """满档等待预算：表单上限请求数加图层拆分满额产图的最坏推导。

    config-info 下发此值作前端 preflight 失败的等待兜底，随部署配置走；预算
    本体仍由生成端点 preflight 按输入单源推导。
    """
    return _wait_budget_ms(
        config,
        IMAGE_TO_IMAGE_TOOL_NAME,
        request_count=MAX_PARALLEL_REQUEST_COUNT,
        layer_decomposition=True,
    )


def _preflight_timeout_ms(request: Request, config: SeedreamConfig, tool_name: str) -> int:
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
        tool_name,
        request_count=request_count,
        parallelism=_parse_clamped_query_int(
            query.get("parallelism"), 1, MAX_PARALLEL_REQUEST_COUNT
        ),
        layer_decomposition=query.get("layer_decomposition") == "1",
        max_images=_parse_clamped_query_int(
            query.get("max_images"), 1, MAX_SEQUENTIAL_TOTAL_IMAGES
        ),
        image_count=_parse_clamped_query_int(
            query.get("image_count"), 0, SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES, default=0
        ),
        download_tail=None if download_tail not in ("0", "1") else download_tail == "1",
    )


async def _run_web_generation(
    request: Request,
    tool_name: str,
    model_cls: type[_InputT],
    runner: _GenerationRunner[_InputT],
) -> Response:
    """解析请求体并执行生成 runner，按结果形态映射响应。

    请求体解析、输入模型校验与图片目录解析经 _responses.entry_preamble 与图库端点共享；
    响应体为工具的结构化结果字典，端点自有失败分支与 runner 失败同为完整结构化结果
    供前端展示错误详情，状态码均按 error.type 经单源映射推导。
    query 参数 preflight=1 时预检在序言前返回，按 query 投影推导前端等待预算，
    download_tail 等键的投影语义见 _preflight_timeout_ms；
    大请求体并发准入与读体窗口限时由传输层准入中间件承担，许可终帧即释放；
    解析与响应体序列化达到下沉尺寸阈值时卸载专用 CPU 池，低于阈值同步执行。
    """
    config = get_active_config()
    if request.query_params.get("preflight") == "1":
        # 预检先于序言：推导预算只需 query 投影，免上传含参考图的整请求体。
        return JSONResponse(
            {"success": True, "timeout_ms": _preflight_timeout_ms(request, config, tool_name)},
            headers=_responses.WEB_JSON_HEADERS,
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
    try:
        with output_channels_scope(preview=False, mirror=False):
            result = await runner(params, config, ctx)
    except (SeedreamValidationError, SeedreamConfigError) as exc:
        # 错误码经档案注册表单源，不与注册表漂移；状态由状态表按码查得。
        return await _responses.respond_structured_error(
            tool_name, resolve_error_profile(exc).error_code, exc.message
        )
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
