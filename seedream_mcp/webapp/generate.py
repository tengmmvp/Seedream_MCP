"""Web 操作台生成端点：四个工具经 runners 层复用完整 MCP 流水线。

请求体由 schemas.py 的 *Input 模型校验（字段与 MCP 工具同源），响应为工具的
structured_content 字典；落在保存根内的结果条目附 web_path 相对路径供前端拼接
图片端点。共享 client 经 context 替身借用，鉴权由外层 Bearer 中间件承担。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar, cast

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import SeedreamConfig, get_active_config
from ..tools.core._helpers import _resolve_default_base_dir
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
from ..utils.core.errors import SeedreamValidationError
from ..utils.core.logs import get_logger
from ..utils.io.io_path import is_within_resolved
from . import _shared
from .context import build_web_request_context

logger = get_logger()

_InputT = TypeVar("_InputT", bound=BaseModel)

_GenerationRunner = Callable[[_InputT, SeedreamConfig, Context | None], Awaitable[CallToolResult]]


def augment_generation_payload(structured: dict[str, object], save_root: Path) -> None:
    """为落在保存根内的结果条目附 web_path 相对路径，供前端拼接图片端点。

    条目缺 local_path、路径解析失败或越出保存根时静默跳过，增强只新增
    web_path 字段，不改动既有内容。
    """
    data = structured.get("data")
    if not isinstance(data, list):
        return
    for item in data:
        if not isinstance(item, dict):
            continue
        local_path = item.get("local_path")
        if not isinstance(local_path, str) or not local_path:
            continue
        try:
            resolved = Path(local_path).resolve()
            if is_within_resolved(resolved, save_root):
                item["web_path"] = resolved.relative_to(save_root).as_posix()
        except (OSError, ValueError):
            continue


async def _run_web_generation(
    request: Request,
    model_cls: type[_InputT],
    runner: _GenerationRunner[_InputT],
) -> Response:
    """解析请求体并执行生成 runner，按结果形态映射响应。

    请求体经 pydantic 输入模型校验，响应体为工具的结构化结果字典；失败结果按
    error.type 映射状态码，响应体保持完整结构化结果供前端展示错误详情。
    """
    try:
        body = await request.json()
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
    try:
        result = await runner(params, config, ctx)
    except SeedreamValidationError as exc:
        return _shared.error_json("validation_error", exc.message, 400)
    except Exception:
        logger.exception("Web 生成请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)

    structured = result.structured_content if result.structured_content is not None else {}
    if not isinstance(structured, dict):
        structured = {}

    def _augment_payload() -> None:
        augment_generation_payload(structured, _resolve_default_base_dir(config))

    try:
        await asyncio.to_thread(_augment_payload)
    except SeedreamValidationError:
        logger.debug("保存根不可解析，跳过 web_path 增强")
    status = 200 if not result.is_error else _shared.generation_status(structured)
    return JSONResponse(structured, status_code=status)


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
