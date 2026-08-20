"""Seedream MCP 共享资源模块。

持有 MCPServer 实例 mcp 与其生命周期所需的共享资源管理：服务器元数据常量、
app_lifespan 引用计数单例、活动与退役资源状态、同步与异步清理入口。server 模块
导入 mcp 完成工具、资源与 prompt 注册并重导出本模块符号；transport 模块直接从
本模块导入 mcp 与清理函数，避免经 server 形成反向依赖。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from mcp.server.caching import CacheHint, CacheableMethod
from mcp.server.mcpserver import MCPServer, RequestStateSecurity
from mcp.server.request_state import RequestStateBoundary

from .config import (
    LIFESPAN_KEY_CLIENT,
    LIFESPAN_KEY_CONFIG,
    LIFESPAN_KEY_DOWNLOAD_MANAGER,
    SeedreamConfig,
    active_request_state_keys,
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

logger = get_logger()


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
# asyncio.Lock 绑定首次 acquire 的事件循环，跨循环复用会 RuntimeError；测试经
# _reset_lifespan_state 重建规避。
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
    # 下载并发上限经构造参数下沉，连接器重建的分支自动保持上限。
    new_download_manager = DownloadManager(
        timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
        connection_limit=config.auto_save_max_concurrent,
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


def _has_inflight_references() -> bool:
    """判定共享资源是否仍有在途 lifespan 引用。"""
    active = _active_resource
    return (active is not None and active.refcount > 0) or bool(_retired_resources)


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[dict[str, Any]]:
    """管理 MCPServer 生命周期，向 lifespan_context 注入共享配置、SeedreamClient
    与 DownloadManager，键名见 config 模块 LIFESPAN_KEY_* 常量。

    资源以引用计数的模块级单例持有，跨 lifespan 重入复用；teardown 仅递减在途引用，
    归零前不清理。config 身份变化触发重建：新资源立即取代活动槽位，旧资源待最后一个
    在途请求释放后再关闭，运行期 set_config/reload_config 不影响在途请求已持有的
    HTTP 连接池。
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
        # 清理须在 finally 内执行且以引用计数门控：并发在途引用存在时不得关闭其余
        # 请求仍在使用的资源；drain 的让出点期间可能出现新引用，idle_only 使清理
        # 在等待后复检引用。
        if not _has_inflight_references():
            await _cleanup_shared_resources(idle_only=True)


async def _cleanup_shared_resources(*, idle_only: bool = False) -> None:
    """关闭并清空活动与退役资源，并等待后台清理任务完成。

    先经 drain_background_cleanup_tasks 等待自动保存的节流清理任务收尾再关闭资源。
    idle_only 为 True 时以在途引用门控：drain 等待期间出现新引用即放弃本次清理，
    交由最后一个在途引用的 teardown 重新触发；为 False 时无条件关闭，供进程退出
    兜底。
    """
    global _active_resource
    from .utils.io.io_save import drain_background_cleanup_tasks

    await drain_background_cleanup_tasks()
    if idle_only and _has_inflight_references():
        logger.debug("清理等待期间出现新的在途 lifespan 引用，放弃本次共享资源清理")
        return
    retired = list(_retired_resources)
    _retired_resources.clear()
    active = _active_resource
    _active_resource = None
    for resource in retired:
        await _close_resource(resource)
    if active is not None:
        await _close_resource(active)


