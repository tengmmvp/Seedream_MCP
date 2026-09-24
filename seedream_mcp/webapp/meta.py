"""Web 操作台元信息端点：入口页、重定向与 config-info。

config-info 是前端的启动面：模型能力与尺寸档位同源 model_capabilities，兼作
鉴权探测端点——配置了令牌时未携带 Authorization 的请求在此得到 401，前端据此
弹出令牌输入。
"""

from __future__ import annotations

import asyncio
import os
from enum import Enum, auto
from pathlib import Path
from typing import IO, NamedTuple
from urllib.parse import quote, unquote

from loguru import logger
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from ..config import get_active_config
from ..utils.core.sanitizers import CONTROL_CHARS_PATTERN
from ..utils.core.formats import SUPPORTED_IMAGE_EXTENSIONS_ORDERED
from ..utils.core.validators import (
    MAX_PARALLEL_REQUEST_COUNT,
    MAX_SEQUENTIAL_TOTAL_IMAGES,
)
from ..utils.io.io_file import SymlinkRejectedError, open_regular_read
from ..utils.model.model_capabilities import (
    MODEL_FAMILY_UNKNOWN,
    get_model_capabilities,
    model_payloads,
    preset_numeric_sort_key,
)
from ..version import __version__
from . import _responses, constants
from ._responses import _NoFollowFileResponse
from .constants import PAGE_SECURITY_HEADERS, WEB_API_PREFIX, WEB_INDEX_PATH

# 模型能力清单缓存：能力表为进程级静态数据，首次构建后跨请求复用。
_MODELS_PAYLOAD: list[dict[str, object]] | None = None

# 上传预算推导扣除的 JSON 信封与提示词等其余字段余量。
_UPLOAD_BUDGET_ENVELOPE_MARGIN = 3 * 1024 * 1024


