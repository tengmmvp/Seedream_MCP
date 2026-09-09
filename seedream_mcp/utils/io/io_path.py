"""Seedream MCP 路径处理工具：目录体系的位置求值与读权限判定。

位置求值按 docs/development/directory-system.md：数据根目录 =
SEEDREAM_DATA_ROOT > 工作根目录；工作根目录 = MCP Roots 首项 >
SEEDREAM_WORKSPACE_ROOT > 进程启动目录 > 用户主目录；图片目录恒为
``<数据根目录>/.seedream/images``。
读权限 = 工作区 ∪ 图片目录。
提供路径规范化、越界判定原语，拦截包含 ``..`` 或经由符号链接指向权限范围
之外的路径。roots 取回有三种形态：工具链经 server 层 Resolve 依赖注入
（SEP-2577 非废弃形态）、资源处理器在 2026-07-28 及以后的会话上经
InputRequiredResult 多轮取回，均由 workspace_roots_scope_from_result 应用；
旧修订会话保留 workspace_roots_scope 的 roots/list 直连。另提供目录图片
查找与拼写相近路径建议。
"""

from __future__ import annotations

import asyncio
import heapq
import os
import sys
import tempfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.shared.exceptions import NoBackChannelError
from mcp.types import ListRootsResult

from ..core.errors import SeedreamConfigError
from ..core.formats import DATA_DIR_NAME, SUPPORTED_IMAGE_EXTENSIONS
from ..core.logs import get_logger
from .io_file import has_reparse_attribute
from .io_scan import cached_find_images_in_directory

logger = get_logger()

_WORKSPACE_ROOTS_VAR: ContextVar[tuple[Path, ...] | None] = ContextVar(
    "seedream_workspace_roots",
    default=None,
)

# roots/list 请求的显式短超时：不设超时将依赖会话层读超时，慢客户端或半开连接会把
# 工具调用拖到分钟级；超时按读取失败处理，工作根目录经声明链回退。
_ROOTS_LIST_TIMEOUT_SECONDS = 5.0

# 目录扫描的物化条目预算：单次 find_images_in_directory 调用物化的条目（含非
# 图片，重扫趟只计新增物化段）累计超过该值即停止遍历，约束递归广度与前缀物化量；
# heapq.nsmallest 选前缀须消费整个目录迭代器，单目录单趟枚举不在此预算内，由
# io_scan 的 mtime+TTL 缓存缓解重复扫描。
_SCAN_ENTRY_BUDGET = 20000

# Windows 保留设备名清单：CON/NUL/COM1 等作最终分量时被解释为设备而非文件，
# normalize_path 的拒绝与 io_storage 的文件名净化经 is_windows_reserved_name 共用。
WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)


def is_windows_reserved_name(name: str) -> bool:
    """判断文件名是否命中 Windows 保留设备名。

    Windows 解析前剥离前导点与首尾空格并取首个点前词干，CON.txt、con. 与 .CON
    同样命中；normalize_path 的拒绝与 io_storage 的文件名净化共用本判定。

    Args:
        name: 待判定的文件名。

    Returns:
        词干命中保留设备名清单返回 True。
    """
    normalized_stem = name.lstrip(". ").split(".", 1)[0].strip(". ")
    return normalized_stem.upper() in WINDOWS_RESERVED_NAMES


# 已 resolve 回退根的进程级缓存：首次探测结果复用到进程结束，消除回退边界下
# 每次位置求值的重复探测。
_fallback_root: Path | None = None

# 已 resolve 回退根的进程级缓存，键为配置原始字符串，消除回退边界下每次文件访问的
# 重复 expanduser/resolve 文件系统调用。仅缓存 expanduser 后为绝对路径的配置值：
# 相对路径的 resolve 结果随进程 CWD 变化，须每次现算；解析失败不缓存，下次访问
# 重试。活动配置变更时经 clear_resolved_env_root_cache 显式失效。
_RESOLVED_ENV_ROOT_CACHE: dict[str, Path] = {}

# 已 resolve 数据根目录与图片目录的进程级缓存，缓存口径与回退根一致。键三类：
# "data-root:"+数据根目录声明、"explicit-images:"+数据根目录声明、
# "default-images:"+工作根目录字符串，前缀隔离防撞车；默认分支的键含客户端可控
# 的 roots 字符串，按 LRU 限容防轮换 roots 的无界增长。活动配置变更时随
# clear_resolved_env_root_cache 一并失效。
_RESOLVED_DATA_ROOT_CACHE: OrderedDict[str, Path] = OrderedDict()
_DATA_ROOT_CACHE_MAX_ENTRIES = 64

