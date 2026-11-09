"""Web 操作台图库浏览端点：透传 browse 工具并收敛前端消费形态。

与 MCP 会话共用同一求值链与读权限判定；Web 文件端点仅服务图片目录内文件，
图库浏览同样以图片目录为界：图片目录与 Web 拒绝文案经 entry_bound 一并传入 browse 核心，
请求目录的解析与边界判定由核心单点承担。browse 条目为绝对路径，
Web 层改写为图片目录相对形态供前端直接拼接为图片端点参数。
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

from ..definitions.conventions import BROWSE_IMAGES_TOOL_NAME
from ..tools.outputs.channels import output_channels_scope
from ..tools.schemas import BrowseImagesInput, EntryBound
from ..tools.runners import run_browse_images
from ..utils.core.errors import error_text
from ..utils.core.logs import get_logger
from . import _responses

logger = get_logger()

# 同界门拒绝文案（Web 图库口径）。
_ENTRY_BOUND_DENIAL_MESSAGE = error_text("目录不在图片目录内，Web 图库仅浏览图片目录")


def _converge_for_web(structured: dict[str, object], images_root: Path) -> None:
    """投影白名单裁剪后，条目 path 改写为图片目录相对形态。

    越界条目已在扫描源头经替代界剔除。改写经 _responses.converge_path_entry
    与 generate 端单点维护。
    """
    _responses.project_browse_payload(structured)
    images = structured.get("images")
    if not isinstance(images, list):
        return
    for item in images:
        if isinstance(item, dict):
            _responses.converge_path_entry(item, "path", images_root)


async def web_browse(request: Request) -> Response:
    """图库浏览端点，请求体经 BrowseImagesInput 校验后透传 browse 工具。

    端点各失败分支均回浏览族结构化错误，与 runner 失败结果形态一致；
    请求体解析、参数校验与图片目录解析经 _responses.entry_preamble，
    下沉口径与早期失败形态单点维护。
    """
    preamble = await _responses.entry_preamble(request, BROWSE_IMAGES_TOOL_NAME, BrowseImagesInput)
    if isinstance(preamble, Response):
        return preamble
    params, images_root = preamble
    # browse 契约保证不抛，异常已归约为 is_error 结果，兜底仅防处理器全捕回归。
    try:
        # 端点仅消费 structuredContent，镜像通道在作用域边界关闭，免付镜像构建成本。
        with output_channels_scope(mirror=False):
            result = await run_browse_images(
                params,
                ctx=None,
                entry_bound=EntryBound(images_root, _ENTRY_BOUND_DENIAL_MESSAGE),
            )
    except Exception:
        logger.exception("Web 图库浏览请求执行异常")
        return await _responses.INTERNAL_ERROR_INFO.as_response(BROWSE_IMAGES_TOOL_NAME)
    payload = await _responses.runner_result_payload(BROWSE_IMAGES_TOOL_NAME, result)
    _converge_for_web(payload, images_root)
    status = 200 if not result.is_error else _responses.structured_status(payload)
    return await _responses.respond_structured_json(payload, status)
