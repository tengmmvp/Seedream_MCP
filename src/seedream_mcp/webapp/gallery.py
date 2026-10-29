"""Web 操作台图库浏览端点：图片目录边界校验后透传 browse 工具并收敛前端消费形态。

与 MCP 会话共用同一求值链与读权限判定；Web 文件端点仅服务图片目录内文件，图库
浏览同样以图片目录为界，请求目录经 resolve_against_base 与 browse 核心共用同一
解析组合，越出图片目录在端点拒绝，解析结果透传 browse 复用，免同请求重复解析。
browse 条目为绝对路径，Web 层改写为图片目录相对形态供前端直接拼接为图片端点
参数；workspace_roots 与 resolved_directories 回显字段 Web 前端不消费，返回浏览
器前剥除。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

from ..definitions.conventions import BROWSE_IMAGES_TOOL_NAME
from ..tools.outputs.channels import output_channels_scope
from ..tools.schemas import BrowseImagesInput
from ..tools.runners import run_browse_images
from ..utils.core.logs import get_logger
from ..utils.io.path import BoundedResolution, resolve_against_base
from . import _responses

logger = get_logger()

# browse 工具为 MCP 客户端回显的边界字段，Web 前端不消费，不出 Web 端点。
_ROOTS_ECHO_KEYS = ("workspace_roots", "resolved_directories")


def _converge_for_web(structured: dict[str, object], images_root: Path) -> None:
    """剥除 Web 前端不消费的边界字段，条目 path 改写为图片目录相对形态。

    越界条目已在扫描源头经 bounds_scope 剔除。改写经 _responses.converge_path_entry
    与 generate 端单点维护。
    """
    for key in _ROOTS_ECHO_KEYS:
        structured.pop(key, None)
    images = structured.get("images")
    if not isinstance(images, list):
        return
    for item in images:
        if isinstance(item, dict):
            _responses.converge_path_entry(item, "path", images_root)


async def _resolve_request_directory(directory: str, images_root: Path) -> BoundedResolution | None:
    """经共享解析组合解析请求目录，供端点拒绝判定与 browse 复用。

    形态非法时返回 None，交 browse 核心以原语义报具体原因。
    """

    def _resolve() -> BoundedResolution | None:
        try:
            return resolve_against_base(directory, images_root)
        except ValueError:
            return None

    return await asyncio.to_thread(_resolve)


async def _structured_error(error: _responses.WebErrorInfo) -> Response:
    """图库端点错误分支的结构化响应，错误载荷经输出族映射绑定浏览族模型。"""
    return await error.as_response(BROWSE_IMAGES_TOOL_NAME)


async def web_browse(request: Request) -> Response:
    """图库浏览端点，请求体经 BrowseImagesInput 校验后透传 browse 工具。

    端点各失败分支均回浏览族结构化错误，与 runner 失败结果形态一致；
    请求体解析、参数校验与图片目录解析经 _responses.entry_preamble 与生成端点共享，
    下沉口径与早期失败形态单点维护。
    """
    preamble = await _responses.entry_preamble(request, BROWSE_IMAGES_TOOL_NAME, BrowseImagesInput)
    if isinstance(preamble, Response):
        return preamble
    params, images_root = preamble
    directory = params.effective_directory
    resolution = await _resolve_request_directory(directory, images_root)
    if resolution is not None and not resolution.within_base:
        return await _structured_error(_responses.INVALID_DIRECTORY_INFO)
    resolved_directory = resolution.resolved if resolution is not None else None
    # browse 契约保证不抛，异常已归约为 is_error 结果，兜底仅防处理器全捕回归；
    # 条目过滤以图片目录为界在扫描源头剔除，与 Web 文件端点服务范围一致。
    try:
        # 端点仅消费 structuredContent，镜像通道在作用域边界关闭，免付镜像构建成本。
        with output_channels_scope(mirror=False):
            result = await run_browse_images(
                params, ctx=None, bounds_scope=[images_root], resolved_directory=resolved_directory
            )
    except Exception:
        logger.exception("Web 图库浏览请求执行异常")
        return await _structured_error(_responses.INTERNAL_ERROR_INFO)
    payload = await _responses.runner_result_payload(BROWSE_IMAGES_TOOL_NAME, result)
    _converge_for_web(payload, images_root)
    status = 200 if not result.is_error else _responses.structured_status(payload)
    return await _responses.respond_structured_json(payload, status)
