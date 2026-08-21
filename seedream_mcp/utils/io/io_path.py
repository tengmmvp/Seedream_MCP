"""Seedream MCP 路径处理工具：MCP 工作区 Roots 边界与路径越界校验。

以 MCP Roots 作为文件访问边界，提供路径规范化与越界判定原语，拦截包含 ``..``
或经由符号链接指向工作区之外的路径；无 Roots 时回退 SEEDREAM_WORKSPACE_ROOT
环境变量。roots 取回有三种形态：工具链经 server 层 Resolve 依赖注入（SEP-2577
非废弃形态）、资源处理器在 2026-07-28 及以后的会话上经 InputRequiredResult
多轮取回，均由 workspace_roots_scope_from_result 应用；旧修订会话保留
workspace_roots_scope 的 roots/list 直连。另提供目录图片查找与拼写相近路径建议。
"""

from __future__ import annotations

import asyncio
import heapq
import os
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.shared.exceptions import NoBackChannelError
from mcp.types import ListRootsResult

from ..core.errors import SeedreamMCPError
from ..core.formats import SUPPORTED_IMAGE_EXTENSIONS
from ..core.logs import get_logger
from .io_file import has_reparse_attribute, is_reparse_point

logger = get_logger()

_WORKSPACE_ROOTS_VAR: ContextVar[tuple[Path, ...] | None] = ContextVar(
    "seedream_workspace_roots",
    default=None,
)

# roots/list 请求的显式短超时：不设超时将依赖会话层读超时，慢客户端或半开连接会把
# 工具调用拖到分钟级；超时按读取失败进入回退判定，无环境变量根时 fail-closed。
_ROOTS_LIST_TIMEOUT_SECONDS = 5.0

# 回退 CWD 告警只记录一次；无 Roots 时本解析随每次文件访问触发，逐次告警会淹没日志。
_cwd_fallback_warned = False

# 已 resolve 回退根的进程级缓存，键为配置原始字符串，消除回退边界下每次文件访问的
# 重复 expanduser/resolve 文件系统调用。仅缓存 expanduser 后为绝对路径的配置值：
# 相对路径的 resolve 结果随进程 CWD 变化，须每次现算；解析失败不缓存，下次访问
# 重试。活动配置变更时经 clear_resolved_env_root_cache 显式失效。
_RESOLVED_ENV_ROOT_CACHE: dict[str, Path] = {}

# 已 resolve 自动保存基础目录的进程级缓存，键与失效口径与回退根缓存一致，供 tools
# 层的默认保存目录解析复用，消除已显式配置保存根时每次生成请求的重复
# expanduser/resolve 文件系统调用。
_RESOLVED_SAVE_BASE_DIR_CACHE: dict[str, Path] = {}

# 工作区根目录提供者：由 config 模块加载时注入，返回活动配置的 workspace_root
# 原始字符串，未配置返回 None；依赖方向为 config 向下注入，本模块不向上 import。
EnvWorkspaceRootProvider = Callable[[], str | None]
_env_workspace_root_provider: EnvWorkspaceRootProvider | None = None


# ==================== 工作区根目录管理 ====================


def register_env_workspace_root_provider(provider: EnvWorkspaceRootProvider) -> None:
    """注册工作区根目录提供者，config 侧在模块加载时注入取值入口。

    Args:
        provider: 返回活动配置的 workspace_root 原始字符串，未配置返回 None。
    """
    global _env_workspace_root_provider
    _env_workspace_root_provider = provider


def clear_resolved_env_root_cache() -> None:
    """清空已 resolve 配置路径的进程级缓存。

    覆盖回退工作区根与自动保存基础目录两处缓存。活动配置变更时由 config 侧调用，
    使后续访问按新配置重新解析。
    """
    _RESOLVED_ENV_ROOT_CACHE.clear()
    _RESOLVED_SAVE_BASE_DIR_CACHE.clear()