# 位置声明提供者：由 config 模块加载时注入，返回活动配置的原始字符串，未配置
# 返回 None；依赖方向为 config 向下注入，本模块不向上 import。两个声明各占一槽，
# 键为对应的环境变量名，取值先调提供者，未注册即 config 模块未加载时回退读取
# 同名环境变量。
EnvValueProvider = Callable[[], str | None]

_WORKSPACE_ROOT_ENV = "SEEDREAM_WORKSPACE_ROOT"
_DATA_ROOT_ENV = "SEEDREAM_DATA_ROOT"

# 读取范围授权指引的环境变量尾段，浏览与参考图读取的用户可见提示及 Web 端配置
# 指引共用，env 改名时单点维护。
READ_SCOPE_AUTH_ENV_HINT = f"{_WORKSPACE_ROOT_ENV} 或 {_DATA_ROOT_ENV}"


def read_scope_denial_message(noun: str) -> str:
    """构造越出读取范围的拒绝消息，noun 取目录或路径。"""
    return (
        f"{noun}不在读取范围内；可通过客户端工作区（MCP Roots）、"
        f"{READ_SCOPE_AUTH_ENV_HINT} 授权该{noun}"
    )


_env_value_providers: dict[str, EnvValueProvider] = {}


# ==================== 工作区根目录管理 ====================


def _configured_env_value(env_var: str) -> str | None:
    """取位置声明的原始配置值：注册的提供者优先，未注册时回退同名环境变量。"""
    provider = _env_value_providers.get(env_var)
    if provider is not None:
        return provider()
    env_value = os.getenv(env_var)
    return env_value.strip() if env_value else None


def register_env_workspace_root_provider(provider: EnvValueProvider) -> None:
    """注册工作区根目录提供者，config 侧在模块加载时注入取值入口。

    Args:
        provider: 返回活动配置的 workspace_root 原始字符串，未配置返回 None。
    """
    _env_value_providers[_WORKSPACE_ROOT_ENV] = provider


def register_data_root_provider(provider: EnvValueProvider) -> None:
    """注册数据根目录提供者，config 侧在模块加载时注入取值入口。

    Args:
        provider: 返回活动配置的 data_root 原始字符串，未配置返回 None。
    """
    _env_value_providers[_DATA_ROOT_ENV] = provider


def clear_resolved_env_root_cache() -> None:
    """清空已 resolve 配置路径与回退根的进程级缓存。

    覆盖回退工作区根、数据根目录与回退根三处缓存。活动配置变更时由
    config 侧调用，测试隔离经 conftest 复位协议调用，使后续访问按新配置与
    当前环境重新解析。
    """
    global _fallback_root
    _RESOLVED_ENV_ROOT_CACHE.clear()
    _RESOLVED_DATA_ROOT_CACHE.clear()
    _fallback_root = None


def _resolve_with_cache(cache_key: str, resolver: Callable[[], Path]) -> Path:
    """按键缓存 resolve 结果，命中移到链尾保持真 LRU，超容逐出最旧条目。"""
    cached_dir = _RESOLVED_DATA_ROOT_CACHE.get(cache_key)
    if cached_dir is not None:
        _RESOLVED_DATA_ROOT_CACHE.move_to_end(cache_key)
        return cached_dir
    resolved_dir = resolver()
    _RESOLVED_DATA_ROOT_CACHE[cache_key] = resolved_dir
    while len(_RESOLVED_DATA_ROOT_CACHE) > _DATA_ROOT_CACHE_MAX_ENTRIES:
        _RESOLVED_DATA_ROOT_CACHE.popitem(last=False)
    return resolved_dir


def resolve_cached_data_root(configured_dir: str) -> Path:
    """解析已显式配置的数据根目录，按 "data-root:"+配置串做进程级缓存。

    缓存口径与回退工作区根一致，随 clear_resolved_env_root_cache 一并失效。

    Args:
        configured_dir: 配置的数据根目录原始字符串。

    Returns:
        resolve 后的数据根目录。
    """
    expanded_dir = Path(configured_dir).expanduser()
    if not expanded_dir.is_absolute():
        return expanded_dir.resolve()
    return _resolve_with_cache(f"data-root:{configured_dir}", lambda: expanded_dir.resolve())


