"""streamable-http 传输层：ASGI 中间件与传输配置。

包含请求体大小限制、Bearer 鉴权、健康检查三个 ASGI 中间件，以及 streamable-http
监听与 TLS 配置。中间件经 Starlette add_middleware 装配到 FastMCP 的 streamable_http_app
外层，按装配逆序执行。FastMCP 实例 mcp 在调用时延迟导入，避免与 server 模块形成顶层
循环导入。
"""

from __future__ import annotations

# 标准库导入
import argparse
import asyncio
import hmac
import json
from typing import Any

from .config import get_active_config
from .utils.logging import get_logger

# 模块日志记录器
logger = get_logger(__name__)

# ==================== 传输层常量 ====================

# streamable-http 可信回环地址集合：仅字面量回环地址免鉴权。
# 不含 "localhost"，其解析依赖 hosts/DNS，污染时可指向非回环地址，
# 仅凭字符串判定会使公网暴露仍免鉴权。
_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


# ==================== ASGI 响应辅助 ====================


async def _send_asgi_json(
    send: Any,
    status: int,
    body: bytes,
    extra_headers: tuple[tuple[bytes, bytes], ...] = (),
) -> None:
    """
    发送统一格式的 JSON ASGI 响应。

    构造 http.response.start 与 http.response.body 两条消息，content-type 固定为
    application/json，content-length 按实得 body 字节计算；extra_headers 附加在标准头
    之后，供 www-authenticate 等响应头复用，消除三处中间件的手写重复。
    """
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        # 鉴权失败、请求超限与健康探针响应禁止缓存，避免中间代理或浏览器缓存
        # 敏感状态码响应后误导后续请求。
        (b"cache-control", b"no-store"),
    ]
    headers.extend(extra_headers)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


# ==================== ASGI 中间件 ====================


class _BearerTokenAuthMiddleware:
    """
    streamable-http Bearer 令牌鉴权 ASGI 中间件。

    校验请求 Authorization 头中的 Bearer 令牌，匹配则放行，否则 HTTP 流量返回 401。
    启用鉴权时拒绝 websocket 等非 HTTP 流量并以 code 1008 关闭，避免绕过 Bearer 校验。
    使用 hmac.compare_digest 做常数时间比较，避免时序侧信道泄露令牌。
    """

    def __init__(self, app: Any, expected_token: str) -> None:
        self.app = app
        self._expected = expected_token.encode("utf-8")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type != "http":
            # 启用鉴权时拒绝 websocket 等非 http 流量，避免绕过 Bearer 校验
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        if self._request_authorized(scope):
            await self.app(scope, receive, send)
            return

        await self._send_unauthorized(send)

    def _request_authorized(self, scope: Any) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                if value[:7].lower() == b"bearer ":
                    return hmac.compare_digest(value[7:].strip(), self._expected)
                return False
        return False

    async def _send_unauthorized(self, send: Any) -> None:
        body = json.dumps(
            {"error": "invalid_token", "error_description": "Authentication required"}
        ).encode("utf-8")
        await _send_asgi_json(
            send,
            401,
            body,
            extra_headers=(
                (b"www-authenticate", b'Bearer error="invalid_token"'),
                (b"connection", b"close"),
            ),
        )


class _LimitRequestBodyMiddleware:
    """streamable-http 请求体大小限制 ASGI 中间件。

    先按 Content-Length 头早拒超过上限的请求作为快速路径；再包装 receive 累计 chunked 实际
    接收字节数，超限即短路返回 413，防止缺失或谎报 Content-Length 的超大请求体在 pydantic
    物料化前撑爆内存。仅作用于 http 请求，websocket 等非 http 流量原样透传。
    """

    def __init__(self, app: Any, max_body_size: int) -> None:
        self.app = app
        self._max_body_size = max_body_size

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_length = 0
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    pass
                break

        # 快速路径：声明长度超限直接 413，避免进入应用读取阶段
        if content_length > self._max_body_size:
            await self._send_too_large(send)
            return

        # chunked 防御：累计实际接收字节，缺失或谎报 Content-Length 时超限短路 413
        total_received = 0
        too_large = False

        async def receive_wrapper() -> Any:
            nonlocal total_received, too_large
            # 一旦已判定超限，后续调用直接返回空终帧，不再向底层 receive 读取真实消息，
            # 彻底切断超大 body 的剩余投递。
            if too_large:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message.get("type") == "http.request":
                total_received += len(message.get("body", b""))
                if total_received > self._max_body_size:
                    too_large = True
                    # 返回空终帧，停止向下游投递剩余超大请求体
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def send_wrapper(message: Any) -> None:
            # 一旦判定超限，吞掉下游响应，由本中间件统一回 413 避免双响应
            if too_large:
                return
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            # 下游读到被截断的空终帧后可能抛异常；too_large 时吞掉并统一回 413，避免冒泡为 500
            if too_large:
                await self._send_too_large(send)
                return
            raise

        if too_large:
            await self._send_too_large(send)

    async def _send_too_large(self, send: Any) -> None:
        body = json.dumps(
            {"error": "request_too_large", "error_description": "Request body exceeds limit"}
        ).encode("utf-8")
        await _send_asgi_json(send, 413, body, extra_headers=((b"connection", b"close"),))


