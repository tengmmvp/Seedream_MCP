"""MCP Roots 会话取回：能力探测、roots/list 直连与 resolver 注入应用。

roots 取回有两种形态：工具链经 server 层 Resolve 依赖注入（SEP-2577 非废弃
形态）与资源处理器在 2026-07-28 及以后的会话上经 InputRequiredResult 多轮取回，
结果均由 workspace_roots_scope_from_result 应用；旧修订会话的资源处理器经
read_session_roots_result 的 roots/list 直连取回后共用同一应用。组内依赖
io_roots → io_path 单向，工作区状态经 io_path 的公共访问器置位。
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from contextvars import Token
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.shared.exceptions import NoBackChannelError
from mcp.types import ListRootsResult

from ..core.logs import get_logger
from .io_path import (
    apply_workspace_roots,
    has_windows_colon_component,
    is_unc_path,
    is_windows_reserved_name,
    reset_workspace_roots,
)

logger = get_logger()

# roots/list 请求的显式短超时：不设超时将依赖会话层读超时，慢客户端或半开连接会把
# 工具调用拖到分钟级；超时按读取失败处理，工作根目录经声明链回退。
_ROOTS_LIST_TIMEOUT_SECONDS = 5.0


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
    """记录 roots 读取失败，本次工作根目录经声明链回退。"""
    logger.error("{}: {}，本次请求的工作根目录经声明链回退", reason, exc)


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
    """旧修订会话经 roots/list 直连读取客户端 roots 结果。

    无请求上下文（session 属性抛 ValueError）、ctx 为 None、session 无可用
    list_roots 或未声明 roots capability 时返回 None 不发起取回；NoBackChannelError
    与其余取回异常记录后同样返回 None，工作区边界交由调用方回退声明链。显式
    短超时防慢客户端拖住请求。
    """
    if ctx is None:
        return None

    # 无请求上下文的 Context 其 session 属性抛 ValueError，须显式捕获以维持
    # 「回退环境变量边界」的承诺。
    session: Any
    try:
        session = ctx.session
    except ValueError:
        return None
    list_roots: Any = getattr(session, "list_roots", None)
    if not callable(list_roots):
        return None
    if not session_declares_roots_capability(session):
        logger.debug("客户端未声明 roots capability，跳过 roots 取回，回退环境变量边界")
        return None
    try:
        return await asyncio.wait_for(list_roots(), timeout=_ROOTS_LIST_TIMEOUT_SECONDS)
    except NoBackChannelError as exc:
        # 协议能力缺失而非瞬时失败，重试不会好转，提级为 error。
        _log_roots_read_failure(exc, "协议会话无反向通道，无法读取 MCP Roots")
    except Exception as exc:
        _log_roots_read_failure(exc, "读取 MCP Roots 失败")
    return None


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
    由 SDK 在调用层报错而非在此降级，不放宽文件访问边界。file URI 转 Path 的
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

    candidate = Path(path_part)
    # 有根无盘符形态在 win32 锚定当前盘根而非可判定的绝对位置，与 normalize_path
    # 同口径拒绝；POSIX 无 drive 概念，绝对路径恒放行。
    if sys.platform == "win32" and candidate.root and not candidate.drive:
        return None
    # 两类冒号畸形按完整路径判定：整路径为盘符相对形态（c:ads 的 c: 被解析为盘符、
    # 裸文件名判定漏拒）与含冒号的普通分量（NTFS ADS）；保留设备名与 normalize_path
    # 同口径拒绝，畸形形态不成为工作区 root。
    if (candidate.drive and not candidate.root) or has_windows_colon_component(str(candidate)):
        return None
    if is_windows_reserved_name(candidate.name):
        return None

    try:
        return candidate.expanduser().resolve()
    except Exception:
        return None