def resolve_cached_default_images_root(workspace_root: Path) -> Path:
    """解析工作根目录下的默认图片目录，按 "default-images:"+工作根目录字符串做进程级缓存。

    与显式配置分支共用 _RESOLVED_DATA_ROOT_CACHE，随
    clear_resolved_env_root_cache 一并失效。

    Args:
        workspace_root: 已 resolve 的工作根目录。

    Returns:
        resolve 后的 workspace_root/.seedream/images。
    """
    return _resolve_with_cache(
        f"default-images:{workspace_root}",
        lambda: (workspace_root / DATA_DIR_NAME / "images").resolve(),
    )


def resolve_cached_explicit_images_root(configured_dir: str) -> Path:
    """解析显式声明的图片目录整条路径，绝对声明按 "explicit-images:"+配置串做进程级缓存。

    相对声明不缓存，与 resolve_cached_data_root 的缓存口径一致，按调用时的
    进程位置重新解析。

    Args:
        configured_dir: 配置的数据根目录原始字符串。

    Returns:
        resolve 后的 <数据根目录>/.seedream/images；.seedream 为符号链接时
        尾部 resolve 使结果与其指向一致。
    """
    expanded_dir = Path(configured_dir).expanduser()
    if not expanded_dir.is_absolute():
        return (expanded_dir / DATA_DIR_NAME / "images").resolve()
    return _resolve_with_cache(
        f"explicit-images:{configured_dir}",
        lambda: (resolve_cached_data_root(configured_dir) / DATA_DIR_NAME / "images").resolve(),
    )


def _resolve_configured_root() -> Path | None:
    """解析已配置的工作区根目录，未配置或解析失败返回 None。

    供 resolve_env_workspace_root 的声明链兜底取配置根。UNC 声明与
    resolve_images_root 同口径在 resolve 前拒绝。

    Raises:
        SeedreamConfigError: 工作区根目录声明为 UNC 形态。
    """
    configured_root = _configured_env_value(_WORKSPACE_ROOT_ENV)
    if not configured_root:
        return None
    reject_unc_declaration(configured_root, label="工作区根目录")
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


def _resolve_home_root() -> Path:
    """解析用户主目录，供回退链的最终回退。

    Returns:
        已 resolve 的用户主目录。

    Raises:
        SeedreamConfigError: 主目录不可解析（剥离 HOME 的容器、无 passwd 条目的
            任意 UID），属部署环境缺陷而非调用方参数错误，携带配置指引归配置
            错误档案。
    """
    try:
        return Path.home().resolve()
    except (RuntimeError, OSError) as exc:
        raise SeedreamConfigError(
            f"无法确定用户主目录，请配置 {READ_SCOPE_AUTH_ENV_HINT}",
        ) from exc


def _usable_cwd_root() -> Path | None:
    """返回可写的进程启动目录；UNC 形态或不可写返回 None。

    可写性以创建并删除临时探测文件实测，不在目录留下副作用。
    """
    try:
        cwd = Path.cwd()
    except OSError:
        return None
    if is_unc_path(str(cwd)):
        logger.debug("进程启动目录为 UNC 形态，不作为回退根")
        return None
    try:
        fd, probe_name = tempfile.mkstemp(prefix=".seedream-probe-", dir=cwd)
    except OSError:
        return None
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(probe_name)
    except OSError:
        pass
    try:
        return cwd.resolve()
    except OSError:
        return None


# 启动期消息缓冲：日志目录求值可能早于日志系统初始化，回退提示先进队列，由
# drain_pending_start_messages 在 setup_logging 之后冲刷，不经 loguru 导入期
# 默认 sink 输出。
_pending_start_messages: list[tuple[str, str]] = []


def drain_pending_start_messages() -> None:
    """输出并清空启动期缓冲的回退提示。"""
    pending = _pending_start_messages[:]
    _pending_start_messages.clear()
    for level, message in pending:
        logger.log(level, message)