class _HealthCheckMiddleware:
    """streamable-http 健康检查中间件，短路 GET /health 返回进程存活状态。

    最后装配使其成为最外层，先于请求体限制与 Bearer 鉴权执行，负载均衡与健康探针
    无需令牌即可探活。仅做 liveness 判定，不探测上游 API，避免拖慢探针。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == "/health"
        ):
            await _send_asgi_json(send, 200, b'{"status":"ok"}')
            return
        await self.app(scope, receive, send)


# ==================== streamable-http 传输配置 ====================


def _resolve_http_auth_token(args: argparse.Namespace) -> str:
    """解析 streamable-http 鉴权令牌：CLI 参数优先，其次活动配置。"""
    token = args.auth_token or get_active_config().http_auth_token
    return (token or "").strip()


def _apply_http_bind_settings(host: str, port: int, stateless: bool, auth_enabled: bool) -> None:
    """
    将 streamable-http 监听配置写入 FastMCP settings，并就暴露风险与鉴权状态告警。

    非回环绑定时，调用方需已在 cli_main 中完成 fail-closed 校验，即必须配置鉴权令牌，
    因此此处非回环分支仅用于确认已启用鉴权。stateless 启用无状态模式，更适合远程
    多客户端与负载均衡场景。
    """
    from .server import mcp

    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.stateless_http = stateless
    _warn_remote_exposure(host, auth_enabled)


def _warn_remote_exposure(host: str, auth_enabled: bool) -> None:
    """根据绑定地址与鉴权状态输出风险告警，同时写入日志与控制台。"""
    if host in _LOOPBACK_HOSTS:
        if auth_enabled:
            message = "streamable-http 已启用 Bearer 鉴权，本机访问需在 Authorization 头携带令牌。"
        else:
            message = (
                "streamable-http 未启用应用层认证，仅限本机信任环境使用；"
                "如需远程访问，请使用 --auth-token 配置鉴权或经反向代理增加鉴权。"
            )
    elif auth_enabled:
        message = (
            f"streamable-http 绑定到 {host}（非回环地址）并已启用 Bearer 鉴权；"
            "请确认网络隔离与令牌妥善保管。"
        )
    else:
        # 防御性死代码：cli_main 对非回环地址未启用鉴权已 fail-closed 拒绝启动，正常路径
        # 不可达此分支；保留以备绕过 cli_main 直接调用 _apply_http_bind_settings 时的告警。
        message = (
            f"streamable-http 绑定到 {host}（非回环地址）且未启用鉴权，存在未授权访问风险；"
            "请使用 --auth-token 配置鉴权。"
        )
    logger.warning(message)
    print(message)


async def _drain_pending_tasks() -> None:
    """取消并回收当前事件循环上的残余任务，避免 loop.close 触发 pending 警告。

    server.serve 返回后连接处理等任务可能仍待处理，直接关闭循环会跳过其连接清理
    finally 并产生 "Task was destroyed but it is pending!" 警告。排除当前自身任务后
    取消其余任务，并以 return_exceptions 等待其退出。
    """
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def _run_streamable_http(
    host: str,
    port: int,
    auth_token: str,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
) -> None:
    """
    启动 streamable-http 传输。

    配置鉴权令牌时，在 FastMCP 应用外层包裹 Bearer 校验中间件，未携带有效令牌的
    请求返回 401。配置 TLS 证书时透传给 uvicorn 启用 HTTPS。仅使用 FastMCP 公开接口
    streamable_http_app() 获取 ASGI 应用，避免依赖其私有鉴权装配路径。

    显式管理事件循环：uvicorn Server.serve 返回后循环仍存活，于其上运行共享资源的异步
    清理，使连接池在绑定的原循环上优雅释放。共享 HTTP 资源经 app_lifespan 创建并使用于
    该循环，跨循环 aclose 对底层传输无效，故关闭须在同一循环。stateless_http 模式下
    lifespan 不在 teardown 清理以保留连接复用，退出清理依赖此处完成。关闭循环前取消并
    回收残余任务，避免连接处理任务的清理 finally 被跳过。
    """
    import uvicorn

    from .server import _cleanup_shared_resources, mcp

    app = mcp.streamable_http_app()
    # streamable_http_app 首次调用后缓存 _session_manager 并固定中间件栈，重复调用会在同一
    # 缓存实例上叠加 add_middleware。生产路径 cli_main 单次调用无叠加风险；测试需重建栈时
    # 以 monkeypatch 重置 mcp._session_manager 为 None 强制重建，见 test_streamable_http_e2e。
    # Starlette add_middleware 经 insert(0) 使后添加者为更外层。装配目标执行序为
    # HealthCheck -> LimitRequestBody -> Bearer -> app：Bearer 最先添加居最内，其后添加
    # 请求体限制使其位于鉴权之外，从而声明超长 Content-Length 的请求在鉴权前即被 413 早拒。
    # 已认证的 chunked 请求由 receive 字节累计保护；未授权 chunked 请求不读 body 直接 401，
    # 其体积限制依赖 uvicorn 或反向代理层。
    if auth_token:
        app.add_middleware(_BearerTokenAuthMiddleware, expected_token=auth_token)
        logger.info("streamable-http 已启用 Bearer 令牌鉴权")
    app.add_middleware(
        _LimitRequestBodyMiddleware, max_body_size=get_active_config().http_max_body_size
    )
    # 健康检查最后添加，因 Starlette insert(0) 成为最外层，先于请求体限制与鉴权短路 GET /health
    app.add_middleware(_HealthCheckMiddleware)
    ssl_kwargs: dict[str, Any] = {}
    if ssl_certfile:
        ssl_kwargs["ssl_certfile"] = ssl_certfile
        ssl_kwargs["ssl_keyfile"] = ssl_keyfile
        logger.info("streamable-http 已启用 TLS")
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, **ssl_kwargs))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        try:
            loop.run_until_complete(_drain_pending_tasks())
        except Exception as exc:
            logger.warning("streamable-http 残余任务回收失败: {}", exc)
        try:
            loop.run_until_complete(_cleanup_shared_resources())
        except Exception as exc:
            logger.warning("streamable-http 退出清理失败: {}", exc)
        loop.close()
