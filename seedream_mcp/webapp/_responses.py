"""Web 操作台各域 handler 共享的响应辅助与错误码映射。

本模块只放跨域复用的纯辅助：统一错误 JSON 形态、图片目录解析契约、生成错误
类型到 HTTP 状态码的映射、文件端点的缓存头与 no-follow 文件响应形态。域内
专有逻辑不落此处，避免演化为杂物箱。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

from loguru import logger
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, MalformedRangeHeader, Response
from starlette.types import Message, Receive, Scope, Send

from ..tools.core.outputs import dump_compact_strict_json
from ..utils.core.errors import SeedreamConfigError
from ..utils.core.executors import (
    CpuOffloadPoolClosedError,
    run_in_cpu_pool,
    should_offload_size,
)
from ..utils.io.io_path import (
    READ_SCOPE_AUTH_ENV_HINT as READ_SCOPE_AUTH_ENV_HINT,
    images_root_relative,
    normalize_path,
    resolve_images_root,
)

GENERATION_ERROR_STATUS: dict[str, int] = {
    "validation_error": 400,
    "payload_too_large": 400,
    "rate_limited": 429,
    "payment_required": 402,
    "auth_error": 503,
    "config_error": 503,
    "network_error": 502,
    "api_error": 502,
    "timeout_error": 504,
}

# 缩略图与原图响应允许浏览器私有缓存：已保存图片内容不再变化；nosniff 阻断
# 旧浏览器把图片字节嗅探为其他类型。
PRIVATE_CACHE_HEADER = {
    "cache-control": "private, max-age=3600",
    "x-content-type-options": "nosniff",
}


# /web/api 全部 JSON 响应的公共安全头：no-store 防代理缓存动态状态，nosniff
# 封堵旧浏览器 MIME 嗅探反射面。
WEB_JSON_HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}


def error_json(error: str, description: str, status: int) -> JSONResponse:
    """构造与传输层中间件同形态的错误 JSON 响应。"""
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers=WEB_JSON_HEADERS,
    )


def cpu_pool_closed_json() -> JSONResponse:
    """CPU 卸载池关闭窗口的服务不可用响应，请求体解析与结构化序列化共用。

    池关闭属退出清理与服务并发的窗口，是服务端不可用而非请求或结果缺陷。
    """
    return error_json("service_unavailable", "CPU 卸载线程池已关闭，服务不可用", 500)


async def parse_json_object_body(request: Request) -> tuple[dict[str, Any], JSONResponse | None]:
    """解析请求体为 JSON 对象，解析失败或形态不符时返回 400 错误响应。

    解析成本随参考图体积线性增长，达到下沉尺寸阈值的卸载长时 CPU 专用池，低于
    阈值同步执行；池关闭取消排队解析时返回 500 服务不可用响应，生成与图库端点
    共用同一口径。
    """
    try:
        raw_body = await request.body()
        # 阈值之下的解析微秒级完成，线程往返成本高于收益，同步执行。
        if should_offload_size(len(raw_body)):
            body = await run_in_cpu_pool(json.loads, raw_body)
        else:
            body = json.loads(raw_body)
    except CpuOffloadPoolClosedError:
        return {}, cpu_pool_closed_json()
    # 深嵌套 JSON 触发解析器递归上限抛 RecursionError，与解析失败同归 400。
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        return {}, error_json("invalid_json", f"请求体不是合法 JSON: {exc}", 400)
    if not isinstance(body, dict):
        return {}, error_json("invalid_request", "请求体须为 JSON 对象", 400)
    return body, None


def validation_error_json(exc: ValidationError) -> JSONResponse:
    """把 pydantic 校验失败格式化为统一的首个错误描述响应。"""
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", ()))
    return error_json(
        "invalid_request",
        f"参数校验失败: {field or first.get('type')} {first.get('msg')}",
        400,
    )


def images_root_unavailable(exc: Exception) -> JSONResponse:
    """把图片目录解析失败包装为携带配置指引的 400 响应。"""
    message = getattr(exc, "message", None) or str(exc)
    return error_json(
        "images_root_unavailable",
        f"无法确定图片目录: {message}；可配置 {READ_SCOPE_AUTH_ENV_HINT}",
        400,
    )


async def resolve_web_images_root() -> Path | JSONResponse:
    """解析图片目录，含文件系统的解析下沉工作线程；不可解析时返回 400 响应。"""
    try:
        return await asyncio.to_thread(resolve_images_root)
    except SeedreamConfigError as exc:
        return images_root_unavailable(exc)


def structured_error_type(structured: dict[str, object]) -> str | None:
    """提取结构化结果的 error.type，缺失或形态不符返回 None。"""
    error = structured.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        if isinstance(error_type, str):
            return error_type
    return None


def generation_status(structured: dict[str, object]) -> int:
    """按结构化结果的错误类型映射 HTTP 状态码。"""
    error_type = structured_error_type(structured)
    if error_type is None:
        return 502
    return GENERATION_ERROR_STATUS.get(error_type, 502)


def browse_status(structured: dict[str, object]) -> int:
    """图库结构化结果的错误类型映射，validation_error 归 400 其余归 500。"""
    return 400 if structured_error_type(structured) == "validation_error" else 500


def dump_strict_json(structured: dict[str, object]) -> str:
    """以严格 JSON 序列化结构化结果，序列化形态与工具镜像共用同一原语。

    非有限浮点已在流水线用量净化处归零，严格拒绝仅作漏网哨兵。
    """
    return dump_compact_strict_json(structured)


async def respond_structured_json(structured: dict[str, object], status: int) -> Response:
    """以严格 JSON 构造结构化结果响应，序列化无条件下沉专用 CPU 池。"""
    try:
        payload = await run_in_cpu_pool(dump_strict_json, structured)
    except CpuOffloadPoolClosedError:
        return cpu_pool_closed_json()
    return Response(
        content=payload,
        media_type="application/json",
        status_code=status,
        headers=WEB_JSON_HEADERS,
    )


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

    分叉仅四处：chunk_size 加大、pathsend 禁用、倒置区间加严 400、多区间读源
    截断改断连。读取走上游 open 时刻才生产的复制品 fd，其开关归上游发送循环的
    async with，正常与异常路径均由其 finally 关闭；原句柄为响应独占，__call__
    收尾同步兜底关闭，句柄对象自身的 closed 幂等簿记使关闭无双重释放与号码误回收。
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