def _resolve_fallback_root() -> Path:
    """解析无任何声明时的回退根：进程启动目录可写时采用，否则回退用户主目录。

    成功结果进程级缓存。回退提示进启动缓冲队列，不经 loguru 导入期默认
    sink 输出。

    Returns:
        已 resolve 的回退根目录。

    Raises:
        SeedreamConfigError: 进程启动目录与用户主目录均不可用，携带配置指引归
            配置错误档案。
    """
    global _fallback_root
    if _fallback_root is not None:
        return _fallback_root
    cwd_root = _usable_cwd_root()
    if cwd_root is not None:
        root = cwd_root
        message = (
            "未声明 MCP Roots 且未配置 SEEDREAM_WORKSPACE_ROOT，工作根目录回退为"
            "进程启动目录 {}，默认图片目录为 {}"
        )
    else:
        root = _resolve_home_root()
        message = "进程启动目录不可用作回退根，工作根目录回退为用户主目录 {}，默认图片目录为 {}"
    _fallback_root = root
    _pending_start_messages.append(
        ("INFO", message.format(str(root), str(root / DATA_DIR_NAME / "images")))
    )
    return root


def resolve_env_workspace_root() -> Path:
    """解析环境回退的工作根目录：已配置根，无配置时走回退链。

    无任何声明时落进程启动目录，不可写回退用户主目录；首次回退记录
    一次日志提示默认数据位置。

    Returns:
        已 resolve 的工作根目录；无任何配置时为回退链结果。

    Raises:
        SeedreamConfigError: 无配置且工作目录与用户主目录均不可用。
    """
    resolved_root = _resolve_configured_root()
    if resolved_root is not None:
        return resolved_root
    return _resolve_fallback_root()


def resolve_log_file_path() -> Path:
    """求值日志文件路径，启动期单点实现。

    顺位为 SEEDREAM_DATA_ROOT > SEEDREAM_WORKSPACE_ROOT > 回退链，不读取
    会话级 MCP Roots。无任何声明时的回退提示进启动缓冲队列，由
    drain_pending_start_messages 在日志系统就绪后输出。

    Returns:
        日志文件完整路径。

    Raises:
        SeedreamConfigError: 声明值非法或回退链整体不可解析。
    """
    configured_data_root = _configured_env_value(_DATA_ROOT_ENV)
    if configured_data_root:
        reject_unc_declaration(configured_data_root, label="数据根目录")
        base = resolve_cached_data_root(configured_data_root)
    else:
        base = resolve_env_workspace_root()
    return base / DATA_DIR_NAME / "logs" / "seedream_mcp.log"


def resolve_images_root() -> Path:
    """求值图片目录：数据根目录恒为显式声明或工作根目录，图片目录统一派生自
    <数据根目录>/.seedream/images。

    显式声明与工作根目录派生两分支的 resolve 结果分别经进程级缓存，配置写入
    路径统一使缓存失效。

    Returns:
        resolve 后的图片目录。

    Raises:
        SeedreamConfigError: 数据根目录声明为 UNC 形态或无法解析（路径非法或
            超长），或工作根目录声明链不可解析，均属部署配置缺陷归配置错误档案。
    """
    configured = _configured_env_value(_DATA_ROOT_ENV)
    if configured:
        reject_unc_declaration(configured, label="数据根目录")
        try:
            # 显式声明与工作根目录同为数据根目录，图片目录统一派生自其 .seedream 子目录。
            return resolve_cached_explicit_images_root(configured)
        except (OSError, RuntimeError, ValueError) as exc:
            # 异常原文嵌 expanduser 展开后的服务器绝对路径，仅进日志；用户消息只
            # 回显其自行配置的原始值。
            logger.error("数据根目录配置无法解析 '{}': {}", configured, exc)
            raise SeedreamConfigError(f"数据根目录配置无法解析: {configured}") from exc
    return resolve_cached_default_images_root(get_workspace_root())


def get_read_context() -> tuple[list[Path], Path, list[Path]]:
    """一次求值 (工作区声明集合, 图片目录, 读权限)，供读取链各消费方共享单次结果。

    工作区声明链不可解析（无 Roots 与环境根且主目录不可解析）时工作区为空、
    读权限退化为仅图片目录并记录，显式数据根目录声明可用即不整体失败；图片
    目录自身不可解析时照常上抛。
    """
    images_root = resolve_images_root()
    try:
        workspace_roots = get_workspace_roots()
    except SeedreamConfigError as exc:
        logger.error("工作根目录声明链不可解析，读权限退化为仅图片目录: {}", exc.message)
        workspace_roots = []
    scope = list(workspace_roots)
    if images_root not in scope:
        scope.append(images_root)
    return workspace_roots, images_root, scope


def get_read_scope() -> list[Path]:
    """读权限集合 = 工作区声明集合 ∪ 图片目录，求值细节见 get_read_context。"""
    return get_read_context()[2]