def resolve_cached_save_base_dir(configured_dir: str) -> Path:
    """解析已配置的自动保存基础目录，按配置原始字符串做进程级缓存。

    缓存口径与回退根一致：仅缓存 expanduser 后为绝对路径的配置值，相对路径的
    resolve 结果随进程 CWD 变化须每次现算；解析异常向上抛出且不缓存，下次调用
    重试。缓存随 clear_resolved_env_root_cache 失效。

    Args:
        configured_dir: 配置的自动保存基础目录原始字符串。

    Returns:
        resolve 后的保存基础目录。
    """
    expanded_dir = Path(configured_dir).expanduser()
    if not expanded_dir.is_absolute():
        return expanded_dir.resolve()
    cached_dir = _RESOLVED_SAVE_BASE_DIR_CACHE.get(configured_dir)
    if cached_dir is not None:
        return cached_dir
    resolved_dir = expanded_dir.resolve()
    _RESOLVED_SAVE_BASE_DIR_CACHE[configured_dir] = resolved_dir
    return resolved_dir


def _resolve_configured_root() -> Path | None:
    """解析已配置的工作区根目录，未配置或解析失败返回 None。

    供 resolve_env_workspace_root 的 CWD 回退判定与 workspace_roots_scope 的
    NoBackChannelError fail-closed 判定共用。expanduser 后为绝对路径的配置根按
    原始字符串缓存 resolve 结果；相对形态与解析失败不缓存，下次访问现算或重试。
    """
    configured_root = _configured_workspace_root()
    if not configured_root:
        return None
    try:
        expanded_root = Path(configured_root).expanduser()
        cacheable = expanded_root.is_absolute()
        cached_root = _RESOLVED_ENV_ROOT_CACHE.get(configured_root) if cacheable else None
        if cached_root is not None:
            return cached_root
        resolved_root = expanded_root.resolve()
    except Exception as e:
        logger.warning("无效的工作区根目录配置 '{}': {}", configured_root, e)
        return None
    if cacheable:
        _RESOLVED_ENV_ROOT_CACHE[configured_root] = resolved_root
    return resolved_root


def resolve_env_workspace_root() -> Path:
    """解析工作区根目录，失败时回退当前工作目录。

    本地开发无 MCP Roots 时的文件访问边界回退。无可用配置根回退进程 CWD 时记录
    一次告警，提示边界已放宽为整个工作目录。

    Returns:
        已 resolve 的工作区根目录；无任何配置时为进程当前工作目录。
    """
    global _cwd_fallback_warned
    resolved_root = _resolve_configured_root()
    if resolved_root is not None:
        return resolved_root
    if not _cwd_fallback_warned:
        _cwd_fallback_warned = True
        logger.warning(
            "未配置 MCP Roots 与 SEEDREAM_WORKSPACE_ROOT，文件访问边界回退为进程当前工作目录 {}",
            Path.cwd().resolve(),
        )
    return Path.cwd().resolve()


def _configured_workspace_root() -> str | None:
    """返回已配置的工作区根目录原始值，未配置返回 None。

    优先调用 config 侧注册的提供者读取活动配置的 workspace_root；提供者未注册即
    config 模块未加载时，回退读取 SEEDREAM_WORKSPACE_ROOT 环境变量。
    """
    provider = _env_workspace_root_provider
    if provider is not None:
        return provider()
    env_root = os.getenv("SEEDREAM_WORKSPACE_ROOT")
    return env_root.strip() if env_root else None


def get_workspace_roots() -> list[Path]:
    """获取当前请求生效的工作区根目录列表。

    优先使用 MCP Roots 作为文件访问边界，无 Roots 时回退环境变量配置。

    Returns:
        当前请求生效的工作区根目录列表。
    """
    roots_from_context = _WORKSPACE_ROOTS_VAR.get()
    if roots_from_context is not None:
        return list(roots_from_context)
    return [resolve_env_workspace_root()]


def get_workspace_root() -> Path:
    """获取当前请求默认工作区根目录，取 Roots 首项或环境变量目录。

    Raises:
        ValueError: 当前请求未授权任何工作区目录。
    """
    workspace_roots = get_workspace_roots()
    if not workspace_roots:
        raise ValueError("当前 MCP 会话未授权任何工作区目录")
    return workspace_roots[0]


def is_boundary_from_session_roots() -> bool:
    """判断当前请求的工作区边界是否来自客户端会话 Roots 声明。

    经 SEEDREAM_WORKSPACE_ROOT 或进程 CWD 回退取得的边界属服务器环境而非客户端
    授权声明，其绝对路径不进入面向调用方的输出。

    Returns:
        来自会话 Roots 声明返回 True，环境变量或 CWD 回退返回 False。
    """
    return _WORKSPACE_ROOTS_VAR.get() is not None