def _sync_cleanup() -> None:
    """同步入口的进程级兜底清理，覆盖异常退出未触发常规清理的情形。

    先提取并清空全局引用，避免后续清理抛错使引用滞留。关闭在新事件循环上尽力而为：
    httpx/aiohttp 传输绑定原循环，跨循环 aclose 常无效，残余连接交由进程退出回收。
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
    """重置 lifespan 单例、全局配置、初始化锁与关联模块状态，仅供测试隔离调用。

    重建 _shared_init_lock 避免跨事件循环复用旧锁；_global_config 一并复位避免
    跨用例残留，赋值经函数内延迟 import 取当前 config 模块对象，测试重载模块后
    不会清错目标。
    """
    global _shared_init_lock, _active_resource
    _active_resource = None
    _retired_resources.clear()
    set_active_config(None)
    from . import config as config_module

    config_module._global_config = None
    _shared_init_lock = asyncio.Lock()
    # io_save 的清理节流状态与 io_scan 的目录扫描缓存同步复位。
    from .utils.io.io_save import reset_cleanup_state
    from .utils.io.io_scan import reset_directory_scan_cache

    reset_cleanup_state()
    reset_directory_scan_cache()


# ==================== 服务器构造 ====================

# 静态列表面的客户端缓存提示时长。纳入的键均为 import 期固定的静态声明，可安全
# 声明新鲜度；resources/read、server/info、workspace/roots 随会话与活动配置变化，
# 不纳入。
_STATIC_LIST_CACHE_TTL_MS = 60_000

_STATIC_LIST_CACHE_HINTS: dict[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=_STATIC_LIST_CACHE_TTL_MS),
    "prompts/list": CacheHint(ttl_ms=_STATIC_LIST_CACHE_TTL_MS),
    "resources/list": CacheHint(ttl_ms=_STATIC_LIST_CACHE_TTL_MS),
    "resources/templates/list": CacheHint(ttl_ms=_STATIC_LIST_CACHE_TTL_MS),
    "server/discover": CacheHint(ttl_ms=_STATIC_LIST_CACHE_TTL_MS),
}


def _build_request_state_security() -> RequestStateSecurity | None:
    """按活动配置构造 requestState 密钥环策略，未配置时返回 None 保持 SDK 默认。"""
    keys = active_request_state_keys()
    if not keys:
        return None
    return RequestStateSecurity(keys=keys)


def _create_mcp_server() -> MCPServer:
    """构造进程级 MCPServer 实例，静态列表面附缓存提示并按配置启用密钥环。"""
    return MCPServer(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        version=SERVER_VERSION,
        lifespan=app_lifespan,
        cache_hints=_STATIC_LIST_CACHE_HINTS,
        request_state_security=_build_request_state_security(),
    )


# 模块级单例：server 经此注册工具/prompt/resource，transport 与 lifespan 复用同一实例。
# version 必须显式传入：SDK 2.0 起未传 version 的服务器在 initialize 结果的 serverInfo
# 中报告空串而非 SDK 包版本。request_state_security 为 None 时 SDK 回退进程临时密钥；
# 多副本 HTTP 部署经 SEEDREAM_REQUEST_STATE_KEYS 共享密钥环，重试落到其他实例仍可
# 解封 requestState。密钥环经默认环境源在导入期固化，--config-file 场景由启动路径的
# rebind_request_state_security 以最终活动配置重绑。
mcp = _create_mcp_server()


def rebind_request_state_security(keys: tuple[bytes, ...] | None) -> bool:
    """以最终活动配置重绑单例的 requestState 密钥环，返回是否重绑成功。

    单例密钥环在模块导入期经默认环境源构造，``--config-file`` 加载的密钥不会
    到达它，故由启动路径在活动配置就绪后调用本函数；keys 为 None 时重绑回 SDK
    进程临时密钥。经 SDK 公开属性 mcp.middleware 定位 boundary，探测失败时告警
    并返回 False，不阻断启动。

    Args:
        keys: 最终活动配置解析出的密钥环字节，None 表示未配置。
    """
    try:
        middleware_chain = mcp.middleware
    except AttributeError:
        logger.error(
            "SDK 公开属性 mcp.middleware 不可用，requestState 密钥环重绑被跳过，"
            "单例保持导入期形态；多副本部署的密钥共享可能失效"
        )
        return False
    boundary = None
    for middleware in middleware_chain:
        if isinstance(middleware, RequestStateBoundary):
            boundary = middleware
            break
    if boundary is None or not hasattr(boundary, "_security"):
        logger.error(
            "SDK 私有路径中未找到 RequestStateBoundary，requestState 密钥环"
            "重绑被跳过，单例保持导入期形态；多副本部署的密钥共享可能失效"
        )
        return False
    boundary._security = (
        RequestStateSecurity(keys=keys) if keys else RequestStateSecurity.ephemeral()
    )
    return True