def get_workspace_roots() -> list[Path]:
    """获取当前请求生效的工作根目录声明集合。

    会话声明的非空 Roots 为工作区；空声明等同未声明，回退环境配置根或用户
    主目录。

    Returns:
        当前请求生效的工作根目录列表。
    """
    roots_from_context = _WORKSPACE_ROOTS_VAR.get()
    if roots_from_context:
        return list(roots_from_context)
    return [resolve_env_workspace_root()]


def get_workspace_root() -> Path:
    """获取当前请求的工作根目录：Roots 首项或环境回退根，多 Roots 时取首项。

    Raises:
        ValueError: 防御分支，回退链恒产出非空集合，正常不触发。
    """
    workspace_roots = get_workspace_roots()
    if not workspace_roots:
        raise ValueError("当前 MCP 会话未授权任何工作区目录")
    return workspace_roots[0]


async def _resolve_workspace_roots_from_context(ctx: Any) -> list[Path]:
    """从 MCP 上下文读取客户端 Roots 并转换为本地路径列表。"""
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


def _log_roots_read_failure(exc: Exception, reason: str) -> None:
    """记录 roots 读取失败，本次工作根目录经声明链回退。"""
    logger.error("{}: {}，本次请求的工作根目录经声明链回退", reason, exc)


def _apply_roots_token(resolved_roots: list[Path]) -> Token[tuple[Path, ...] | None]:
    """将已解析的 Roots 置位到请求上下文变量并记录边界日志，返回复位用 token。

    workspace_roots_scope_from_result 与 workspace_roots_scope 的置位收尾共用，
    日志语义两侧一致：非空 Roots 记录已应用工作区，空 Roots 等同未声明。
    """
    token = _WORKSPACE_ROOTS_VAR.set(tuple(resolved_roots))
    if resolved_roots:
        logger.debug("已应用 MCP Roots 工作区: {}", resolved_roots)
    else:
        logger.debug("MCP Roots 为空，等同未声明，工作根目录经声明链回退")
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
        logger.debug("客户端未声明 roots capability，跳过 roots 取回，回退环境变量边界")

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
    唯一途径）。客户端 Roots 设置到上下文变量作为该请求的工作区；未声明
    roots capability 时跳过 roots/list 往返；读取失败仅记录，工作位置经声明链
    回退。

    Args:
        ctx: MCP 请求上下文，经其 session 读取客户端 Roots。

    Yields:
        当前请求解析出的工作区根目录列表；客户端不支持 Roots 时为空列表。
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
        logger.debug("客户端未声明 roots capability，跳过 roots 取回，回退环境变量边界")
        roots_supported = False

    if roots_supported:
        try:
            resolved_roots = await _resolve_workspace_roots_from_context(ctx)
        except NoBackChannelError as exc:
            # 协议能力缺失而非瞬时失败，重试不会好转，提级为 error。
            _log_roots_read_failure(exc, "协议会话无反向通道，无法读取 MCP Roots")
        except Exception as exc:
            _log_roots_read_failure(exc, "读取 MCP Roots 失败")
        else:
            token = _apply_roots_token(resolved_roots)

    try:
        yield resolved_roots
    finally:
        if token is not None:
            _WORKSPACE_ROOTS_VAR.reset(token)


# ==================== 路径验证和规范化 ====================


def is_within_resolved(path_resolved: Path, base_resolved: Path) -> bool:
    """判断已 resolve 的路径是否位于已 resolve 的基目录内。

    直接做 relative_to 比较，不再重复 resolve。供循环场景复用以避免重复解析。

    Args:
        path_resolved: 已 resolve 的待判定路径。
        base_resolved: 已 resolve 的基目录。

    Returns:
        路径等于基目录或位于其内返回 True，否则返回 False。
    """
    try:
        path_resolved.relative_to(base_resolved)
        return True
    except ValueError:
        return False


def is_unc_path(path_str: str) -> bool:
    """判断是否为 Windows UNC 路径，前两个分隔符字符为斜杠或反斜杠的组合。

    混合形态仅在 win32 判为 UNC，POSIX 反斜杠是合法文件名字符；统一分隔符
    形态各平台一致拒绝。UNC 的 resolve 在 Windows 触发 SMB 认证，须在
    resolve 前拦截。io_path 与 images 组的候选守卫共用本判定。
    """
    stripped = path_str.lstrip()
    if len(stripped) < 2:
        return False
    first, second = stripped[0], stripped[1]
    if first not in "\\/" or second not in "\\/":
        return False
    return first == second or sys.platform == "win32"