def resolve_workspace_roots(roots: Sequence[Path | str]) -> list[Path]:
    """将工作区根目录列表归一为 Path 列表，保持入参顺序。

    入参在产出时已完成 resolve，本函数不再重复 resolve，仅做 Path 归一。

    Args:
        roots: 已 resolve 的工作区根目录列表，元素可为 Path 或路径字符串。
    """
    return [Path(root) for root in roots]


async def _resolve_workspace_roots_from_context(ctx: Any) -> list[Path]:
    """从 MCP 上下文读取客户端 Roots 并转换为本地路径列表。

    将各 Root 的 file:// URI 转为本地路径，拒绝 UNC 形式以避免触发 SMB 连接。
    """
    if ctx is None:
        return []

    session = getattr(ctx, "session", None)
    list_roots = getattr(session, "list_roots", None)
    if session is None or not callable(list_roots):
        return []

    roots_result = await asyncio.wait_for(list_roots(), timeout=_ROOTS_LIST_TIMEOUT_SECONDS)
    return await asyncio.to_thread(_roots_result_to_paths, roots_result)


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


def session_declares_roots_capability(session: Any) -> bool:
    """判断会话对端客户端是否声明了 roots capability。

    据此可跳过必然失败的 roots 取回线上往返；check_client_capability 不可达或探测
    异常时保守视为已声明，保持旧版 SDK 与测试替身下的原有行为。
    """
    check_capability = getattr(session, "check_client_capability", None)
    if not callable(check_capability):
        return True
    try:
        from mcp.types import ClientCapabilities, RootsCapability

        declared = check_capability(ClientCapabilities(roots=RootsCapability()))
    except Exception:
        return True
    return bool(declared)


def _fallback_roots_or_fail_closed(exc: Exception, reason: str) -> None:
    """读取 roots 失败后的共用回退处置：已配置环境变量根时保持空 roots 回退该边界。

    未配置环境变量根时回退会放宽文件访问边界到进程 CWD，抛 SeedreamMCPError
    拒绝进入作用域；已配置则提级 error 日志并保持空 roots，由下游的环境变量
    兜底链路取得显式边界。

    Raises:
        SeedreamMCPError: 未配置 SEEDREAM_WORKSPACE_ROOT 且读取失败。
    """
    configured_root = _resolve_configured_root()
    if configured_root is None:
        logger.error("{}，且无环境变量工作区根可用: {}", reason, exc)
        raise SeedreamMCPError(
            f"{reason}，且未配置 SEEDREAM_WORKSPACE_ROOT，" "拒绝将文件访问边界放宽到进程工作目录"
        ) from exc
    logger.error("{}，回退环境变量边界 {}: {}", reason, configured_root, exc)


def _apply_roots_token(resolved_roots: list[Path]) -> Token[tuple[Path, ...] | None]:
    """将已解析的 Roots 置位到请求上下文变量并记录边界日志，返回复位用 token。

    workspace_roots_scope_from_result 与 workspace_roots_scope 的置位收尾共用，
    日志语义两侧一致：非空 Roots 记录已应用边界，空 Roots 按无本地目录权限处理。
    """
    token = _WORKSPACE_ROOTS_VAR.set(tuple(resolved_roots))
    if resolved_roots:
        logger.debug("已应用 MCP Roots 边界: {}", resolved_roots)
    else:
        logger.debug("MCP Roots 为空，当前请求按无本地目录权限处理")
    return token


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
        logger.debug("客户端未声明 roots capability，未发起 roots 取回，回退环境变量边界")

    try:
        yield resolved_roots
    finally:
        if token is not None:
            _WORKSPACE_ROOTS_VAR.reset(token)


