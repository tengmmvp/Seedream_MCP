"""Web 操作台生成端点：四个工具经 runners 层复用完整 MCP 流水线。

请求体由 schemas.py 的 *Input 模型校验，字段与 MCP 工具同源，响应为工具的
structured_content 字典；生成链路不伪造会话 Roots，文件边界由 runner 内的
环境变量回退链处理，与客户端未声明 roots capability 的 MCP 会话同构。服务器
绝对路径不出端点：data 与 auto_save.results 条目的 local_path 改写为保存根
相对形态并附 web_path 供前端拼接图片端点，越出保存根的删除该键；markdown_ref
恒由绝对路径拼出且前端不消费，整体删除；错误自由文本中的读权限成员路径替换
为占位符。共享 client 经 context 替身借用，鉴权由外层 Bearer 中间件承担；
端点仅消费 structuredContent，预览装配关闭，请求体解析与响应体序列化下沉
工作线程执行。
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
from ..tools.core._helpers import _resolve_base_dir
from ..tools.core.results import _sanitize_value_tree
from ..utils.core.errors import SeedreamConfigError, SeedreamValidationError
from ..utils.core.logs import get_logger
from ..utils.io.io_path import (
    mask_scope_paths,
    save_root_relative,
)
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


def sanitize_save_root_text(
    structured: dict[str, object],
    save_root: Path,
    read_scope: list[Path],
    extra_masks: list[tuple[Path, str]] | None = None,
) -> None:
    """替换结构化结果字符串值中的读权限成员绝对路径为占位符，就地改写。

    树遍历复用 results._sanitize_value_tree 的显式栈实现：深嵌套不触发递归上
    限，循环引用以占位终止。str 值经 io_path.mask_scope_paths 替换存储根与
    工作区根的全部出现处，extra_masks 追加请求内 save_path 声明的保存目录
    遮蔽，覆盖 auto_save.results[].error、data[].error 嵌套 message 与顶层
    error.message 等错误自由文本通道；非字符串叶子值保持原样。须在
    augment_generation_payload 之后调用，此时保存根内 local_path 已改写为相对
    形态，不受替换波及。
    """
    sanitized = _sanitize_value_tree(
        structured,
        lambda text: mask_scope_paths(text, save_root, read_scope, extra_masks),
    )
    structured.clear()
    structured.update(sanitized)


def _rewrite_item_path(item: dict[str, object], save_root: Path) -> None:
    """改写单个结果条目的路径字段，服务器绝对路径不出端点。

    落在保存根内的条目附 web_path 相对路径且 local_path 替换为同一相对形态；
    越出保存根（save_path 指定的保存根外目的地）或路径解析失败的条目删除
    local_path 键。markdown_ref 恒由绝对路径拼出而前端不消费，无条件删除。
    条目缺 local_path、值空串或非字符串时仅删 markdown_ref，其余内容不改动。
    """
    item.pop("markdown_ref", None)
    local_path = item.get("local_path")
    if not isinstance(local_path, str) or not local_path:
        return
    try:
        web_path = save_root_relative(Path(local_path).resolve(), save_root)
    except (OSError, ValueError):
        del item["local_path"]
        return
    if web_path is None:
        del item["local_path"]
        return
    item["web_path"] = web_path
    item["local_path"] = web_path


def augment_generation_payload(structured: dict[str, object], save_root: Path) -> None:
    """改写 data 与 auto_save.results 条目的路径字段并附 web_path。

    供前端拼接图片端点；save_path 越出保存根时其条目同样经 _rewrite_item_path
    收敛，不向浏览器泄露保存根外目的地。
    """
    data = structured.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _rewrite_item_path(item, save_root)
    auto_save = structured.get("auto_save")
    results = auto_save.get("results") if isinstance(auto_save, dict) else None
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                _rewrite_item_path(item, save_root)


def _destination_masks(params: BaseModel) -> list[tuple[Path, str]]:
    """把请求内 save_path 声明的保存目录转为遮蔽对，错误文案不泄露目的地。

    保存目录不在读权限成员内（save_path 可指向任意位置），文件系统错误文案
    会嵌入其绝对路径，遮蔽仅覆盖成员时该通道漏出。目的地解析复用生成链路的
    _resolve_base_dir 单点，遮蔽前缀与实际写入位置恒同规则；解析失败时无可靠
    目的地可遮蔽，返回空列表。
    """
    save_path = getattr(params, "save_path", None)
    if not isinstance(save_path, str) or not save_path:
        return []
    try:
        return [(_resolve_base_dir(save_path), "<保存目录>")]
    except (SeedreamValidationError, SeedreamConfigError):
        return []


def _masking_context(
    params: BaseModel, save_root: Path
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """单点派生遮蔽上下文：读权限集合与 save_path 目的地遮蔽对一次求值。

    调用方须在工作线程执行。成功与错误路径共用同一份结果，读权限不再逐阶段
    重复派生，失败降级口径由 _shared.read_scope_or_default 单点定义。
    """
    return _shared.read_scope_or_default(save_root), _destination_masks(params)


def _finalize_web_payload(
    structured: dict[str, object],
    save_root: Path,
    read_scope: list[Path],
    extra_masks: list[tuple[Path, str]] | None = None,
) -> None:
    """响应出端前的改写步骤：先增强条目路径，再净化错误自由文本。

    local_path 改写在前：保存根内条目此时替换为相对 web_path，不再携带可被
    净化匹配的绝对前缀；保存根外条目被删除，残余绝对路径只存在于错误文本中。
    读权限由 _masking_context 预先派生后传入，本函数不再触达文件系统。
    """
    augment_generation_payload(structured, save_root)
    sanitize_save_root_text(structured, save_root, read_scope, extra_masks)


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
    save_root = await _shared.resolve_web_save_root()
    if isinstance(save_root, JSONResponse):
        return save_root
    # 遮蔽上下文单次派生：读权限与 save_path 遮蔽对在同一线程 hop 内求值，成功
    # 响应与错误分支共用，不再逐阶段重复 resolve。
    read_scope, extra_masks = await asyncio.to_thread(_masking_context, params, save_root)
    try:
        result = await runner(params, config, ctx, include_previews=False)
    except (SeedreamValidationError, SeedreamConfigError) as exc:
        message = mask_scope_paths(exc.message, save_root, read_scope, extra_masks)
        if isinstance(exc, SeedreamConfigError):
            return _shared.error_json("config_error", message, 503)
        return _shared.error_json("validation_error", message, 400)
    except Exception:
        logger.exception("Web 生成请求执行异常")
        return _shared.error_json("internal_error", "服务器内部错误，详情见日志", 500)

    structured = result.structured_content if result.structured_content is not None else {}
    if not isinstance(structured, dict):
        structured = {}

    await asyncio.to_thread(_finalize_web_payload, structured, save_root, read_scope, extra_masks)

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