def reject_unc_declaration(value: str, *, label: str, env_hint: str = "") -> None:
    """目录声明为 UNC 形态时抛配置错误，各声明入口在 resolve 前统一拒绝。

    Args:
        value: 原始声明值。
        label: 错误消息中的目录称呼。
        env_hint: 追加到错误消息的环境变量提示后缀，可为空串。

    Raises:
        SeedreamConfigError: 声明值为 UNC 形态。
    """
    if is_unc_path(value):
        raise SeedreamConfigError(f"{label}不支持 UNC 路径: {value}{env_hint}")


def has_windows_colon_component(path: str) -> bool:
    """判断 win32 下路径是否存在含冒号的分量，即 NTFS 备用数据流形态。

    驱动器符与其带根形态为合法前缀，跳过；非 win32 平台恒返回 False，冒号在
    POSIX 是合法文件名字符。normalize_path 与参考图读取链共用本判定，保持
    单一规则。

    Args:
        path: 输入路径字符串，可为相对或绝对。

    Returns:
        win32 下存在含冒号的路径分量返回 True；非 win32 平台或无冒号分量返回 False。
    """
    if sys.platform != "win32":
        return False
    path_obj = Path(path)
    drive = path_obj.drive
    for part in path_obj.parts:
        if part == drive or part == drive + path_obj.root:
            continue
        if ":" in part:
            return True
    return False