def _upload_budget_chars(max_body_size: int) -> int:
    """按请求体上限推导 data URI 参考图的累计字符预算，供前端预检单一来源。"""
    return max(max_body_size * 3 // 4 - _UPLOAD_BUDGET_ENVELOPE_MARGIN, 0)


def _fallback_presets() -> list[str]:
    """未知模型回退档位：取 unknown 家族的能力声明，与后端放行口径同源。"""
    presets = get_model_capabilities(MODEL_FAMILY_UNKNOWN).allowed_presets
    return sorted(presets, key=preset_numeric_sort_key)


def _models_payload() -> list[dict[str, object]]:
    """构建模型能力清单。"""
    global _MODELS_PAYLOAD
    if _MODELS_PAYLOAD is None:
        _MODELS_PAYLOAD = model_payloads()
    return _MODELS_PAYLOAD


class _StaticPageFailureKind(Enum):
    """静态页打开失败的分类，降级分流按成员判定。"""

    MISSING = auto()
    SYMLINK_REJECTED = auto()
    NON_REGULAR = auto()
    IO_ERROR = auto()


class _StaticPageOpenFailure(NamedTuple):
    """静态页打开失败的类型化结果，kind 与 errno 构成与展示短语解耦的失败身份。"""

    kind: _StaticPageFailureKind
    errno: int | None = None

    @property
    def missing(self) -> bool:
        """真缺失层级标记，降级文案按它分流。"""
        return self.kind is _StaticPageFailureKind.MISSING

    @property
    def reason(self) -> str:
        """降级告警的原因短语，改写不影响分流与去重。"""
        if self.kind is _StaticPageFailureKind.MISSING:
            return "缺失"
        if self.kind is _StaticPageFailureKind.SYMLINK_REJECTED:
            return "符号链接拒绝"
        if self.kind is _StaticPageFailureKind.NON_REGULAR:
            return "非常规文件"
        return f"IO 错误 errno={self.errno}"


# 已告警的静态页降级 (页面, 失败身份) 组合：进程内每组合只告警一条，抑制持续探测刷屏。
_WARNED_STATIC_PAGE_FAILURES: set[tuple[Path, _StaticPageOpenFailure]] = set()


def _static_page_failure(exc: OSError) -> _StaticPageOpenFailure:
    """把静态页打开异常归类为类型化失败，供降级分流与告警去重。"""
    if isinstance(exc, SymlinkRejectedError):
        return _StaticPageOpenFailure(_StaticPageFailureKind.SYMLINK_REJECTED)
    if isinstance(exc, FileNotFoundError):
        return _StaticPageOpenFailure(_StaticPageFailureKind.MISSING)
    return _StaticPageOpenFailure(_StaticPageFailureKind.IO_ERROR, exc.errno)


def _warn_static_page_degraded(page: Path, failure: _StaticPageOpenFailure) -> None:
    """静态页降级按 (页面, 失败身份) 组合进程内只告警一条。"""
    key = (page, failure)
    if key in _WARNED_STATIC_PAGE_FAILURES:
        return
    _WARNED_STATIC_PAGE_FAILURES.add(key)
    logger.warning("静态页 {} 不可用，降级纯文本响应：{}", page, failure.reason)


def _open_static_page(page: Path) -> tuple[IO[bytes], os.stat_result] | _StaticPageOpenFailure:
    """打开静态页常规文件并取 fstat，可降级失败返回类型化失败。

    常规文件的 PermissionError 原样上抛，由调用方归 500 诊断响应；其余打开
    失败与非常规形态按类型化失败交调用方降级。
    """
    try:
        opened = open_regular_read(page)
    except PermissionError:
        raise
    except OSError as exc:
        return _static_page_failure(exc)
    if opened is None:
        return _StaticPageOpenFailure(_StaticPageFailureKind.NON_REGULAR)
    return opened


async def _serve_static_page(
    page: Path,
    *,
    fallback_text: str,
    status_code: int,
    unavailable_text: str | None = None,
) -> Response:
    """打开静态页构造携带安全头的直出响应，页面不可用降级纯文本，不可读归 500 诊断。

    打开下沉工作线程，降级告警按页面与失败身份去重；status_code 同时作用于降级
    文本与页面直出；真缺失降级回 fallback_text，打开失败类降级回
    unavailable_text，未提供时两类共用 fallback_text。
    """
    try:
        opened = await asyncio.to_thread(_open_static_page, page)
    except PermissionError as exc:
        # 与 files 图片打开失败同口径：不可读归 500 诊断响应，不并入降级文案。
        return _responses.error_json("page_open_failed", f"页面打开失败: {exc}", 500)
    if isinstance(opened, _StaticPageOpenFailure):
        _warn_static_page_degraded(page, opened)
        if opened.missing or unavailable_text is None:
            text = fallback_text
        else:
            text = unavailable_text
        return Response(
            text,
            status_code=status_code,
            media_type="text/plain",
            headers=PAGE_SECURITY_HEADERS,
        )
    handle, page_stat = opened
    return _NoFollowFileResponse(
        handle,
        page_stat,
        media_type="text/html",
        headers=PAGE_SECURITY_HEADERS,
        status_code=status_code,
    )


async def web_index(_request: Request) -> Response:
    """返回 Web 操作台入口页，附 CSP 与 nosniff 安全头；真缺失与打开失败分别降级纯文本，不可读回 500。"""
    # STATIC_DIR 经模块属性访问而非导入期绑定，目录指向可在运行期整体替换。
    page = constants.STATIC_DIR / "index.html"
    return await _serve_static_page(
        page,
        fallback_text="Web 操作台页面缺失，请检查安装完整性。",
        unavailable_text="Web 操作台页面无法读取，详情请查看服务端日志。",
        status_code=200,
    )


async def web_not_found(request: Request) -> Response:
    """兜底 404：尾斜杠路径先 307 到去尾斜杠形态，其余按前缀分流错误页。

    兜底路由会吞掉 Starlette 的 redirect_slashes 语义，使 /mcp/、/web/ 落到
    404；此处按同语义重定向，路径每跳至少短一个字符故无循环。浏览器把特殊
    scheme 路径中的反斜杠归一为斜杠，去尾斜杠后以斜杠或字面反斜杠开头的形态
    会解析成协议相对的外域目标；判定前先做百分号解码与反斜杠归一，归一形以
    // 开头即不重定向，落入后续 404 分支。API 前缀回统一 JSON 错误，其余路径
    回附安全头的风格化 404 页，页面不可用时降级纯文本，不可读回 500 诊断。
    """
    path = request.url.path
    if path != "/" and path.endswith("/"):
        trimmed = path.rstrip("/")
        normalized = unquote(trimmed).replace("\\", "/")
        if trimmed and not normalized.startswith("//"):
            # 查询串原样回填；控制字符进 Location 头触发协议层 500，剔除后重定向；
            # 非 ASCII 字符百分号编码使 Location 头恒可编码，浏览器跟随时按
            # 归一化规则解码。
            query = request.url.query
            target = CONTROL_CHARS_PATTERN.sub("", trimmed + (f"?{query}" if query else ""))
            location = "".join(
                char if ord(char) < 128 else quote(char, encoding="utf-8") for char in target
            )
            return RedirectResponse(location, status_code=307)
    if path == WEB_API_PREFIX or path.startswith(WEB_API_PREFIX + "/"):
        return _responses.error_json("not_found", "接口不存在", 404)
    # STATIC_DIR 经模块属性访问而非导入期绑定，目录指向可在运行期整体替换。
    page = constants.STATIC_DIR / "404.html"
    # 兜底文案只陈述 404 结果，不携带页面状态断言，两类降级原因共用不分档。
    return await _serve_static_page(page, fallback_text="404 Not Found", status_code=404)


async def web_root_redirect(_request: Request) -> Response:
    """把根路径重定向到操作台入口。"""
    return RedirectResponse(WEB_INDEX_PATH, status_code=307)


async def web_config_info(_request: Request) -> Response:
    """返回前端所需的模型能力、默认值与图片目录可用性。

    图片目录解析经 _responses.resolve_web_images_root 与 gallery、generate 域同契约；
    仅回传可用性布尔，不向浏览器泄露服务器绝对路径；不可用时前端在图库区
    给出配置指引。unknown_max_reference_images 与 upload_budget_chars 使前端的
    未知模型参考图上限和上传预算预检与后端配置单一来源。响应附
    cache-control: no-store，兼作鉴权探测端点的状态不落代理缓存。
    """
    config = get_active_config()
    resolved = await _responses.resolve_web_images_root()
    images_root_available = not isinstance(resolved, JSONResponse)
    return JSONResponse(
        {
            "server_version": __version__,
            "model_id": config.model_id,
            "default_size": config.default_size,
            "default_watermark": config.default_watermark,
            "models": _models_payload(),
            "fallback_presets": _fallback_presets(),
            "unknown_max_reference_images": get_model_capabilities(
                MODEL_FAMILY_UNKNOWN
            ).max_reference_images,
            "upload_budget_chars": _upload_budget_chars(config.http_max_body_size),
            "supported_extensions": list(SUPPORTED_IMAGE_EXTENSIONS_ORDERED),
            "max_request_count": MAX_PARALLEL_REQUEST_COUNT,
            "max_images": MAX_SEQUENTIAL_TOTAL_IMAGES,
            "images_root_available": images_root_available,
            "images_root_hint": (
                ""
                if images_root_available
                else f"未配置数据根目录（{_responses.READ_SCOPE_AUTH_ENV_HINT}）"
            ),
            "auto_save_enabled": config.auto_save_enabled,
            "preview_enabled": config.preview_enabled,
        },
        headers=_responses.WEB_JSON_HEADERS,
    )
