"""Web 操作台各域 handler 共享的响应辅助与错误码映射。

本模块只放跨域复用的纯辅助：统一错误 JSON 形态与结构化错误响应原语、
错误载荷的出端投影查表、生成与图库入口的序言脚手架、图片目录解析契约、
错误类型到 HTTP 状态码的单源映射、文件端点的缓存头与 no-follow 文件响应形态。
域内专有逻辑不落此处，避免演化为杂物箱。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any, NamedTuple, TypeVar

from mcp.types import CallToolResult
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, MalformedRangeHeader, Response
from starlette.types import Message, Receive, Scope, Send

# as 同名形态为显式再导出，供 webapp 测试经本模块替换序列化原语。
from ..definitions.conventions import (
    BROWSE_FAILED_ERROR_TYPE,
    BROWSE_IMAGES_TOOL_NAME,
    MODEL_INFO_FAILED_ERROR_TYPE,
)
from ..tools.outputs import (
    build_error_dict,
    build_error_structured,
    dump_strict_json_bytes as dump_strict_json_bytes,
)
from ..utils.core.errors import (
    SeedreamConfigError,
    iter_error_profiles,
    validation_error_user_message,
)
from ..utils.core.executors import (
    CpuOffloadPoolClosedError,
    load_json_bytes,
    run_in_cpu_pool,
    should_offload_to_cpu_pool,
)
from ..utils.core.logs import get_logger
from ..utils.core.sanitizers import (
    ERROR_TEXT_SEGMENT_JOINER,
    sanitize_assembled_text,
    utf8_value_leaf_length,
)
from ..utils.io.path import (
    READ_SCOPE_AUTH_ENV_HINT as READ_SCOPE_AUTH_ENV_HINT,
    images_root_relative,
    normalize_path,
    resolve_images_root,
)

logger = get_logger()

# 缩略图与原图响应允许浏览器私有缓存：已保存图片内容不再变化；nosniff 阻断
# 旧浏览器把图片字节嗅探为其他类型。
PRIVATE_CACHE_HEADER = {
    "cache-control": "private, max-age=3600",
    "x-content-type-options": "nosniff",
}


# /web/api 全部 JSON 响应的公共安全头：no-store 防代理缓存动态状态，nosniff
# 封堵旧浏览器 MIME 嗅探反射面。
WEB_JSON_HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}


class WebErrorInfo(NamedTuple):
    """端点自有错误的载体，HTTP 状态码随错误定义携带。"""

    type: str
    message: str
    http_status: int

    async def as_response(self, tool_name: str) -> Response:
        """构造绑定 tool_name 的完整结构化错误响应。"""
        return await respond_structured_error(tool_name, self.type, self.message)


# 端点自有错误码单表：需登记状态表的错误码定义收口于此，调用点只消费常量；动态文案码
# 以表项为骨架展开——invalid_json 与 images_root_unavailable 在表项文案后追加详情，
# invalid_path 整体替换表项文案。不入表的诊断码在调用点内联构造，状态随定义携带。
INVALID_JSON_INFO = WebErrorInfo("invalid_json", "请求体不是合法 JSON", 400)
INVALID_REQUEST_INFO = WebErrorInfo("invalid_request", "请求体须为 JSON 对象", 400)
INVALID_PATH_INFO = WebErrorInfo("invalid_path", "无效的图片路径", 400)
IMAGE_NOT_FOUND_INFO = WebErrorInfo("not_found", "图片不存在", 404)
# not_found 一码两文案：API 兜底变体自文件端点表项派生，类型与状态共享单一定义。
API_NOT_FOUND_INFO = IMAGE_NOT_FOUND_INFO._replace(message="接口不存在")
THUMBNAIL_FAILED_INFO = WebErrorInfo("thumbnail_failed", "缩略图生成失败", 422)
IMAGES_ROOT_UNAVAILABLE_INFO = WebErrorInfo("images_root_unavailable", "无法确定图片目录", 400)
INVALID_DIRECTORY_INFO = WebErrorInfo(
    "invalid_directory", "目录不在图片目录内，Web 图库仅浏览图片目录", 400
)
# CPU 卸载池关闭属退出清理与服务并发的窗口期，是服务端不可用而非请求或结果缺陷。
SERVICE_UNAVAILABLE_INFO = WebErrorInfo(
    "service_unavailable", "CPU 卸载线程池已关闭，服务不可用", 500
)

# 端点全捕兜底的固定 WebErrorInfo，生成与图库端点共用，防文案各处漂移。
INTERNAL_ERROR_INFO = WebErrorInfo("internal_error", "服务器内部错误，详情见日志", 500)

# 端点自有码清单：状态表与守护测试共用的单一名单，加码只改此处。末两项为 runner
# 失败码——browse 与 model_info 处理器经 definitions 常量构造失败，无端点构造点，
# 仅登记状态表。
_ENDPOINT_OWN_ERRORS: tuple[WebErrorInfo, ...] = (
    INTERNAL_ERROR_INFO,
    INVALID_JSON_INFO,
    INVALID_REQUEST_INFO,
    INVALID_PATH_INFO,
    IMAGE_NOT_FOUND_INFO,
    THUMBNAIL_FAILED_INFO,
    IMAGES_ROOT_UNAVAILABLE_INFO,
    INVALID_DIRECTORY_INFO,
    SERVICE_UNAVAILABLE_INFO,
    WebErrorInfo(BROWSE_FAILED_ERROR_TYPE, "图片浏览失败", 500),
    WebErrorInfo(MODEL_INFO_FAILED_ERROR_TYPE, "模型信息查询失败", 500),
)


def _merge_error_type_status(
    profile_status: Mapping[str, int], endpoint_status: Mapping[str, int]
) -> dict[str, int]:
    """合并档案与端点自有两段状态表，撞键即抛 ValueError，防按合并顺序静默覆盖。"""
    conflicts = sorted(set(profile_status) & set(endpoint_status))
    if conflicts:
        raise ValueError(f"错误档案与端点自有错误码撞键，须单侧改名: {conflicts}")
    return {**profile_status, **endpoint_status}


def _profile_error_status() -> dict[str, int]:
    """错误档案段状态表，同 error_code 不同 http_status 即抛 AssertionError。"""
    profiles = tuple(iter_error_profiles())
    status = {profile.error_code: profile.http_status for profile in profiles}
    # 字典推导对重码按迭代序 last-wins 静默取后值，断言显式拦下
    assert len(status) == len({(profile.error_code, profile.http_status) for profile in profiles})
    return status


# 错误类型到 HTTP 状态码的单源映射：档案码（含 runner 失败码档案）自错误档案迭代派生，
# 端点自有码取 _ENDPOINT_OWN_ERRORS 定义，撞键在导入期 fail-fast；表外类型经
# _error_status 回 502。
ERROR_TYPE_STATUS: dict[str, int] = _merge_error_type_status(
    _profile_error_status(),
    {error.type: error.http_status for error in _ENDPOINT_OWN_ERRORS},
)


def flat_error_json(error: WebErrorInfo) -> JSONResponse:
    """扁平错误信封响应，形态与传输层中间件一致，message 经幂等净化。"""
    return JSONResponse(
        {"error": error.type, "error_description": sanitize_assembled_text(error.message)},
        status_code=error.http_status,
        headers=WEB_JSON_HEADERS,
    )


def _error_status(error_type: str | None) -> int:
    """错误类型经状态映射表取 HTTP 状态码，缺失或未知类型缺省 502。"""
    return ERROR_TYPE_STATUS.get(error_type or "", 502)


def endpoint_error_json(error: WebErrorInfo) -> JSONResponse:
    """端点自有错误的结构化子对象信封，error 子对象与结构化负载同形，状态码随错误定义携带。"""
    return JSONResponse(
        {"success": False, "error": build_error_dict(error.type, error.message)},
        status_code=error.http_status,
        headers=WEB_JSON_HEADERS,
    )


async def parse_json_object_body(request: Request) -> tuple[dict[str, Any], WebErrorInfo | None]:
    """解析请求体为 JSON 对象，失败时返回 WebErrorInfo 由调用方包装为各自端点的结构化错误响应。

    解析成本随参考图体积线性增长，达到下沉尺寸阈值的卸载长时 CPU 专用池，低于
    阈值同步执行；池关闭取消排队解析归 service_unavailable 500，生成与图库端点
    共用同一口径。
    """
    raw_body = await request.body()
    try:
        body = await load_json_bytes(raw_body)
    except CpuOffloadPoolClosedError:
        return {}, SERVICE_UNAVAILABLE_INFO
    # 深嵌套触发 RecursionError，超长整数字面量抛非 JSONDecodeError 的 ValueError，同归 400。
    except (ValueError, RecursionError) as exc:
        return {}, INVALID_JSON_INFO._replace(message=f"{INVALID_JSON_INFO.message}: {exc}")
    if not isinstance(body, dict):
        return {}, INVALID_REQUEST_INFO
    return body, None


def invalid_path_error(exc: ValueError) -> WebErrorInfo:
    """路径校验拒绝的 WebErrorInfo，拒绝原因透传校验异常的文案。"""
    return INVALID_PATH_INFO._replace(message=str(exc))


def images_root_unavailable_error(exc: Exception) -> WebErrorInfo:
    """图片目录解析失败的 WebErrorInfo，携带配置指引，生成、图库与文件端点共用。"""
    message = getattr(exc, "message", None) or str(exc)
    return IMAGES_ROOT_UNAVAILABLE_INFO._replace(
        message=(
            f"{IMAGES_ROOT_UNAVAILABLE_INFO.message}: {message}"
            f"{ERROR_TEXT_SEGMENT_JOINER}可配置 {READ_SCOPE_AUTH_ENV_HINT}"
        )
    )


async def resolve_web_images_root() -> Path | WebErrorInfo:
    """解析图片目录，含文件系统的解析下沉工作线程；不可解析时返回 WebErrorInfo。"""
    try:
        return await asyncio.to_thread(resolve_images_root)
    except SeedreamConfigError as exc:
        return images_root_unavailable_error(exc)


_ModelT = TypeVar("_ModelT", bound=BaseModel)


async def entry_preamble(
    request: Request,
    tool_name: str,
    model_cls: type[_ModelT],
) -> tuple[_ModelT, Path] | Response:
    """生成与图库入口的序言脚手架：请求体解析、输入模型校验与图片目录解析。

    早期失败统一回绑定 tool_name 的结构化错误响应，两端错误形态单点维护；
    校验对含大体积参考图的请求体是线性 CPU 工作，与解析同批下沉工作线程。
    """
    body, parse_failure = await parse_json_object_body(request)
    if parse_failure is not None:
        return await parse_failure.as_response(tool_name)
    try:
        params = await asyncio.to_thread(model_cls.model_validate, body)
    except ValidationError as exc:
        return await respond_structured_error(
            tool_name, "validation_error", validation_error_user_message(exc)
        )
    images_root = await resolve_web_images_root()
    if isinstance(images_root, WebErrorInfo):
        return await images_root.as_response(tool_name)
    return params, images_root


def structured_error_type(structured: dict[str, object]) -> str | None:
    """提取结构化结果的 error.type，缺失或形态不符返回 None。"""
    error = structured.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        if isinstance(error_type, str):
            return error_type
    return None


def structured_status(structured: dict[str, object]) -> int:
    """按结构化结果的错误类型经单源映射取 HTTP 状态码，生成与图库端点共用。"""
    return _error_status(structured_error_type(structured))


async def runner_result_payload(tool_name: str, result: CallToolResult) -> dict[str, object]:
    """归位 runner 结果的结构化负载，is_error 而错误类型不可判时合成 internal_error。

    进入合成即记 warning，internal_error 文案的「详情见日志」须有真实落点。dict
    负载保留原顶层字段：error 为 dict 时仅补 type，保住上游 message/code 等诊断键；
    error 缺失或非 dict 时插入或替换 internal_error 错误对象。非 dict 负载整体合成。
    """
    structured = result.structured_content
    if result.is_error and not (
        isinstance(structured, dict) and structured_error_type(structured) is not None
    ):
        logger.warning(
            "Web 端点 {} 的 runner 失败结果错误类型不可判，合成 internal_error，原负载: {!r}",
            tool_name,
            structured,
        )
        if not isinstance(structured, dict):
            return build_error_structured(
                tool_name, INTERNAL_ERROR_INFO.type, INTERNAL_ERROR_INFO.message
            )
        synthesized = dict(structured)
        error = synthesized.get("error")
        if isinstance(error, dict):
            synthesized["error"] = {**error, "type": INTERNAL_ERROR_INFO.type}
        else:
            synthesized["error"] = build_error_dict(
                INTERNAL_ERROR_INFO.type, INTERNAL_ERROR_INFO.message
            )
        return synthesized
    if not isinstance(structured, dict):
        structured = {}
    return structured


async def respond_structured_json(structured: dict[str, object], status: int) -> Response:
    """以严格 JSON 构造结构化结果响应，载荷达阈值时序列化与编码下沉专用 CPU 池。"""
    try:
        if should_offload_to_cpu_pool(structured, value_leaf_cost=utf8_value_leaf_length):
            payload = await run_in_cpu_pool(dump_strict_json_bytes, structured)
        else:
            payload = dump_strict_json_bytes(structured)
    except CpuOffloadPoolClosedError:
        # 池关闭兜底无工具名可构完整结构化结果，回与传输层中间件同形的扁平错误。
        return flat_error_json(SERVICE_UNAVAILABLE_INFO)
    return Response(
        content=payload,
        media_type="application/json",
        status_code=status,
        headers=WEB_JSON_HEADERS,
    )


# Web 浏览端全部 JSON 响应的显式字段投影：白名单外字段（含未来新增的输出字段）
# 不出端点，成功、失败与早期失败载荷同口径。
_WEB_PROJECTION_KEYS = frozenset(
    {
        "tool",
        "success",
        "status",
        "error",
        "directory",
        "count",
        "total_count",
        "offset",
        "has_more",
        "next_offset",
        "stall_permanent",
        "rescan_futile",
        "skipped_count",
        "images",
        "recursive",
        "max_depth",
        "limit",
        "format_filter",
        "show_details",
    }
)


def project_browse_payload(structured: dict[str, Any]) -> None:
    """按投影白名单裁剪字段。"""
    for key in structured.keys() - _WEB_PROJECTION_KEYS:
        del structured[key]


# 错误载荷的出端投影按 tool_name 查表：未登记工具全字段输出，generate 端点有意不投影。
_WEB_PROJECTIONS: Mapping[str, Callable[[dict[str, Any]], None]] = {
    BROWSE_IMAGES_TOOL_NAME: project_browse_payload,
}


async def respond_structured_error(tool_name: str, error_type: str, message: str) -> Response:
    """经输出族映射构造失败结构化结果并回 JSON 响应，状态码按状态表查得。"""
    structured = build_error_structured(tool_name, error_type, message)
    projection = _WEB_PROJECTIONS.get(tool_name)
    if projection is not None:
        projection(structured)
    return await respond_structured_json(structured, _error_status(error_type))


def converge_path_entry(
    item: dict[str, object], key: str, images_root: Path, *, resolve: bool = False
) -> None:
    """把条目的路径键改写为图片目录相对形态并附 web_path，越界删除该键。

    resolve 为 True 时先解析为物理路径再相对化（输入为任意本地路径）；False 时
    输入须已是 resolve 后的绝对路径，做纯词法相对化。generate 与 gallery 的
    条目收敛共用，改写规则单点维护。
    """
    value = item.get(key)
    if not isinstance(value, str) or not value:
        return
    if resolve:
        try:
            # normalize_path 携带 UNC/NUL/ADS 等全套预拒绝，异常归一为 ValueError。
            path = normalize_path(value)
        except ValueError:
            del item[key]
            return
    else:
        path = Path(value)
    relative = images_root_relative(path, images_root)
    if relative is None:
        del item[key]
        return
    item["web_path"] = relative
    item[key] = relative


class _TruncatedSourceError(RuntimeError):
    """多区间响应中读源被截断时主动断开连接的中止信号。"""


def _abort_on_empty_chunk(send: Send) -> Send:
    """包装多区间发送：零长分块意味着读源截断，抛中止信号断开连接。"""

    async def _send(message: Message) -> None:
        if (
            message["type"] == "http.response.body"
            and message.get("more_body")
            and not message.get("body")
        ):
            raise _TruncatedSourceError("multipart 区间读到零字节，读源已截断")
        await send(message)

    return _send


class _DupAtOpenFd:
    """上游 open 时刻才生产复制品 fd 的整数替身，交出即归上游文件对象独占关闭。"""

    def __init__(self, fd: int) -> None:
        self._source_fd = fd
        self._duplicate: int | None = None

    def __index__(self) -> int:
        # open() 对 fd 槽做多次整数转换，复制品只生产一次。
        if self._duplicate is None:
            self._duplicate = os.dup(self._source_fd)
        return self._duplicate


class _NoFollowFileResponse(FileResponse):
    """以预开 no-follow 句柄为读源的 FileResponse，读取与应答语义单源于上游。

    分叉仅五处：chunk_size 加大、pathsend 禁用、倒置区间加严 400、多区间读源
    截断改断连、错误状态剥离 Range。读取走上游 open 时刻才生产的复制品 fd，
    其开关归上游发送循环的 async with，正常与异常路径均由其 finally 关闭；
    原句柄为响应独占，__call__ 收尾同步兜底关闭，句柄对象自身的 closed 幂等
    簿记使关闭无双重释放与号码误回收。
    """

    chunk_size = 512 * 1024

    def __init__(
        self,
        handle: IO[bytes],
        stat_result: os.stat_result,
        *,
        media_type: str,
        headers: Mapping[str, str] | None = None,
        status_code: int = 200,
    ) -> None:
        super().__init__(
            _DupAtOpenFd(handle.fileno()),  # type: ignore[arg-type]
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            stat_result=stat_result,
        )
        self._handle = handle

    @classmethod
    def _parse_ranges(cls, range_: str, file_size: int) -> list[tuple[int, int]]:
        ranges = super()._parse_ranges(range_, file_size)
        # 倒置区间经 merge 折叠后无法检出，校验须落在原始区间上；起点越界的留给上游 416。
        if any(0 <= start < file_size and start >= end for start, end in ranges):
            raise MalformedRangeHeader("Range header: start must be less than end")
        return ranges

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # RFC 9110 §14.2：Range 仅适用于成功表示；错误状态页剥离 Range 头，整体按原状态应答。
        if self.status_code >= 400:
            headers = scope.get("headers", [])
            stripped = [pair for pair in headers if pair[0] != b"range"]
            if len(stripped) != len(headers):
                scope = {**scope, "headers": stripped}
        try:
            await super().__call__(scope, receive, send)
        finally:
            # 同步关闭无 await，收尾不可被取消，已定结局不被改写。
            self._close_handle()

    def _close_handle(self) -> None:
        """原句柄关闭兜底，失败仅记录不改写已送达的响应结局。"""
        if self._handle.closed:
            return
        try:
            self._handle.close()
        except Exception:
            logger.opt(exception=True).warning("预开句柄关闭失败，不影响已送达的响应")

    async def _handle_simple(self, send: Send, send_header_only: bool, send_pathsend: bool) -> None:
        # pathsend 把文件按路径移交服务器重开，绕过预开句柄，一律不参与。
        await super()._handle_simple(send, send_header_only, False)

    async def _handle_multiple_ranges(
        self,
        send: Send,
        ranges: list[tuple[int, int]],
        file_size: int,
        send_header_only: bool,
    ) -> None:
        await super()._handle_multiple_ranges(
            _abort_on_empty_chunk(send), ranges, file_size, send_header_only
        )
