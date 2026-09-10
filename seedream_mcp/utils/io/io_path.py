"""Seedream MCP 路径处理工具：目录体系的位置求值与读权限判定。

位置求值按 docs/development/directory-system.md：数据根目录 =
SEEDREAM_DATA_ROOT > 工作根目录；工作根目录 = MCP Roots 首项 >
SEEDREAM_WORKSPACE_ROOT > 进程启动目录 > 用户主目录；图片目录恒为
``<数据根目录>/.seedream/images``。
读权限 = 工作区 ∪ 图片目录。
提供路径规范化、越界判定原语，拦截包含 ``..`` 或经由符号链接指向权限范围
之外的路径。MCP Roots 的会话取回与应用位于同组 io_roots 模块，工作区状态经
本模块的上下文变量共享，置位与复位走 apply_workspace_roots/reset_workspace_roots。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Callable

from ..core.errors import SeedreamConfigError
from ..core.formats import DATA_DIR_NAME
from ..core.logs import EarlyMessageBuffer, get_logger

logger = get_logger()

_WORKSPACE_ROOTS_VAR: ContextVar[tuple[Path, ...] | None] = ContextVar(
    "seedream_workspace_roots",
    default=None,
)


def apply_workspace_roots(roots: tuple[Path, ...]) -> Token[tuple[Path, ...] | None]:
    """置位当前请求的工作区根集合，返回复位 token；写入口供 io_roots 专用。"""
    return _WORKSPACE_ROOTS_VAR.set(roots)


def reset_workspace_roots(token: Token[tuple[Path, ...] | None]) -> None:
    """按 apply_workspace_roots 返回的 token 复位工作区根集合。"""
    _WORKSPACE_ROOTS_VAR.reset(token)


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
_DATA_ROOT_CACHE_MAX_ENTRIES = 64


class ResolveResultCache:
    """带锁与可选 TTL 的 resolve 结果 LRU 缓存，配置根与扫描条目共用骨架。

    映射协议面向测试的预热与断言；TTL 为 None 时条目仅经 clear 与容量驱逐失效。
    """

    def __init__(self, max_entries: int, ttl_seconds: float | None = None) -> None:
        self._entries: OrderedDict[str, tuple[Path, float]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()

    def get_or_resolve(self, cache_key: str, resolver: Callable[[], Path]) -> Path:
        """命中且未过期返回缓存值，否则执行 resolver 并写入。"""
        now = time.monotonic() if self._ttl_seconds is not None else 0.0
        with self._lock:
            hit = self._entries.get(cache_key)
            if hit is not None:
                resolved, captured_at = hit
                if self._ttl_seconds is None or now - captured_at < self._ttl_seconds:
                    self._entries.move_to_end(cache_key)
                    return resolved
                del self._entries[cache_key]
        resolved = resolver()
        with self._lock:
            self._entries[cache_key] = (resolved, now)
            self._entries.move_to_end(cache_key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return resolved

    def clear(self) -> None:
        """清空全部条目。"""
        with self._lock:
            self._entries.clear()

    def __contains__(self, cache_key: object) -> bool:
        return cache_key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, cache_key: str) -> Path:
        return self._entries[cache_key][0]

    def __setitem__(self, cache_key: str, resolved: Path) -> None:
        self._entries[cache_key] = (resolved, time.monotonic())


_DATA_ROOT_RESOLVE_CACHE = ResolveResultCache(_DATA_ROOT_CACHE_MAX_ENTRIES)

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
    _DATA_ROOT_RESOLVE_CACHE.clear()
    _fallback_root = None


def _resolve_with_cache(cache_key: str, resolver: Callable[[], Path]) -> Path:
    """按键缓存 resolve 结果，命中移到链尾保持真 LRU，超容逐出最旧条目。"""
    return _DATA_ROOT_RESOLVE_CACHE.get_or_resolve(cache_key, resolver)


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

    与显式配置分支共用 _DATA_ROOT_RESOLVE_CACHE，随
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
_START_MESSAGES = EarlyMessageBuffer()


def drain_pending_start_messages() -> None:
    """输出并清空启动期缓冲的回退提示。"""
    _START_MESSAGES.drain()


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
    _START_MESSAGES.append("INFO", message.format(str(root), str(root / DATA_DIR_NAME / "images")))
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