@asynccontextmanager
async def workspace_roots_scope(ctx: Any) -> AsyncIterator[list[Path]]:
    """在当前请求作用域内绑定 MCP Roots，退出时自动恢复。

    资源处理器在旧修订会话上的取回入口：新修订会话改由 server 层经
    InputRequiredResult 多轮取回后走 workspace_roots_scope_from_result，本函数
    承接旧修订会话的 ctx.session.list_roots 直连（SEP-2577 废弃但为旧修订上
    唯一途径）。客户端 Roots 设置到上下文变量作为该请求的文件访问边界；未声明
    roots capability 时跳过 roots/list 往返，回退环境变量边界；读取失败的回退
    判定见 Raises。

    Args:
        ctx: MCP 请求上下文，经其 session 读取客户端 Roots。

    Yields:
        当前请求解析出的工作区根目录列表；客户端不支持 Roots 时为空列表。

    Raises:
        SeedreamMCPError: roots/list 读取失败（协议会话无反向通道或超时等瞬时
            失败）且无环境变量工作区根可用，进入作用域前抛出，使该请求的本地
            文件操作失败而非放宽边界到进程工作目录。
    """
    token: Token[tuple[Path, ...] | None] | None = None
    resolved_roots: list[Path] = []

    # 无请求上下文的 Context 其 session 属性抛 ValueError，须显式捕获以维持
    # 「回退环境变量边界」的承诺；ctx 为 None 的直调场景按无会话处理。
    session = None
    if ctx is not None:
        try:
            session = ctx.session
        except ValueError:
            session = None
    list_roots = getattr(session, "list_roots", None) if session is not None else None
    roots_supported = session is not None and callable(list_roots)
    if roots_supported and not session_declares_roots_capability(session):
        logger.debug("客户端未声明 roots capability，跳过 roots/list，回退环境变量边界")
        roots_supported = False

    if roots_supported:
        try:
            resolved_roots = await _resolve_workspace_roots_from_context(ctx)
        except NoBackChannelError as exc:
            # 协议能力缺失而非瞬时失败，重试不会好转，提级为 error；回退判定与
            # 瞬时失败共用 _fallback_roots_or_fail_closed。
            _fallback_roots_or_fail_closed(exc, "协议会话无反向通道，无法读取 MCP Roots")
        except Exception as exc:
            # 超时等瞬时读取失败：回退会放宽文件访问边界，与无反向通道同判定。
            _fallback_roots_or_fail_closed(exc, "读取 MCP Roots 失败")
        else:
            token = _apply_roots_token(resolved_roots)

    try:
        yield resolved_roots
    finally:
        if token is not None:
            _WORKSPACE_ROOTS_VAR.reset(token)


# ==================== 路径验证和规范化 ====================


def is_within_resolved(path_resolved: Path, base_resolved: Path) -> bool:
    """判断已 resolve 的路径是否位于已 resolve 的基础目录内。

    直接做 relative_to 比较，不再重复 resolve。供循环场景复用以避免重复解析。

    Args:
        path_resolved: 已 resolve 的待判定路径。
        base_resolved: 已 resolve 的基础目录。

    Returns:
        路径等于基础目录或位于其内返回 True，否则返回 False。
    """
    try:
        path_resolved.relative_to(base_resolved)
        return True
    except ValueError:
        return False


def is_unc_path(path_str: str) -> bool:
    """判断是否为 Windows UNC 路径，即以 \\\\ 或 // 开头的路径。

    UNC 路径的 resolve 在 Windows 会触发 SMB 认证，须在 resolve 前拦截，
    避免越界校验尚未拒绝时凭据已向远端泄露。io_path 内部与 images 组的
    候选守卫共用本公共判定，保持单一规则。
    """
    stripped = path_str.lstrip()
    return stripped.startswith("\\\\") or stripped.startswith("//")


