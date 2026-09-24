"""streamable-http 传输栈测试共享辅助：ASGI lifespan 驱动器、传输栈构建与协议信封。

httpx.ASGITransport 不驱动 ASGI lifespan，触达 MCP 应用的用例以 _LifespanManager
显式运行 session_manager 生命周期；_assemble_streamable_http_app 是两套传输 fixture
的共享装配核心，build_transport_app 与 _web_fixtures.build_web_app 经差异参数在其上
构建传输栈，modern_meta_envelope 构造现代纪元请求体的 _meta 协议信封，e2e 与 SDK
行为特征化用例共享同一脚手架。
"""

from __future__ import annotations

import asyncio
from typing import Any, MutableMapping

import seedream_mcp.server as server
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.transport import _attach_streamable_http_middleware, _transport_security_for_host

# 生产请求体上限默认值，与 SeedreamConfig.http_max_body_size 默认一致。
_MAX_BODY = 64 * 1024 * 1024


def modern_meta_envelope(protocol_version: str) -> dict[str, Any]:
    """构造现代纪元请求 params._meta 的协议信封，供原始 HTTP 直发用例复用。"""
    return {
        PROTOCOL_VERSION_META_KEY: protocol_version,
        CLIENT_CAPABILITIES_META_KEY: {},
    }


class _LifespanManager:
    """最小 ASGI lifespan 驱动器，等价替代未引入的 asgi_lifespan。

    发送 lifespan.startup/shutdown 并等待 complete，使 Starlette 运行
    session_manager 建立请求处理任务组；failed 消息转译为 RuntimeError，用例立即
    失败而非在等待 complete 事件上无限挂起。
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._error: BaseException | None = None

    def _fail(self, message: MutableMapping[str, Any]) -> None:
        detail = message.get("message", "")
        self._error = RuntimeError(f"ASGI lifespan 失败: {detail}")
        self._startup_complete.set()
        self._shutdown_complete.set()

    async def __aenter__(self) -> "_LifespanManager":
        self._startup_complete = asyncio.Event()
        self._shutdown_complete = asyncio.Event()
        self._queue: "asyncio.Queue[MutableMapping[str, Any]]" = asyncio.Queue()

        async def receive() -> MutableMapping[str, Any]:
            return await self._queue.get()

        async def send(message: MutableMapping[str, Any]) -> None:
            msg_type = message["type"]
            if msg_type == "lifespan.startup.complete":
                self._startup_complete.set()
            elif msg_type == "lifespan.startup.failed":
                self._fail(message)
            elif msg_type == "lifespan.shutdown.complete":
                self._shutdown_complete.set()
            elif msg_type == "lifespan.shutdown.failed":
                self._fail(message)

        self._task = asyncio.ensure_future(self._app({"type": "lifespan"}, receive, send))
        await self._queue.put({"type": "lifespan.startup"})
        await self._startup_complete.wait()
        if self._error is not None:
            await self._task
            raise self._error
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._queue.put({"type": "lifespan.shutdown"})
        await self._shutdown_complete.wait()
        await self._task
        if self._error is not None:
            raise self._error


def _assemble_streamable_http_app(
    auth_token: str,
    *,
    host: str = "127.0.0.1",
    stateless: bool = False,
    json_response: bool = False,
    body_limit: int = _MAX_BODY,
    config: SeedreamConfig | None = None,
    attach_max_body_size: int | None = None,
    web_enabled: bool = False,
) -> Any:
    """按生产装配序构建 streamable-http 栈，供 build_transport_app 与 build_web_app 共享。

    中间件经 transport._attach_streamable_http_middleware 复用生产装配且顺序同源，
    transport_security 按绑定地址派生后与其余传输参数直传 streamable_http_app。
    差异参数默认值归属：host、stateless、json_response、body_limit 与
    attach_max_body_size=None 由本核心持有；build_transport_app 额外安装 config 并
    转发 host、stateless、json_response 与 body_limit；build_web_app 显式传
    attach_max_body_size 并转发 host 与 web_enabled，config 与其余参数沿用核心默认。
    config 非 None 时先安装为活动配置再派生 transport_security；attach_max_body_size
    为 None 时中间件回退读取活动配置；web_enabled 驱动 Web 路由注册、静态挂载与
    中间件参数，webapp 模块保持按需导入。
    """
    if config is not None:
        set_active_config(config)
    if web_enabled:
        from seedream_mcp.webapp import register_web_routes

        register_web_routes()
    app = server.mcp.streamable_http_app(
        host=host,
        stateless_http=stateless,
        json_response=json_response,
        transport_security=_transport_security_for_host(host),
        max_request_body_size=body_limit,
    )
    if web_enabled:
        from seedream_mcp.webapp import mount_web_static

        mount_web_static(app)
    _attach_streamable_http_middleware(
        app, host, auth_token, max_body_size=attach_max_body_size, web_enabled=web_enabled
    )
    return app


def build_transport_app(
    auth_token: str,
    *,
    body_limit: int = _MAX_BODY,
    stateless: bool = False,
    json_response: bool = False,
    host: str = "127.0.0.1",
) -> Any:
    """按生产 run_streamable_http 的装配路径构建传输栈。

    请求体上限经活动配置注入，配置合法下限 1MB，超限用例以 1MB 配 1MB+1 触发；
    装配与中间件挂载经 _assemble_streamable_http_app 单点完成。
    """
    return _assemble_streamable_http_app(
        auth_token,
        host=host,
        stateless=stateless,
        json_response=json_response,
        body_limit=body_limit,
        config=SeedreamConfig(api_key="test_key", http_max_body_size=body_limit),
    )
