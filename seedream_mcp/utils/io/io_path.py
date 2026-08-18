"""Seedream MCP 路径处理工具：MCP 工作区 Roots 边界与路径越界校验。

以 MCP Roots 作为文件访问边界，提供路径规范化与越界判定原语，拦截包含 ``..``
或经由符号链接指向工作区之外的路径；无 MCP Roots 时回退 SEEDREAM_WORKSPACE_ROOT
环境变量。另提供目录图片查找与拼写相近路径建议。组合工作区边界与图像规则的
validate_image_path 位于 images 子包的 image_validation，本模块保持纯路径职责。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.shared.exceptions import NoBackChannelError

from ..core.errors import SeedreamMCPError
from ..core.formats import SUPPORTED_IMAGE_EXTENSIONS
from ..core.logs import get_logger
from .io_file import _is_reparse_point

logger = get_logger(__name__)

_WORKSPACE_ROOTS_VAR: ContextVar[tuple[Path, ...] | None] = ContextVar(
    "seedream_workspace_roots",
    default=None,
)

# roots/list 请求的显式短超时：每次工具调用前都有一次线上往返，不设超时将退化为依赖
# 会话层读超时，慢客户端或半开连接会把工具调用拖到分钟级；超时按读取失败回退环境变量边界。
_ROOTS_LIST_TIMEOUT_SECONDS = 5.0

# 回退 CWD 告警只记录一次；无 Roots 时本解析随每次文件访问触发，逐次告警会淹没日志。
_cwd_fallback_warned = False

# 已 resolve 回退根的进程级缓存，键为配置原始字符串。回退边界模式下每次文件访问都会
# 经过本解析，expanduser 与 resolve 属文件系统调用，缓存消除事件循环上的重复阻塞。
# 仅缓存 expanduser 后为绝对路径的配置值：相对路径的 resolve 结果随进程 CWD 变化，
# 以原始字符串为键会把首个 CWD 下的解析固化给后续访问，相对形态每次现算；~ 形态的
# 展开结果只依赖用户主目录环境，与进程 CWD 无关，展开后为绝对路径即可缓存。键含配置
# 原始值，配置变更自然产生新键；解析失败不缓存，下次访问重试。活动配置变更时经
# clear_resolved_env_root_cache 显式失效。
_RESOLVED_ENV_ROOT_CACHE: dict[str, Path] = {}

# 工作区根目录提供者：由 config 模块加载时经 register_env_workspace_root_provider
# 注入，返回活动配置的 workspace_root 原始字符串，未配置返回 None。本模块不向上
# import 顶层 config，配置值的读取方向由 config 向下注入。
EnvWorkspaceRootProvider = Callable[[], str | None]
_env_workspace_root_provider: EnvWorkspaceRootProvider | None = None


# ==================== 工作区根目录管理 ====================


def register_env_workspace_root_provider(provider: EnvWorkspaceRootProvider) -> None:
    """注册工作区根目录提供者，config 侧在模块加载时注入取值入口。

    依赖方向为 config 向下注入：提供者返回活动配置的 workspace_root 原始字符串，
    未配置返回 None；活动配置未就绪时的环境变量回退语义由提供者自身封装，与本模块
    未注册提供者时的回退一致。

    Args:
        provider: 工作区根目录提供者，返回活动配置的 workspace_root 原始字符串，
            未配置返回 None。
    """
    global _env_workspace_root_provider
    _env_workspace_root_provider = provider


def clear_resolved_env_root_cache() -> None:
    """清空已 resolve 回退根的进程级缓存。

    活动配置变更时由 config 侧的配置写入路径调用，使后续访问按新配置重新解析；
    测试复位协议经 set_active_config(None) 间接触发，跨用例不残留上个配置的解析结果。
    """
    _RESOLVED_ENV_ROOT_CACHE.clear()


def _resolve_configured_root() -> Path | None:
    """解析已配置的工作区根目录，未配置或解析失败返回 None。

    供 resolve_env_workspace_root 与 workspace_roots_scope 的 NoBackChannelError
    分支共用同一解析与缓存：前者以 None 决定 CWD 回退，后者以 None 判定无显式边界
    可回退并 fail-closed。expanduser 后为绝对路径的配置根按原始字符串缓存 resolve
    结果，同配置重复访问不再触达文件系统；相对形态不缓存，每次现算。解析失败不
    缓存，下次访问重试。
    """
    configured_root = _configured_workspace_root()
    if not configured_root:
        return None
    try:
        # expanduser 先行：~ 形态的展开结果只依赖用户主目录，与进程 CWD 无关，
        # 以展开后的绝对性判定可缓存性，缓存键仍为配置原始字符串。
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

    本地开发无 MCP Roots 时作为文件访问边界回退。优先读取活动配置，config 未就绪时
    回退环境变量。配置根的解析与缓存由 _resolve_configured_root 提供，无可用配置根
    回退进程 CWD 时记录告警，提示文件访问边界已放宽为整个工作目录。

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

    workspace_roots_scope 仅在客户端声明 roots capability 且 roots/list 成功时写入
    工作区根上下文变量；变量为 None 说明边界经 SEEDREAM_WORKSPACE_ROOT 或进程 CWD
    回退取得，属服务器环境而非客户端授权声明，其绝对路径不进入面向调用方的输出。

    Returns:
        边界来自会话 Roots 声明返回 True，经环境变量或进程 CWD 回退返回 False。
    """
    return _WORKSPACE_ROOTS_VAR.get() is not None