def normalize_path(path: str, base_dir: str | None = None) -> Path:
    """标准化文件路径为绝对 Path 对象。

    win32 平台剥离最终分量尾部的点与空格，使返回的路径名与实际打开的文件名一致。

    Args:
        path: 输入路径，可为相对或绝对。
        base_dir: 基础目录，用于解析相对路径。

    Raises:
        ValueError: 路径为 UNC 形式、Windows 驱动器相对形式或路径无效时抛出。
    """
    try:
        # 空字节在任何文件系统都不是合法路径分量。Python 3.13 起 Windows 的
        # resolve 对含空字节路径不再抛 ValueError 而是原样返回，此前依赖隐式异常
        # 拒绝的口径随之失效，改为入口显式拒绝保证跨版本行为一致。
        if "\x00" in path:
            raise ValueError(f"路径含空字节: {path}")
        path_obj = Path(path)

        # UNC 路径在 Windows 的 resolve 会触发 SMB 认证，须在 resolve 前拒绝。
        if is_unc_path(str(path_obj)):
            raise ValueError(f"拒绝 UNC 路径以避免触发 SMB 连接: {path}")

        # 驱动器相对路径有 drive 无 root，pathlib 拼接对该形态会丢弃 base_dir 落到
        # 该盘进程 CWD，与 UNC 同口径在 resolve 前拒绝；POSIX 无 drive 恒不触发。
        if path_obj.drive and not path_obj.root:
            raise ValueError(f"拒绝驱动器相对路径以避免绕过基础目录解析: {path}")

        # Win32 命名空间打开文件时剥离最终分量尾部的点与空格，先做同口径名称归一，
        # 已验证路径字符串才与实际打开的文件名一致；仅名称级归一，不改变越界判定。
        if sys.platform == "win32":
            final_name = path_obj.name
            polished_name = final_name.rstrip(". ") if final_name else final_name
            if polished_name and polished_name != final_name:
                path_obj = path_obj.with_name(polished_name)

        if path_obj.is_absolute():
            return path_obj.resolve()

        if base_dir:
            base_path = Path(base_dir)
            return (base_path / path_obj).resolve()
        else:
            return path_obj.resolve()

    except ValueError:
        # UNC 拒绝等 ValueError 原样抛出，保留具体原因。
        raise
    except OSError as e:
        # ENAMETOOLONG 等文件系统错误单独分支，errno 原因进入错误文案，供调用方
        # 区分拼写问题与系统级长度限制。
        logger.error("路径标准化失败 {}: {}", path, e)
        raise ValueError(f"无效的路径格式: {path} ({e})") from e
    except Exception as e:
        logger.error("路径标准化失败 {}: {}", path, e)
        raise ValueError(f"无效的路径格式: {path}") from e


def get_relative_path(path: str | Path, base_dir: str | None = None) -> str:
    """获取相对路径。

    Args:
        path: 文件路径。
        base_dir: 基础目录，默认为当前工作目录。

    Returns:
        相对路径字符串；无法相对化时回退绝对路径字符串。
    """
    try:
        path_obj = Path(path)
        base_path = Path(base_dir) if base_dir else Path.cwd()

        try:
            relative_path = path_obj.relative_to(base_path)
            return str(relative_path)
        except ValueError:
            # is_absolute 为纯词法判定，浏览链路传入的已 resolve 路径免除一次逐级 stat。
            if path_obj.is_absolute():
                return str(path_obj)
            return str(path_obj.resolve())

    except Exception as e:
        logger.error("获取相对路径失败 {}: {}", path, e)
        return str(path)


