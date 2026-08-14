"""
Seedream MCP 共享资源模块。

持有 FastMCP 实例 mcp 与其生命周期所需的共享资源管理：服务器元数据常量、
app_lifespan 引用计数单例、活动与退役资源状态、同步与异步清理入口。server 模块
导入 mcp 完成工具、资源与 prompt 注册并重导出本模块符号；transport 模块直接从
本模块导入 mcp 与清理函数，避免经 server 形成反向依赖。
"""

from __future__ import annotations

# 标准库导入
import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from mcp.server.fastmcp import FastMCP

from .config import (
    LIFESPAN_KEY_CLIENT,
    LIFESPAN_KEY_CONFIG,
    LIFESPAN_KEY_DOWNLOAD_MANAGER,
    SeedreamConfig,
    get_active_config,
    set_active_config,
)
from .utils.core.logs import get_logger
from .cli import _DEFAULT_HTTP_HOST, _DEFAULT_HTTP_PORT
from .version import __version__

if TYPE_CHECKING:
    from .client import SeedreamClient
    from .utils.io.io_download import DownloadManager

# ==================== 服务器元数据常量 ====================

# 服务器标识名称
SERVER_NAME = "seedream_mcp"

# 服务器版本号
SERVER_VERSION = __version__

# 服务器功能说明
SERVER_INSTRUCTIONS = "Seedream 图像生成工具，支持文生图、图文生图、多图融合、组图输出与图片浏览。"

# ==================== MCP 服务器实例与共享资源状态 ====================

# 模块日志记录器。须先于引用它的 lifespan/cleanup 定义，否则函数体内无法解析该名
logger = get_logger(__name__)


# 共享 client/download_manager 对及在途引用计数，跨 lifespan 重入复用。
# stateless_http 模式下 FastMCP 每请求重入 lifespan，模块级单例确保连接池跨请求复用。
# 创建受 _shared_init_lock 保护避免并发重复构造；config 身份变化触发重建时，旧资源可能
# 仍被在途请求持有，引用计数确保仅当所有在途请求释放后才关闭旧实例，避免断开在途请求
# 已持有的 HTTP 连接池。
class _SharedResource:
    """共享 client/download_manager 对及在途引用计数。"""

    def __init__(
        self,
        client: "SeedreamClient",
        download_manager: "DownloadManager",
        config: SeedreamConfig,
    ) -> None:
        self.client = client
        self.download_manager = download_manager
        self.config = config
        self.refcount = 0


# 当前活动资源句柄
_active_resource: _SharedResource | None = None
# 被重建取代但仍有在途引用的资源，引用归零时由 _release_resource 关闭并移出
_retired_resources: list[_SharedResource] = []
_shared_init_lock = asyncio.Lock()


async def _safe_close(obj: Any) -> None:
    """关闭共享资源，吞掉异常避免清理路径中断。"""
    if obj is None:
        return
    try:
        await obj.close()
    except Exception as exc:
        logger.warning("关闭共享资源失败: {}", exc)


async def _build_active_resource(config: SeedreamConfig) -> _SharedResource:
    """创建并初始化新的共享资源句柄，部分初始化失败时关闭已创建部分避免泄漏。"""
    from .client import SeedreamClient
    from .utils.io.io_download import DownloadManager

    new_client = SeedreamClient(config)
    new_download_manager = DownloadManager(
        timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
    )
    try:
        await new_client.__aenter__()
        await new_download_manager.__aenter__()
    except Exception:
        await _safe_close(new_client)
        await _safe_close(new_download_manager)
        raise
    return _SharedResource(new_client, new_download_manager, config)


async def _close_resource(resource: _SharedResource) -> None:
    """关闭资源持有的 client 与 download_manager。"""
    await _safe_close(resource.client)
    await _safe_close(resource.download_manager)


async def _retire_resource(resource: _SharedResource | None) -> None:
    """资源被新资源取代。仍有在途引用时纳入退役追踪，否则立即关闭。"""
    if resource is None:
        return
    if resource.refcount > 0:
        _retired_resources.append(resource)
    else:
        await _close_resource(resource)