def resolve_workspace_roots(roots: Sequence[Path | str]) -> list[Path]:
    """将工作区根目录列表归一为 Path 列表，保持入参顺序。

    两类生产方在产出时已完成 resolve：会话 Roots 经 ``_file_uri_to_path`` 转换即
    resolve，环境变量回退经 ``resolve_env_workspace_root`` 返回已 resolve 路径。本
    函数不再重复 resolve，消除每请求对同一批根的重复文件系统调用；仅做 Path 归一，
    兼容字符串入参形态。

    Args:
        roots: 已 resolve 的工作区根目录列表，元素可为 Path 或路径字符串。

    Returns:
        归一后的根目录列表，保持入参顺序。
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


def _session_declares_roots_capability(session: Any) -> bool:
    """判断会话对端客户端是否在 initialize 阶段声明了 roots capability。

    未声明 roots 的客户端对 roots/list 请求必然返回方法不支持错误，先经会话内存中的
    capability 声明探测可跳过这次每请求一次的线上往返。check_client_capability
    不可达或探测异常时保守视为已声明，保持旧版 SDK 与测试替身下的原有行为。
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


@asynccontextmanager
async def workspace_roots_scope(ctx: Any) -> AsyncIterator[list[Path]]:
    """在当前请求作用域内绑定 MCP Roots，退出时自动恢复。

    将客户端 Roots 设置到上下文变量作为该请求的文件访问边界；客户端不支持
    Roots 时回退环境变量边界。客户端未声明 roots capability 时直接跳过
    roots/list 往返，同样回退环境变量边界。当前协议会话无服务端反向通道时
    roots/list 必抛 NoBackChannelError：未配置环境变量根则 fail-closed 抛出
    SeedreamMCPError，已配置则回退该显式边界。

    Args:
        ctx: MCP 请求上下文，经其 session 读取客户端 Roots。

    Yields:
        当前请求解析出的工作区根目录列表；客户端不支持 Roots 时为空列表。

    Raises:
        SeedreamMCPError: 协议会话无反向通道且无环境变量工作区根可用，进入
            作用域前抛出，使该请求的本地文件操作失败而非放宽边界。
    """
    token: Token[tuple[Path, ...] | None] | None = None
    resolved_roots: list[Path] = []

    session = getattr(ctx, "session", None)
    list_roots = getattr(session, "list_roots", None)
    roots_supported = session is not None and callable(list_roots)
    if roots_supported and not _session_declares_roots_capability(session):
        logger.debug("客户端未声明 roots capability，跳过 roots/list，回退环境变量边界")
        roots_supported = False

    if roots_supported:
        try:
            resolved_roots = await _resolve_workspace_roots_from_context(ctx)
        except NoBackChannelError as exc:
            # 当前协议会话不提供服务端发起请求的反向通道，roots/list 无法送达，
            # 属协议能力缺失而非瞬时失败，重试不会好转，提级为 error。回退在未配置
            # 环境变量根时会放宽文件访问边界到进程 CWD，此时 fail-closed；已配置
            # 环境变量根则回退该显式边界。
            configured_root = _resolve_configured_root()
            if configured_root is None:
                logger.error(
                    "协议会话无反向通道，无法读取 MCP Roots，且无环境变量工作区根可用: {}", exc
                )
                raise SeedreamMCPError(
                    "当前协议会话不支持读取 MCP Roots，且未配置 SEEDREAM_WORKSPACE_ROOT，"
                    "拒绝将文件访问边界放宽到进程工作目录"
                ) from exc
            logger.error(
                "协议会话无反向通道，无法读取 MCP Roots，回退环境变量边界 {}: {}",
                configured_root,
                exc,
            )
        except Exception as exc:
            # 回退会放宽文件访问边界到环境变量根（未配置时为进程 CWD），多租户
            # streamable-http 部署须感知该回退，提级为 warning 而非 debug
            logger.warning("读取 MCP Roots 失败，回退环境变量边界: {}", exc)
        else:
            token = _WORKSPACE_ROOTS_VAR.set(tuple(resolved_roots))
            if resolved_roots:
                logger.debug("已应用 MCP Roots 边界: {}", resolved_roots)
            else:
                logger.debug("MCP Roots 为空，当前请求按无本地目录权限处理")

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

    Args:
        path: 输入路径，可为相对或绝对。
        base_dir: 基础目录，用于解析相对路径。

    Returns:
        标准化的 Path 对象。

    Raises:
        ValueError: 路径为 UNC 形式、Windows 驱动器相对形式或路径无效时抛出。
    """
    try:
        path_obj = Path(path)

        # UNC 路径在 Windows 的 resolve 会触发 SMB 认证，须在 resolve 前拒绝。
        if is_unc_path(str(path_obj)):
            raise ValueError(f"拒绝 UNC 路径以避免触发 SMB 连接: {path}")

        # 驱动器相对路径有 drive 无 root，pathlib 的 / 拼接对该形态会丢弃 base_dir，
        # resolve 落到该盘的进程 CWD，静默绕开调用方指定的基础目录；与 UNC 同口径
        # 在 resolve 前拒绝。POSIX 路径无 drive，恒不触发本分支。
        if path_obj.drive and not path_obj.root:
            raise ValueError(f"拒绝驱动器相对路径以避免绕过基础目录解析: {path}")

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
        # 区分路径拼写问题与系统级长度限制，不归并为笼统的格式错误。
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
            # 已是绝对路径时直接返回字符串，不再重复 resolve；is_absolute 为纯词法
            # 判定，浏览链路传入的已 resolve 路径由此免除一次逐级 stat。
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

    安全前置条件：本函数自身不做工作区越界校验，调用方必须先完成工作区越界校验，
    确认 directory 位于允许的工作区根之内，再调用本函数。本函数经 utils/__init__
    重导出为公共工具，任何外部调用方均须遵守此前置条件。UNC 形式的 directory 入参
    与 normalize_path 同口径在 resolve 前拒绝，返回空列表并记录告警，避免越界校验
    尚未介入时 resolve 触发 SMB 认证。

    单个目录内的条目列表在排序阶段全量物化，limit 早停不缩减单目录内的物化与
    排序成本，仅使跨目录递归提前终止下降。重复扫描同一目录的成本由 io_scan 的
    mtime 加 TTL 目录扫描缓存缓解。

    Args:
        directory: 搜索目录。
        recursive: 是否递归搜索。
        max_depth: 最大搜索深度。
        extensions: 指定的文件扩展名列表。
        limit: 返回数量上限，<=0 时返回空列表；扫描按 normcase 稳定顺序，凑够即提前停止。
        unreadable_dirs: 可选收集列表，扫描中因权限或系统错误无法读取的目录会追加至此，
            供调用方区分「目录不可读」与「目录内无图片」；未提供时不可读目录仅记日志跳过。

    Returns:
        找到的图片文件路径列表。目录不存在或预检失败时返回空列表。

    Raises:
        OSError: 扫描中途的文件系统错误（如条目 stat 失败）向上传播，由调用方区分
            「扫完」与「中途出错」；单个目录不可读属预期信号，经 unreadable_dirs
            收集后跳过该目录，不视为扫描失败。
    """
    images: list[Path] = []

    if limit is not None and limit <= 0:
        return images

    if is_unc_path(directory):
        # UNC 路径的 resolve 在 Windows 会触发 SMB 认证，与 normalize_path 等 resolve
        # 站点同口径在 resolve 前拦截。浏览扫描对拒绝对象返回空列表，告警不回显原始
        # 路径全文，与项目的脱敏口径一致。
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
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda entry: os.path.normcase(entry.path))
        except OSError as e:
            logger.warning("无法访问目录 {}: {}", path, e)
            if unreadable_dirs is not None:
                unreadable_dirs.append(path)
            return False

        for entry in entries:
            entry_path = Path(entry.path)
            # follow_symlinks=False：不跟随符号链接，避免符号链接环与经由符号链接越界遍历。
            if (
                entry.is_file(follow_symlinks=False)
                and entry_path.suffix.lower() in target_extensions
            ):
                # OneDrive 占位文件等 reparse 文件不是 symlink，is_file 不拒绝；与目录
                # 分支同规则剔除，避免列为参考图后读取跟随 reparse 目标。后缀命中后才
                # 判定，非图片条目不付 lstat 开销。
                if _is_reparse_point(entry_path):
                    logger.warning("跳过 reparse point 文件: {}", entry_path)
                    continue
                images.append(entry_path)
                if target_count >= 0 and len(images) >= target_count:
                    return True
            elif entry.is_dir(follow_symlinks=False) and recursive and current_depth < max_depth:
                if _is_reparse_point(entry_path):
                    logger.warning("跳过 reparse point 目录: {}", entry_path)
                    continue
                if scan_directory(entry_path, current_depth + 1):
                    return True
        return False

    scan_directory(dir_path)

    return images


def suggest_similar_paths(target_path: str, search_dirs: list[str] | None = None) -> list[str]:
    """在搜索目录下建议与目标路径拼写相近的图片路径。

    用于路径校验失败时给出纠错建议，缓解用户手误导致的路径错误。

    Args:
        target_path: 目标路径。
        search_dirs: 搜索目录列表；未提供时不扫描任何目录并返回空列表，强制调用方
            显式指定边界。不默认扫描进程 CWD，避免本函数经公开导出被直接调用时以
            CWD 为界泄露本地图片文件名。

    Returns:
        相似路径建议列表，最多 5 条。目标以 ``/``、``.`` 等结尾使文件名归一为空串时
        不产生建议，避免空串子串匹配把任意前几张图片误当相近项。
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
        # URLError（含 POSIX 上的 //server/share 形态），语义同为拒绝，归一为 None。
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
