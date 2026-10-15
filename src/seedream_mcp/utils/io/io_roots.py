"""MCP Roots 会话取回：协商判定、能力探测、roots/list 直连与注入应用。"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from contextvars import Token
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError, NoBackChannelError
from mcp.shared.message import ServerMessageMetadata
from mcp.types import REQUEST_TIMEOUT, ListRootsRequest, ListRootsResult
from mcp.types.version import is_version_at_least

from ..core.logs import get_logger
from .io_path import (
    apply_workspace_roots,
    has_null_byte,
    has_windows_colon_component,
    is_drive_relative,
    is_unc_path,
    is_windows_reserved_name,
    is_windows_rooted_without_drive,
    reset_workspace_roots,
)

logger = get_logger()

# roots/list 取回的整体超时上界：SDK 读超时在出站写完成后才起算，流控暂停的
# 写会让请求无限挂起。
_ROOTS_LIST_TIMEOUT_SECONDS = 5.0

# roots 经 InputRequiredResult 多轮取回所需的最低协商版本：旧修订会话无法序列化
# 该结果类型，客户端会收到 -32603。
_MODERN_PROTOCOL_VERSION = "2026-07-28"


def session_or_none(ctx: Any) -> Any:
    """返回会话对象，脱离请求上下文或无会话时返回 None。

    SDK 在脱离请求上下文访问 ctx.session 时抛 ValueError，此处统一捕获。
    """
    try:
        return ctx.session
    except ValueError:
        return None


def modern_revision_negotiated(ctx: Any) -> bool:
    """协商版本不低于 2026-07-28 时返回 True，协议版本缺失按旧修订处理。"""
    version = getattr(ctx, "protocol_version", None)
    return isinstance(version, str) and is_version_at_least(version, _MODERN_PROTOCOL_VERSION)


def roots_back_channel_available(session: Any) -> bool:
    """判定旧修订会话的反向通道可用性，供 roots/list 直连取回前探测。

    无状态传输等无反向通道场景返回 False 以跳过必失败的取回，旧版 SDK 与
    测试替身无该属性时保守视为可用。
    """
    try:
        return bool(session.can_send_request)
    except AttributeError:
        return True


def session_declares_roots_capability(session: Any) -> bool:
    """判断会话对端客户端是否声明了 roots capability。

    据此可跳过必然失败的 roots 取回线上往返；check_client_capability 不可达或
    探测异常时保守视为已声明，交由取回本身裁决，不静默降级到环境边界。
    """
    check_capability = getattr(session, "check_client_capability", None)
    if not callable(check_capability):
        return True
    try:
        from mcp.types import ClientCapabilities, RootsCapability

        declared = check_capability(ClientCapabilities(roots=RootsCapability()))
    except Exception as exc:
        logger.warning("roots capability 探测异常，按已声明尝试取回: {}", exc)
        return True
    return bool(declared)


def _log_roots_read_failure(exc: Exception, reason: str) -> None:
    """记录 roots 读取失败及其堆栈，本次工作根目录经声明链回退。"""
    logger.opt(exception=True).error("{}: {}，本次请求的工作根目录经声明链回退", reason, exc)


def _apply_roots_token(resolved_roots: list[Path]) -> Token[tuple[Path, ...] | None]:
    """将已解析的 Roots 置位到请求上下文变量并记录边界日志，返回复位用 token。

    workspace_roots_scope_from_result 的置位收尾入口：非空 Roots 记录已应用
    工作区，空 Roots 等同未声明。
    """
    token = apply_workspace_roots(tuple(resolved_roots))
    if resolved_roots:
        logger.debug("已应用 MCP Roots 工作区: {}", resolved_roots)
    else:
        logger.debug("MCP Roots 为空，等同未声明，工作根目录经声明链回退")
    return token


async def read_session_roots_result(ctx: Any) -> ListRootsResult | None:
    """旧修订资源路径的 roots 直连取回，无会话、未声明能力、无反向通道或取回失败时降级返回 None。"""
    if ctx is None:
        return None
    session = session_or_none(ctx)
    if session is None:
        return None
    if not session_declares_roots_capability(session):
        logger.debug("客户端未声明 roots capability，跳过 roots 取回，回退环境变量边界")
        return None
    if not roots_back_channel_available(session):
        logger.debug("会话无反向通道，跳过 roots 取回，回退环境变量边界")
        return None
    try:
        return await _request_session_roots(ctx, session)
    except NoBackChannelError as exc:
        # 协议能力缺失而非瞬时失败，重试不会好转，提级为 error。
        _log_roots_read_failure(exc, "协议会话无反向通道，无法读取 MCP Roots")
    except Exception as exc:
        _log_roots_read_failure(exc, "读取 MCP Roots 失败")
    return None


def _request_id_or_none(ctx: Any) -> str | None:
    """返回当前请求 id，脱离请求上下文或替身无该属性时返回 None。"""
    try:
        request_id: str | None = ctx.request_id
        return request_id
    except (AttributeError, ValueError):
        return None


def _is_roots_read_timeout(exc: BaseException) -> bool:
    """判定取回异常是否超时形态：直连 TimeoutError 或 SDK 读超时的 REQUEST_TIMEOUT。"""
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(exc, MCPError) and exc.code == REQUEST_TIMEOUT


async def _request_session_roots(ctx: Any, session: Any) -> ListRootsResult:
    """roots/list 直连取回私有核心，异常保留原始类型由两条路径分别归类。"""
    send_request: Any = getattr(session, "send_request", None)
    if not callable(send_request):
        raise AttributeError("会话无 send_request 方法，无法读取 MCP Roots")
    # related_request_id 使请求经请求方自己的流送达，SSE 无常驻 GET 流时
    # 连接级 outbound 会被直接丢弃。
    metadata: ServerMessageMetadata | None = None
    request_id = _request_id_or_none(ctx)
    if request_id is not None:
        metadata = ServerMessageMetadata(related_request_id=request_id)
    # 整体上界覆盖 SDK 读超时不计的出站写阶段，request_read_timeout_seconds 留作内层再设防。
    return await asyncio.wait_for(
        send_request(
            ListRootsRequest(),
            ListRootsResult,
            request_read_timeout_seconds=_ROOTS_LIST_TIMEOUT_SECONDS,
            metadata=metadata,
        ),
        timeout=_ROOTS_LIST_TIMEOUT_SECONDS,
    )


async def read_session_roots_or_raise(ctx: Any, session: Any) -> ListRootsResult:
    """旧修订会话经 roots/list 直连取回客户端 roots，送达走请求自身通道，无通道、超时或失败时抛 ToolError。"""
    try:
        return await _request_session_roots(ctx, session)
    except Exception as exc:
        if _is_roots_read_timeout(exc):
            raise ToolError(f"读取 MCP Roots 超时（{_ROOTS_LIST_TIMEOUT_SECONDS:g} 秒）") from exc
        raise ToolError(f"读取 MCP Roots 失败: {exc}") from exc


def resource_roots_via_input_required(ctx: Any) -> bool:
    """判定资源处理器是否应经 InputRequiredResult 多轮形态取回 roots。

    会话可访问、已声明 roots capability 且协商版本不低于 2026-07-28 时返回
    True，否则返回 False。
    """
    session = session_or_none(ctx)
    if session is None or not session_declares_roots_capability(session):
        return False
    return modern_revision_negotiated(ctx)


def roots_degraded(roots_result: Any, applied_roots: list[Path]) -> bool:
    """降级标记单点：声明了非空 roots 但无一可应用即降级；空声明等同未声明不标。

    结果为 None 视为取回失败，不标记：capability 探测异常时按已声明尝试，
    取回失败未必代表客户端真的声明过，标记会误报。
    """
    return bool(getattr(roots_result, "roots", [])) and not applied_roots


def _roots_result_to_paths(roots_result: Any) -> list[Path]:
    """将 ListRootsResult 转换为去重后的本地路径列表。

    各 Root 的 file:// URI 转为本地路径，UNC 形式被 _file_uri_to_path 拒绝以避免
    触发 SMB 连接，不可解析或重复的条目跳过。会话直连与 resolver 注入两条取回
    路径共用本转换。
    """
    resolved_roots: list[Path] = []
    for root in getattr(roots_result, "roots", []):
        uri_value = getattr(root, "uri", None)
        if uri_value is None:
            continue
        resolved_path = _file_uri_to_path(str(uri_value))
        if resolved_path is None:
            continue
        if resolved_path in resolved_roots:
            continue
        resolved_roots.append(resolved_path)

    return resolved_roots


@asynccontextmanager
async def workspace_roots_scope_from_result(
    roots_result: ListRootsResult | None,
) -> AsyncIterator[list[Path]]:
    """在当前请求作用域内应用经工具 resolver 注入的 MCP Roots。

    SEP-2577 非废弃形态：工具链不经 ctx.session.list_roots 直连，由 server 工具
    签名的 Resolve 依赖按协商版本取回后注入本函数消费。roots_result 为 None 表示
    客户端未声明 roots capability，此处不设置边界、下游回退环境变量根；取回失败
    在调用层报错而非在此降级，不放宽文件访问边界。file URI 转 Path 的
    resolve 属同步文件系统调用，下沉工作线程执行，与 browse 链路的目录预解析
    同一口径。

    Args:
        roots_result: 依赖解析器注入的客户端 roots 结果；未声明能力时为 None。

    Yields:
        当前请求解析出的工作区根目录列表；未声明能力时为空列表。
    """
    token: Token[tuple[Path, ...] | None] | None = None
    resolved_roots: list[Path] = []
    if roots_result is not None:
        resolved_roots = await asyncio.to_thread(_roots_result_to_paths, roots_result)
        token = _apply_roots_token(resolved_roots)
    else:
        logger.debug("客户端未声明 roots capability，跳过 roots 取回，回退环境变量边界")

    try:
        yield resolved_roots
    finally:
        if token is not None:
            reset_workspace_roots(token)


# ==================== file URI 转换 ====================


def _file_uri_to_path(uri: str) -> Path | None:
    """将 file:// URI 转换为本地路径，畸形形态与 normalize_path 同口径拒绝。"""
    try:
        parsed = urlparse(uri)
    except Exception:
        return None

    if (parsed.scheme or "").lower() != "file":
        return None

    try:
        path_part = url2pathname(parsed.path or "")
    except Exception:
        # Python 3.14 起 url2pathname 对非 localhost authority 的 file URI 直接抛
        # URLError，POSIX 上的 //server/share 形态同样如此，语义同为拒绝，归一为
        # None。
        return None
    netloc = parsed.netloc or ""
    if netloc and netloc.lower() != "localhost":
        # 拒绝 UNC 路径如 file://host/share，避免 Windows 下触发 SMB 连接泄露凭据。
        return None

    if not path_part:
        return None

    # file://localhost//server/share 等 netloc 合法但 path 为 UNC 形式，resolve 会触发 SMB。
    if is_unc_path(path_part):
        return None
    # 与 normalize_path 同口径显式拒绝空字节：Py3.13+ 的 resolve 不再对其抛错。
    if has_null_byte(path_part):
        return None

    candidate = Path(path_part)
    # 有根无盘符形态在 win32 锚定当前盘根而非可判定的绝对位置，与 normalize_path
    # 同口径拒绝；判定经 is_windows_rooted_without_drive 共用单一来源，POSIX 无
    # drive 概念，绝对路径恒放行。
    if is_windows_rooted_without_drive(candidate):
        return None
    # win32 的 nturl2path 已把 /c:ads 先行转为 c:\ads，盘符相对形态经转换不可达，
    # 该分支仅为与 normalize_path 的拒绝口径对齐保留；含冒号的普通分量（NTFS ADS）
    # 仍按完整路径判定拒绝，畸形形态不成为工作区 root。
    if is_drive_relative(candidate) or has_windows_colon_component(str(candidate)):
        return None
    # 保留设备名拒绝仅 win32 生效，POSIX 上 con 等为合法目录名。
    if sys.platform == "win32" and is_windows_reserved_name(candidate.name):
        return None

    try:
        return candidate.expanduser().resolve()
    except Exception:
        return None