async def _release_resource(resource: _SharedResource) -> None:
    """递减在途引用；退役资源引用归零时关闭并移出追踪列表。"""
    resource.refcount -= 1
    if resource.refcount > 0 or resource is _active_resource:
        return
    if resource in _retired_resources:
        _retired_resources.remove(resource)
    await _close_resource(resource)


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """
    FastMCP 生命周期管理：注入共享配置、SeedreamClient 与 DownloadManager。

    资源以引用计数的模块级单例持有，跨 lifespan 重入复用。stateless_http 模式下 FastMCP
    每请求重入 lifespan，活动资源仅登记在途引用而不关闭，使连接池跨请求复用。config
    身份变化触发重建：新资源立即取代活动槽位，旧资源若仍有在途引用则纳入退役追踪，待
    最后一个在途请求释放后再关闭，避免运行期 set_config/reload_config 断开在途请求已
    持有的 HTTP 连接池。stdio 与普通 streamable-http 仅单次进入 lifespan，teardown 时
    在同事件循环清理。工具经 ctx.request_context.lifespan_context 取
    ["config"]/["client"]/["download_manager"]。
    """
    global _active_resource
    config = get_active_config()
    if _active_resource is None or _active_resource.config is not config:
        async with _shared_init_lock:
            if _active_resource is None or _active_resource.config is not config:
                old = _active_resource
                _active_resource = await _build_active_resource(config)
                await _retire_resource(old)
            resource = _active_resource
            resource.refcount += 1
    else:
        resource = _active_resource
        resource.refcount += 1
    try:
        yield {
            LIFESPAN_KEY_CONFIG: resource.config,
            LIFESPAN_KEY_CLIENT: resource.client,
            LIFESPAN_KEY_DOWNLOAD_MANAGER: resource.download_manager,
        }
    finally:
        await _release_resource(resource)
    if not getattr(server.settings, "stateless_http", False):
        await _cleanup_shared_resources()


async def _cleanup_shared_resources() -> None:
    """关闭并清空活动与退役资源持有的 HTTP 连接池。"""
    global _active_resource
    retired = list(_retired_resources)
    _retired_resources.clear()
    active = _active_resource
    _active_resource = None
    for resource in retired:
        await _close_resource(resource)
    if active is not None:
        await _close_resource(active)


def _sync_cleanup() -> None:
    """同步入口的进程级兜底清理。

    streamable-http 的优雅关闭在 _run_streamable_http 内于服务事件循环上执行，stdio 在
    lifespan teardown 同循环清理；两者完成后活动资源已为 None，本函数为防御性兜底，覆盖
    异常退出未触发上述清理的情形。先提取并清空全局引用，确保即便后续清理协程抛错也不泄漏。
    """
    global _active_resource
    retired = list(_retired_resources)
    _retired_resources.clear()
    active = _active_resource
    _active_resource = None

    async def _close_held() -> None:
        if active is not None:
            await _safe_close(active.client)
            await _safe_close(active.download_manager)
        for resource in retired:
            await _close_resource(resource)

    try:
        asyncio.run(_close_held())
    except RuntimeError:
        # 无事件循环或已有循环运行，无法安全 asyncio.run；引用已清空，余量交 GC/OS
        pass
    except Exception as exc:
        logger.warning("同步清理共享资源失败: {}", exc)


def _reset_lifespan_state() -> None:
    """重置 lifespan 单例、活动配置与初始化锁，仅供测试隔离调用。

    一并重建 _shared_init_lock 与自动保存清理锁，避免跨事件循环复用绑定了旧循环的 asyncio.Lock。
    复位 mcp.settings 的 streamable-http 配置，避免上一个用例的 host/port/stateless 泄漏。
    """
    global _shared_init_lock, _active_resource
    _active_resource = None
    _retired_resources.clear()
    set_active_config(None)
    _shared_init_lock = asyncio.Lock()
    mcp.settings.stateless_http = False
    mcp.settings.host = _DEFAULT_HTTP_HOST
    mcp.settings.port = _DEFAULT_HTTP_PORT
    from .utils.io.io_save import _reset_cleanup_state

    _reset_cleanup_state()


# 初始化 FastMCP 服务器实例
mcp = FastMCP(
    SERVER_NAME,
    instructions=SERVER_INSTRUCTIONS,
    lifespan=app_lifespan,
)