def find_images_in_directory(
    directory: str,
    recursive: bool = True,
    max_depth: int = 3,
    extensions: list[str] | None = None,
    limit: int | None = None,
    unreadable_dirs: list[Path] | None = None,
) -> list[Path]:
    """在目录中查找图片文件。

    安全前置条件：本函数不做工作区越界校验，调用方必须先确认 directory 位于允许
    的工作区根之内。UNC 形式的入参与 normalize_path 同口径在 resolve 前拒绝，返回
    空列表并记录告警。
    单个目录的条目列表按需物化：limit 场景只物化排序前缀，非图片条目占位致结果
    不足且目录未扫尽时倍增前缀重扫，无 limit 时一次物化全量有序列表；limit 亦使
    跨目录递归提前终止，重复扫描的成本由 io_scan 的 mtime 加 TTL 缓存缓解。

    Args:
        directory: 搜索目录。
        recursive: 是否递归搜索。
        max_depth: 最大搜索深度。
        extensions: 指定的文件扩展名列表。
        limit: 返回数量上限，<=0 时返回空列表；扫描按 normcase 稳定顺序，凑够即提前停止。
        unreadable_dirs: 可选收集列表，不可读目录追加至此供调用方区分「目录不可读」
            与「目录内无图片」；未提供时仅记日志跳过。

    Returns:
        找到的图片文件路径列表。目录不存在或预检失败时返回空列表。

    Raises:
        OSError: 扫描中途的文件系统错误向上传播，供调用方区分「扫完」与「中途
            出错」；单个目录不可读经 unreadable_dirs 收集后跳过，不视为失败。
    """
    images: list[Path] = []

    if limit is not None and limit <= 0:
        return images

    if is_unc_path(directory):
        # 与 normalize_path 等 resolve 站点同口径在 resolve 前拦截；告警不回显原始
        # 路径全文，与项目脱敏口径一致。
        logger.warning("拒绝 UNC 形式的目录扫描入参，返回空结果")
        return images

    try:
        dir_path = Path(directory).resolve()

        if not dir_path.exists() or not dir_path.is_dir():
            logger.warning("目录不存在或不是目录: {}", directory)
            return images
    except Exception as e:
        logger.error("搜索图片文件失败 {}: {}", directory, e)
        return images

    target_extensions = set(extensions) if extensions else SUPPORTED_IMAGE_EXTENSIONS
    target_extensions = {ext.lower() for ext in target_extensions}

    # 无上限时记为 -1 表示收集全部。
    target_count = limit if limit is not None else -1

    def scan_directory(path: Path, current_depth: int = 0) -> bool:
        """按 normcase 稳定顺序深度优先扫描；凑够 target_count 即返回 True 提前终止。"""
        if current_depth > max_depth:
            return False

        # 排序前缀按需扩展：heapq.nsmallest 与 sorted 前缀同序，物化量与前缀长度
        # 成正比而非目录全量；无 limit 时一次全量排序。
        prefix_len = target_count
        consumed = 0
        while True:
            try:
                with os.scandir(path) as it:
                    if target_count >= 0:
                        entries = heapq.nsmallest(
                            prefix_len, it, key=lambda entry: os.path.normcase(entry.path)
                        )
                    else:
                        entries = sorted(it, key=lambda entry: os.path.normcase(entry.path))
            except OSError as e:
                logger.warning("无法访问目录 {}: {}", path, e)
                if unreadable_dirs is not None:
                    unreadable_dirs.append(path)
                return False

            for entry in entries[consumed:]:
                entry_path = Path(entry.path)
                # follow_symlinks=False：不跟随符号链接，避免符号链接环与经由符号链接越界遍历。
                if (
                    entry.is_file(follow_symlinks=False)
                    and entry_path.suffix.lower() in target_extensions
                ):
                    # OneDrive 占位文件等 reparse 非 symlink，is_file 不拒绝，与目录分支
                    # 同规则剔除；复用遍历条目的 lstat 结果判定，不付逐文件二次 lstat，
                    # 后缀命中后才判定，非图片条目不付 stat 开销。reparse 判定仅
                    # Windows 有意义，POSIX 上短路跳过 stat 求值。
                    if sys.platform == "win32" and has_reparse_attribute(
                        entry.stat(follow_symlinks=False)
                    ):
                        logger.warning("跳过 reparse point 文件: {}", entry_path)
                        continue
                    images.append(entry_path)
                    if target_count >= 0 and len(images) >= target_count:
                        return True
                elif (
                    entry.is_dir(follow_symlinks=False) and recursive and current_depth < max_depth
                ):
                    if is_reparse_point(entry_path):
                        logger.warning("跳过 reparse point 目录: {}", entry_path)
                        continue
                    if scan_directory(entry_path, current_depth + 1):
                        return True

            if target_count < 0 or len(entries) < prefix_len:
                # 无 limit 或返回条目少于前缀长度即目录已扫尽，不存在可扩展前缀。
                return False
            consumed = prefix_len
            prefix_len *= 2

    scan_directory(dir_path)

    return images


def suggest_similar_paths(target_path: str, search_dirs: list[str] | None = None) -> list[str]:
    """在搜索目录下建议与目标路径拼写相近的图片路径，供路径校验失败时纠错。

    Args:
        target_path: 目标路径。
        search_dirs: 搜索目录列表；未提供时返回空列表，强制调用方显式指定边界，
            避免公开导出后以 CWD 为界泄露本地图片文件名。

    Returns:
        相似路径建议列表，最多 5 条。目标文件名归一为空串时不产生建议，避免空串
        子串匹配误报。
    """
    suggestions: list[str] = []

    try:
        target_name = Path(target_path).name.lower()
        if not target_name:
            return suggestions
        search_directories = search_dirs or []

        for search_dir in search_directories:
            images = find_images_in_directory(search_dir, recursive=True, max_depth=2, limit=500)

            for image_path in images:
                if target_name in image_path.name.lower():
                    suggestions.append(str(image_path))

                if len(suggestions) >= 5:
                    break

            if len(suggestions) >= 5:
                break

    except Exception as e:
        logger.error("生成路径建议失败: {}", e)

    return suggestions


# ==================== file URI 转换 ====================


def _file_uri_to_path(uri: str) -> Path | None:
    """将 file:// URI 转换为本地路径，拒绝 UNC 形式以避免触发 SMB 连接。"""
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

    try:
        return Path(path_part).expanduser().resolve()
    except Exception:
        return None
