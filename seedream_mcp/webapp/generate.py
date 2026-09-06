"""Web 操作台生成端点：四个工具经 runners 层复用完整 MCP 流水线。

请求体由 schemas.py 的 *Input 模型校验，字段与 MCP 工具同源，响应为工具的
structured_content 字典；生成链路不伪造会话 Roots，文件边界由 runner 内的
环境变量回退链处理，与客户端未声明 roots capability 的 MCP 会话同构。结果
条目与校验错误消息中的保存根绝对路径不出端点，local_path 改写为相对形态
或删除，落在保存根内的条目附 web_path 供前端拼接图片端点，错误自由文本中
的保存根路径替换为占位符。共享 client 经 context 替身借用，鉴权由外层
Bearer 中间件承担；端点仅消费 structuredContent，预览装配关闭，请求体解析
与响应体序列化下沉工作线程执行。
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
from ..utils.core.errors import SeedreamValidationError
from ..utils.core.logs import get_logger
from ..utils.io.io_path import is_within_resolved
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


def sanitize_save_root_text(value: object, save_root: Path) -> None:
    """递归替换结构化结果字符串值中的保存根绝对路径为占位符，就地改写。

    dict 与 list 逐层下钻，str 值经 _shared.mask_save_root_text 替换全部
    保存根出现处，覆盖 auto_save.results[].error、data[].error 嵌套 message
    与顶层 error.message 等错误自由文本通道；非字符串叶子值保持原样。须在
    augment_generation_payload 之后调用，此时保存根内 local_path 已改写为相对
    形态，不受替换波及。
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                value[key] = _shared.mask_save_root_text(item, save_root)
            else:
                sanitize_save_root_text(item, save_root)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                value[index] = _shared.mask_save_root_text(item, save_root)
            else:
                sanitize_save_root_text(item, save_root)


def augment_generation_payload(structured: dict[str, object], save_root: Path) -> None:
    """改写 data 条目的 local_path 并附 web_path，供前端拼接图片端点。

    落在保存根内的条目附 web_path 相对路径且 local_path 替换为同一相对形态；
    越出保存根或路径解析失败的条目删除 local_path 键，前端不展示服务器绝对
    路径。条目缺 local_path、值空串或非字符串时跳过，其余内容不改动。
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
                web_path = resolved.relative_to(save_root).as_posix()
                item["web_path"] = web_path
                item["local_path"] = web_path
            else:
                del item["local_path"]
        except (OSError, ValueError):
            del item["local_path"]


def _finalize_web_payload(structured: dict[str, object], save_root: Path) -> None:
    """响应出端前的改写步骤：先增强 data 条目路径，再净化错误自由文本。

    local_path 改写在前：保存根内条目此时替换为相对 web_path，不再携带可被
    净化匹配的绝对前缀；保存根外条目被删除，残余绝对路径只存在于错误文本中。
    """
    augment_generation_payload(structured, save_root)
    sanitize_save_root_text(structured, save_root)


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
    # 不伪造会话 Roots 传入 runner：伪造会解锁本地路径错误回显分支泄露服务器
    # 路径，UNC 工作区根也在 file URI 转换层丢失使回退边界失效。边界交由
    # runner 内的环境变量回退链处理，与客户端未声明 roots 的会话同构。
    save_root = await _shared.resolve_web_save_root(config)
    if isinstance(save_root, JSONResponse):
        return save_root
    try:
        result = await runner(params, config, ctx, include_previews=False)
    except SeedreamValidationError as exc:
        message = _shared.mask_save_root_text(exc.message, save_root)
        return _shared.error_json("validation_error", message, 400)
    except Exception:
        logger.exception("Web 生成请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)

    structured = result.structured_content if result.structured_content is not None else {}
    if not isinstance(structured, dict):
        structured = {}

    await asyncio.to_thread(_finalize_web_payload, structured, save_root)

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
