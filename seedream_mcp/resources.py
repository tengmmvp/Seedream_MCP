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
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT,
    LIFESPAN_KEY_CLIENT,
    LIFESPAN_KEY_CONFIG,
    LIFESPAN_KEY_DOWNLOAD_MANAGER,
    SeedreamConfig,
    get_active_config,
    set_active_config,
)
from .utils.core.logs import get_logger
from .version import __version__

if TYPE_CHECKING:
    from .client import SeedreamClient
    from .utils.io.io_download import DownloadManager

# ==================== 服务器元数据常量 ====================

SERVER_NAME = "seedream_mcp"

SERVER_VERSION = __version__

SERVER_INSTRUCTIONS = "Seedream 图像生成工具，支持文生图、图文生图、多图融合、组图输出与图片浏览。"

# ==================== MCP 服务器实例与共享资源状态 ====================

logger = get_logger(__name__)


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


_active_resource: _SharedResource | None = None
# 被重建取代但仍有在途引用的资源，引用归零时由 _release_resource 关闭并移出。
_retired_resources: list[_SharedResource] = []
# asyncio.Lock 首次 acquire 后绑定当时的事件循环：同进程二次进入不同循环（重复
# asyncio.run、先程序化使用再起 server）会 RuntimeError，生产单循环路径安全；
# 跨循环复用由 _reset_lifespan_state 重建规避。
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
        _apply_download_concurrency_limit(new_download_manager, config)
    except Exception:
        await _safe_close(new_client)
        await _safe_close(new_download_manager)
        raise
    return _SharedResource(new_client, new_download_manager, config)


def _apply_download_concurrency_limit(
    download_manager: "DownloadManager", config: SeedreamConfig
) -> None:
    """为共享下载管理器的会话连接器施加进程级下载并发上限。

    共享 DownloadManager 跨请求复用同一 aiohttp 会话，AutoSaveManager 每次批量保存
    局部构造的信号量只约束单次调用，多个并发生成请求的下载并发会叠加超出配置上限。
    在会话连接器上统一施加 config.auto_save_max_concurrent，使全进程同时建立的下载
    连接数受同一上限约束。aiohttp 连接器的 limit 为只读属性且 DownloadManager 无
    连接器注入入参，经底层 _limit 槽位赋值实现；在会话首次建立连接前施加，效果与
    构造期传入 limit 一致。
    """
    session = download_manager._session
    connector = session.connector if session is not None else None
    if connector is not None:
        connector._limit = config.auto_save_max_concurrent


async def _close_resource(resource: _SharedResource) -> None:
    """关闭资源持有的 client 与 download_manager。"""
    await _safe_close(resource.client)
    await _safe_close(resource.download_manager)


async def _retire_resource(resource: _SharedResource | None) -> None:
    """退役被取代的共享资源。仍有在途引用时纳入退役追踪，否则立即关闭。"""
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
    管理 FastMCP 生命周期，注入共享配置、SeedreamClient 与 DownloadManager。

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
        # 锁内二次判定，避免并发进入 lifespan 时重复构造共享资源。
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
        # 清理须在 finally 内执行：asynccontextmanager 的 yield 体抛异常时，异常经
        # athrow 注入并在 finally 后继续向外传播，写在 finally 之后的语句会被跳过，
        # 导致异常 teardown 下共享资源不被同循环清理。
        if not getattr(server.settings, "stateless_http", False):
            await _cleanup_shared_resources()


async def _cleanup_shared_resources() -> None:
    """关闭并清空活动与退役资源持有的 HTTP 连接池，并等待后台清理任务完成。

    先经 drain_background_cleanup_tasks 等待自动保存的节流清理任务收尾，使退出时
    节流状态定局；随后关闭资源。streamable-http 的
    退出路径另经 _drain_pending_tasks 取消回收残余任务兜底。
    """
    global _active_resource
    from .utils.io.io_save import drain_background_cleanup_tasks

    await drain_background_cleanup_tasks()
    retired = list(_retired_resources)
    _retired_resources.clear()
    active = _active_resource
    _active_resource = None
    for resource in retired:
        await _close_resource(resource)
    if active is not None:
        await _close_resource(active)


def _sync_cleanup() -> None:
    """执行同步入口的进程级兜底清理。

    streamable-http 的优雅关闭在 _run_streamable_http 内于服务事件循环上执行，stdio 在
    lifespan teardown 同循环清理；两者完成后活动资源已为 None，本函数为防御性兜底，覆盖
    异常退出未触发上述清理的情形。先提取并清空全局引用，确保引用不因后续清理抛错而滞留。
    关闭在 asyncio.run 的新事件循环上执行：httpx/aiohttp 传输绑定于原循环，跨循环 aclose
    对底层 socket 常无效，此处为尽力而为的显式关闭，残余连接交由进程退出回收，"不泄漏"
    的强保证仅在同循环清理路径（app_lifespan teardown 与 _run_streamable_http finally）成立。
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
        # 无事件循环或已有循环运行，无法安全 asyncio.run；引用已清空，余量交 GC/OS。
        pass
    except Exception as exc:
        logger.warning("同步清理共享资源失败: {}", exc)


def _reset_lifespan_state() -> None:
    """重置 lifespan 单例、活动配置与初始化锁，仅供测试隔离调用。

    重建 _shared_init_lock 避免跨事件循环复用绑定了旧循环的 asyncio.Lock，复位
    mcp.settings 的 streamable-http 配置避免上一个用例的 host/port/stateless 泄漏。
    模块级可变状态的复位清单集中在本函数，新增状态须登记于此：自动保存清理节流状态、
    目录扫描缓存、参考图 roots 解析缓存分别经对应模块的复位函数清除。
    """
    global _shared_init_lock, _active_resource
    _active_resource = None
    _retired_resources.clear()
    set_active_config(None)
    _shared_init_lock = asyncio.Lock()
    mcp.settings.stateless_http = False
    mcp.settings.host = DEFAULT_HTTP_HOST
    mcp.settings.port = DEFAULT_HTTP_PORT
    # 复位清单：io_save 的清理节流锁与任务集合绑定事件循环，io_scan 的目录扫描缓存与
    # image_prepare 的 roots 解析缓存跨用例残留目录解析结果；三者与 lifespan 单例同步
    # 复位。延迟导入遵循 utils 子模块不在顶层 import 顶层模块的项目约定。
    from .utils.images.image_prepare import reset_resolved_bases_cache
    from .utils.io.io_save import reset_cleanup_state
    from .utils.io.io_scan import reset_directory_scan_cache

    reset_cleanup_state()
    reset_directory_scan_cache()
    reset_resolved_bases_cache()


# 模块级单例：server 经此注册工具/prompt/resource，transport 与 lifespan 亦复用同一实例。
mcp = FastMCP(
    SERVER_NAME,
    instructions=SERVER_INSTRUCTIONS,
    lifespan=app_lifespan,
)