def normalize_path(path: str, base_dir: str | None = None) -> Path:
    """标准化文件路径为绝对 Path 对象。

    win32 平台剥离最终分量尾部的点与空格，使返回的路径名与实际打开的文件名一致。

    Args:
        path: 输入路径，可为相对或绝对。
        base_dir: 基目录，用于解析相对路径。

    Raises:
        ValueError: 路径为 UNC 形式、Windows 驱动器相对形式、Windows 路径分量含冒号、
            最终分量为 Windows 保留设备名或路径无效时抛出。
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
            raise ValueError(f"拒绝驱动器相对路径以避免绕过基目录解析: {path}")

        # 有根无盘符形态有 root 无 drive，is_absolute 判为 False 会被当相对路径拼
        # 基目录，但 pathlib 拼接对该形态锚定重置、静默写出基目录所在盘的盘根之外，
        # 与驱动器相对同口径在 resolve 前拒绝；POSIX 无 drive，该形态即合法绝对
        # 路径，恒不触发。
        if sys.platform == "win32" and path_obj.root and not path_obj.drive:
            raise ValueError(f"拒绝有根无盘符路径以避免绕过基目录解析: {path}")

        # Win32 命名空间打开文件时剥离最终分量尾部的点与空格，先做同口径名称归一，
        # 已验证路径字符串才与实际打开的文件名一致；仅名称级归一，不改变越界判定。
        if sys.platform == "win32":
            # 分量含冒号是 NTFS 备用数据流形态，流名不参与越界判定，逐分量拒绝；
            # 驱动器符与其带根形态为合法前缀，判定经 has_windows_colon_component
            # 与参考图读取链共用单一来源。
            if has_windows_colon_component(path):
                raise ValueError(f"拒绝路径分量含冒号以避免访问 NTFS 备用数据流: {path}")
            final_name = path_obj.name
            polished_name = final_name.rstrip(". ") if final_name else final_name
            if polished_name and polished_name != final_name:
                path_obj = path_obj.with_name(polished_name)
            # Windows 保留设备名作最终分量会被解释为设备而非文件，判定经
            # is_windows_reserved_name 与 sanitize_filename 单一来源。
            if is_windows_reserved_name(polished_name):
                raise ValueError(f"拒绝 Windows 保留设备名以避免被解释为设备而非文件: {path}")

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


def images_root_relative(path: str | Path, images_root: Path) -> str | None:
    """返回路径相对图片目录的正斜杠形态，供 Web 端点改写条目路径共用。

    Args:
        path: 待相对化的路径。
        images_root: 已 resolve 的图片目录。

    Returns:
        正斜杠相对路径字符串；路径落在图片目录外时为 None。
    """
    try:
        return Path(path).relative_to(images_root).as_posix()
    except ValueError:
        return None


def find_images_in_directory(
    directory: str,
    recursive: bool = True,
    max_depth: int = 3,
    extensions: list[str] | None = None,
    limit: int | None = None,
    unreadable_dirs: list[Path] | None = None,
    truncated_dirs: list[Path] | None = None,
) -> list[Path]:
    """在目录中查找图片文件。

    安全前置条件：本函数不做工作区越界校验，调用方必须先确认 directory 位于允许
    的工作区根之内。UNC 形式的入参与 normalize_path 同口径在 resolve 前拒绝，返回
    空列表并记录告警。
    单次调用物化的条目数（含非图片）受 _SCAN_ENTRY_BUDGET 预算封顶，重扫趟只计
    新增物化段使倍增摊销不翻倍计数，超限丢弃当批、终止遍历并返回已收集结果，截断
    的目录追加至 truncated_dirs 供调用方感知结果不完整；heapq.nsmallest 选前缀须
    消费整个目录迭代器，单目录单趟枚举不在此预算内。
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
        truncated_dirs: 可选收集列表，扫描因条目预算截断时追加截断目录，供调用方
            区分「扫完全量」与「截断的部分结果」。

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
        # 与 normalize_path 等 resolve 站点同口径在 resolve 前拦截。
        logger.warning("拒绝 UNC 形式的目录扫描入参，返回空结果: {}", directory)
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

    scanned_entries = 0

    def scan_directory(path: Path, current_depth: int = 0) -> bool:
        """按 normcase 稳定顺序深度优先扫描；凑够 target_count 或条目超预算即返回 True 终止。"""
        nonlocal scanned_entries
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
                            prefix_len,
                            it,
                            key=lambda entry: os.path.normcase(entry.path),
                        )
                    else:
                        entries = sorted(it, key=lambda entry: os.path.normcase(entry.path))
            except OSError as e:
                logger.warning("无法访问目录 {}: {}", path, e)
                if unreadable_dirs is not None:
                    unreadable_dirs.append(path)
                return False

            # 条目预算按物化量累计：前缀重扫趟只按新增段计费，目录条目数可超预算
            # 而分页不中断；超限丢弃本批并终止遍历。
            scanned_entries += max(len(entries) - consumed, 0)
            if scanned_entries > _SCAN_ENTRY_BUDGET:
                logger.warning(
                    "目录扫描条目数超过预算 {}，停止遍历并返回已收集结果: {}",
                    _SCAN_ENTRY_BUDGET,
                    path,
                )
                if truncated_dirs is not None:
                    truncated_dirs.append(path)
                return True

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
                    # junction 等 reparse 非 symlink，is_dir 不拒绝，与文件分支同口径
                    # 复用遍历条目的 no-follow stat 判定；POSIX 上短路跳过 stat 求值。
                    if sys.platform == "win32" and has_reparse_attribute(
                        entry.stat(follow_symlinks=False)
                    ):
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

    扫描经 io_scan 的 mtime/TTL 缓存，重复的校验失败不重复全量扫描目录。

    Args:
        target_path: 目标路径。
        search_dirs: 搜索目录列表；未提供时返回空列表，强制调用方显式指定边界，
            避免公开导出后以 CWD 为界泄露本地图片文件名。

    Returns:
        相似路径建议列表（resolve 后路径），最多 5 条。目标文件名归一为空串时
        不产生建议，避免空串子串匹配误报。
    """
    suggestions: list[str] = []

    try:
        target_name = Path(target_path).name.lower()
        if not target_name:
            return suggestions
        search_directories = search_dirs or []

        for search_dir in search_directories:
            # UNC 形式的搜索目录在 resolve 前跳过，与 find_images_in_directory 的
            # 入参拦截同口径，避免建议扫描触发 SMB 连接。
            if is_unc_path(search_dir):
                logger.warning("拒绝 UNC 形式的建议搜索目录，跳过该目录: {}", search_dir)
                continue
            resolved_dir = Path(search_dir).resolve()
            matched_pairs = cached_find_images_in_directory(
                resolved_dir=resolved_dir,
                recursive=True,
                max_depth=2,
                format_filter=None,
                scan_limit=500,
                scanner=find_images_in_directory,
            )

            for _, resolved in matched_pairs:
                if target_name in resolved.name.lower():
                    suggestions.append(str(resolved))

                if len(suggestions) >= 5:
                    break

            if len(suggestions) >= 5:
                break

    except Exception as e:
        logger.error("生成路径建议失败: {}", e)

    return suggestions


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
