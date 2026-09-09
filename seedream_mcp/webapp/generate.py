"""Web 操作台生成端点：四个工具经 runners 层复用完整 MCP 流水线。

请求体由 schemas.py 的 *Input 模型校验，字段与 MCP 工具同源，响应为工具的
structured_content 字典；生成链路不伪造会话 Roots，文件边界由 runner 内的
环境变量回退链处理，与客户端未声明 roots capability 的 MCP 会话同构。data
与 auto_save.results 条目的 local_path 改写为图片目录相对形态并附 web_path
供前端拼接图片端点，越出图片目录的删除该键（Web 文件端点仅服务图片目录内文件）；
markdown_ref 前端不消费，整体删除；错误文本原样透传。共享 client 经 context
替身借用，鉴权由外层 Bearer 中间件承担；端点仅消费 structuredContent，预览
装配关闭，请求体解析与响应体序列化下沉工作线程执行。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol, TypeVar, cast

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ListRootsResult
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import SeedreamConfig, get_active_config
from ..tools.core.schemas import (
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
from ..utils.core.errors import SeedreamConfigError, SeedreamValidationError
from ..utils.core.logs import get_logger
from . import _shared
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
        include_previews: bool = True,
    ) -> CallToolResult: ...


def _rewrite_item_path(item: dict[str, object], images_root: Path) -> None:
    """改写单个结果条目的路径字段，产出前端可消费的 web_path 相对形态。

    相对化与越界删除经 _shared.converge_path_entry 单点维护；markdown_ref 前端
    不消费，无条件删除。
    """
    item.pop("markdown_ref", None)
    _shared.converge_path_entry(item, "local_path", images_root, resolve=True)


def augment_generation_payload(structured: dict[str, object], images_root: Path) -> None:
    """改写 data 与 auto_save.results 条目的路径字段并附 web_path。

    供前端拼接图片端点；save_path 越出图片目录时其条目同样经 _rewrite_item_path
    收敛为 Web 文件端点可服务的形态。
    """
    data = structured.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _rewrite_item_path(item, images_root)
    auto_save = structured.get("auto_save")
    results = auto_save.get("results") if isinstance(auto_save, dict) else None
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                _rewrite_item_path(item, images_root)


async def _run_web_generation(
    request: Request,
    model_cls: type[_InputT],
    runner: _GenerationRunner[_InputT],
) -> Response:
    """解析请求体并执行生成 runner，按结果形态映射响应。

    请求体经 pydantic 输入模型校验，响应体为工具的结构化结果字典；失败结果
    按 error.type 映射状态码，响应体保持完整结构化结果供前端展示错误详情。
    请求体解析与响应体序列化是随参考图体积线性增长的同步 CPU 工作，下沉
    工作线程避免阻塞事件循环。
    """
    try:
        body_bytes = await request.body()
        body = await asyncio.to_thread(json.loads, body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _shared.error_json("invalid_json", f"请求体不是合法 JSON: {exc}", 400)
    if not isinstance(body, dict):
        return _shared.error_json("invalid_request", "请求体须为 JSON 对象", 400)

    try:
        params = model_cls.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ()))
        return _shared.error_json(
            "invalid_request",
            f"参数校验失败: {field or first.get('type')} {first.get('msg')}",
            400,
        )

    config = get_active_config()
    ctx = cast("Context | None", build_web_request_context())
    # 不伪造会话 Roots 传入 runner：Web 请求无客户端声明可用，UNC 工作区根也
    # 在 file URI 转换层丢失。边界交由 runner 内的环境变量回退链处理，与客户
    # 端未声明 roots 的会话同构。
    images_root = await _shared.resolve_web_images_root()
    if isinstance(images_root, JSONResponse):
        return images_root
    try:
        result = await runner(params, config, ctx, include_previews=False)
    except (SeedreamValidationError, SeedreamConfigError) as exc:
        if isinstance(exc, SeedreamConfigError):
            return _shared.error_json("config_error", exc.message, 503)
        return _shared.error_json("validation_error", exc.message, 400)
    except Exception:
        logger.exception("Web 生成请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)

    structured = result.structured_content if result.structured_content is not None else {}
    if not isinstance(structured, dict):
        structured = {}

    await asyncio.to_thread(augment_generation_payload, structured, images_root)

    status = 200 if not result.is_error else _shared.generation_status(structured)
    payload = await asyncio.to_thread(
        json.dumps,
        structured,
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
    )
    return Response(content=payload, media_type="application/json", status_code=status)


async def web_generate_text_to_image(request: Request) -> Response:
    """文生图端点。"""
    return await _run_web_generation(request, TextToImageInput, run_text_to_image)


async def web_generate_image_to_image(request: Request) -> Response:
    """图生图端点。"""
    return await _run_web_generation(request, ImageToImageInput, run_image_to_image)


async def web_generate_multi_image_fusion(request: Request) -> Response:
    """多图融合端点。"""
    return await _run_web_generation(request, MultiImageFusionInput, run_multi_image_fusion)


async def web_generate_sequential_generation(request: Request) -> Response:
    """组图生成端点。"""
    return await _run_web_generation(request, SequentialGenerationInput, run_sequential_generation)
