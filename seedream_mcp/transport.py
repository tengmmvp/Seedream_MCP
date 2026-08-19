"""streamable-http 传输层：ASGI 中间件与传输配置。

包含请求体大小限制、Bearer 鉴权、健康检查、回环 Host 头防护四个 ASGI 中间件，以及
streamable-http 监听与 TLS 配置。中间件经 Starlette add_middleware 装配到 MCPServer 的
streamable_http_app 外层，按装配逆序执行。MCPServer 实例 mcp 与共享资源清理函数在调用时
从 resources 模块延迟导入，传输层不依赖 server 模块。
"""

from __future__ import annotations

# 标准库导入
import argparse
import asyncio
import hmac
import json
import ssl
import sys
from typing import Any, Callable

from mcp.server.transport_security import TransportSecuritySettings

from .config import get_active_config
from .utils.core.logs import get_logger

logger = get_logger(__name__)

# ==================== 传输层常量 ====================

# streamable-http 可信回环地址集合：仅字面量回环地址免鉴权。
# 不含 "localhost"：其解析依赖 hosts/DNS，污染时可指向非回环地址。
_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}

# 绑定即启用 SDK 内层 DNS rebinding 防护的地址集合，比 _LOOPBACK_HOSTS 多含
# "localhost"，与 streamable_http_app 的默认防护集合一致。
_DNS_REBINDING_PROTECTED_HOSTS = _LOOPBACK_HOSTS | {"localhost"}

# 回环绑定下 SDK 防护的 Host/Origin 白名单，端口通配，与
# _LoopbackHostGuardMiddleware 的容忍集合语义对齐。
_LOOPBACK_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOOPBACK_ALLOWED_ORIGINS = (
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
)

# 残余任务回收的最长等待秒数，超时即放弃等待交由循环关闭收尾。
_DRAIN_PENDING_TIMEOUT_SECONDS = 5.0


# ==================== ASGI 响应辅助 ====================


async def _send_asgi_json(
    send: Any,
    status: int,
    body: bytes,
    extra_headers: tuple[tuple[bytes, bytes], ...] = (),
) -> None:
    """发送统一格式的 JSON ASGI 响应，content-type 固定为 application/json。

    extra_headers 附加在标准头之后，供 www-authenticate 等响应头复用。
    """
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        # 鉴权失败、超限与健康探针响应禁止缓存，避免代理缓存敏感状态码响应。
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
    """streamable-http Bearer 令牌鉴权 ASGI 中间件。

    校验请求 Authorization 头中的 Bearer 令牌，匹配则放行，否则 HTTP 流量返回 401。
    启用鉴权时拒绝 websocket 等非 HTTP 流量并以 code 1008 关闭，避免绕过 Bearer 校验。
    使用 hmac.compare_digest 做常数时间比较，避免时序侧信道泄露令牌。
    """

    def __init__(self, app: Any, expected_token: str) -> None:
        self.app = app
        self._expected = expected_token.encode("utf-8")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """ASGI 调用入口：非 http 流量按类型处置，http 流量校验令牌后放行或回 401。"""
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type != "http":
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        if self._request_authorized(scope):
            await self.app(scope, receive, send)
            return

        await self._send_unauthorized(send)

    def _request_authorized(self, scope: Any) -> bool:
        """判定请求是否携带匹配的 Bearer 令牌，非 Bearer 授权方案同样拒绝。"""
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

    先按 Content-Length 头早拒超限请求，再包装 receive 累计实际接收字节数，超限
    短路返回 413，防止谎报或缺失 Content-Length 的超大请求体撑爆内存。仅作用于
    http 请求，其余流量原样透传。
    """

    def __init__(self, app: Any, max_body_size: int) -> None:
        self.app = app
        self._max_body_size = max_body_size

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """ASGI 调用入口：包装 receive 与 send 实施字节上限，超限短路返回 413。"""
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

        if content_length > self._max_body_size:
            await self._send_too_large(send)
            return

        total_received = 0
        too_large = False
        response_started = False

        async def receive_wrapper() -> Any:
            nonlocal total_received, too_large
            # 已判定超限后直接返回空终帧，切断剩余 body 投递。
            if too_large:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message.get("type") == "http.request":
                total_received += len(message.get("body", b""))
                if total_received > self._max_body_size:
                    too_large = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def send_wrapper(message: Any) -> None:
            nonlocal response_started
            # 下游发出首个响应头即视为响应已开始，此后补发 413 会构成双响应。
            if message.get("type") == "http.response.start":
                response_started = True
            # 一旦判定超限，吞掉下游响应，由本中间件统一回 413 避免双响应。
            if too_large:
                return
            await send(message)

        async def _finalize_too_large() -> None:
            # 防御性不可达路径：下游已开始响应时补发 413 会违反 ASGI 单响应约定，
            # 仅记日志。
            if response_started:
                logger.warning("请求体超限但下游已开始响应，跳过补发 413 以避免双响应")
                return
            await self._send_too_large(send)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            # 下游读到被截断的空终帧后可能抛异常；too_large 时吞掉并统一回 413，
            # 避免冒泡为 500。
            if too_large:
                await _finalize_too_large()
                return
            raise

        if too_large:
            # 客户端超限后断开时，补发 413 可能对已死连接抛异常，吞掉仅记 debug。
            try:
                await _finalize_too_large()
            except Exception:
                logger.debug("请求体超限后补发 413 失败，连接可能已关闭")

    async def _send_too_large(self, send: Any) -> None:
        body = json.dumps(
            {"error": "request_too_large", "error_description": "Request body exceeds limit"}
        ).encode("utf-8")
        await _send_asgi_json(send, 413, body, extra_headers=((b"connection", b"close"),))


class _HealthCheckMiddleware:
    """streamable-http 健康检查中间件，短路 GET /health 返回进程存活状态。

    位于请求体限制与鉴权之外，探针无需令牌；回环绑定时位于 Host 校验之内，
    rebinding 请求连探活也被拒。仅做 liveness 判定，不探测上游 API。
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


class _LoopbackHostGuardMiddleware:
    """回环绑定时校验 Host 头，防 DNS rebinding 使外部域名请求直达本机服务。

    回环绑定且未配置鉴权时，浏览器经 DNS rebinding 可绕过 CORS 直达工具面；校验
    Host 为回环地址可阻断，本地以 127.0.0.1/localhost/[::1] 访问不受影响。http 与
    websocket 均校验，websocket 以 1008 关闭；Host 头缺失按 403 拒绝。
    """

    # 允许的 Host 头值（剥离端口后）。容忍 "localhost" 与绑定判定排除它并不矛盾：
    # 绑定 localhost 在 hosts 污染下会实际暴露公网，须 fail-closed；而 rebinding
    # 请求的 Host 恒为攻击者域名，无法借污染携带 Host: localhost 抵达本机。不含
    # 裸 "::1"：Host 头中 IPv6 字面量必须带方括号，裸形态属畸形。
    _ALLOWED_HOSTS = frozenset({b"127.0.0.1", b"localhost", b"[::1]"})

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type in ("http", "websocket"):
            host = self._host_header(scope)
            # Host 头的主机名大小写不敏感，比较前统一小写，避免大写回环 Host 被误拒。
            if host is None or self._strip_port(host).lower() not in self._ALLOWED_HOSTS:
                if scope_type == "websocket":
                    # websocket 无 HTTP 状态码可回，按鉴权中间件模式以 1008 关闭。
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await self._send_forbidden(send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _host_header(scope: Any) -> bytes | None:
        for name, value in scope.get("headers", []):
            if name == b"host":
                return value  # type: ignore[no-any-return]
        return None

    @staticmethod
    def _strip_port(host: bytes) -> bytes:
        """剥离 Host 头值的端口部分，IPv6 字面量如 [::1]:8000 保留方括号主机部分。"""
        if host.startswith(b"["):
            end = host.find(b"]")
            return host[: end + 1] if end != -1 else host
        idx = host.rfind(b":")
        return host if idx == -1 else host[:idx]

    async def _send_forbidden(self, send: Any) -> None:
        body = json.dumps(
            {"error": "invalid_host", "error_description": "Host not allowed"}
        ).encode("utf-8")
        await _send_asgi_json(send, 403, body, extra_headers=((b"connection", b"close"),))


# ==================== streamable-http 中间件装配 ====================

# 本模块装配到 streamable-http app 的全部中间件类，供重复装配检测使用。
_STREAMABLE_HTTP_MIDDLEWARE_CLASSES = (
    _BearerTokenAuthMiddleware,
    _LimitRequestBodyMiddleware,
    _LoopbackHostGuardMiddleware,
    _HealthCheckMiddleware,
)


def _middleware_attached(app: Any) -> bool:
    """检测 app 的用户中间件栈中是否已含本模块装配的任一中间件。"""
    for middleware in getattr(app, "user_middleware", ()):
        if getattr(middleware, "cls", None) in _STREAMABLE_HTTP_MIDDLEWARE_CLASSES:
            return True
    return False


def _attach_streamable_http_middleware(app: Any, host: str, auth_token: str) -> None:
    """向 streamable-http app 装配中间件栈，重复装配时跳过以保证幂等。

    Starlette add_middleware 经 insert(0) 使后添加者为更外层。装配目标执行序为
    LoopbackHostGuard（仅回环绑定）-> HealthCheck -> LimitRequestBody -> Bearer
    -> app：声明超长 Content-Length 的请求在鉴权前即被 413 早拒，探针免令牌探活，
    回环绑定时 rebinding 请求先于健康检查被拒。
    """
    if _middleware_attached(app):
        logger.warning("streamable-http 中间件已装配，跳过重复装配以避免中间件栈叠加")
        return
    if auth_token:
        app.add_middleware(_BearerTokenAuthMiddleware, expected_token=auth_token)
        logger.info("streamable-http 已启用 Bearer 令牌鉴权")
    app.add_middleware(
        _LimitRequestBodyMiddleware, max_body_size=get_active_config().http_max_body_size
    )
    # 健康检查在鉴权之外短路 GET /health，探针免令牌。
    app.add_middleware(_HealthCheckMiddleware)
    # 回环绑定时最外层启用 Host 校验，先于健康检查拒掉 rebinding 请求。
    if host in _LOOPBACK_HOSTS:
        app.add_middleware(_LoopbackHostGuardMiddleware)


# ==================== streamable-http 传输配置 ====================


def _tls12_ssl_context_factory(
    config: Any, default_factory: Callable[[], ssl.SSLContext]
) -> ssl.SSLContext:
    """在 uvicorn 默认构造的 TLS 上下文之上强制最低协议版本 TLS 1.2。"""
    context = default_factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _resolve_http_auth_token(args: argparse.Namespace) -> str:
    """解析 streamable-http 鉴权令牌：CLI 参数优先，其次活动配置。"""
    token = args.auth_token or get_active_config().http_auth_token
    return (token or "").strip()


def _transport_security_for_host(host: str) -> TransportSecuritySettings:
    """按实际绑定地址派生 SDK 内层 DNS rebinding 防护配置。

    回环与 localhost 绑定启用防护并按回环白名单放行；非回环绑定默认整体关闭，
    活动配置了 SEEDREAM_HTTP_ALLOWED_HOSTS 时改为启用并按该列表放行，条目支持
    host、host:port 与尾部 :* 端口通配，Host 不在列表的请求由 SDK 以 421 拒绝。
    """
    if host in _DNS_REBINDING_PROTECTED_HOSTS:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(_LOOPBACK_ALLOWED_HOSTS),
            allowed_origins=list(_LOOPBACK_ALLOWED_ORIGINS),
        )
    allowed_hosts = get_active_config().http_allowed_hosts
    if allowed_hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _warn_remote_exposure(host: str, auth_enabled: bool) -> None:
    """按绑定地址与鉴权状态输出风险告警，同时写日志与 stderr。

    任何调用路径都不得输出与生效配置相反的状态。
    """
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
        message = (
            f"streamable-http 绑定到 {host}（非回环地址）且未启用鉴权，存在未授权访问风险；"
            "请使用 --auth-token 配置鉴权。"
        )
    logger.warning(message)
    # 控制台输出走 stderr，与 server.py 的运行告警一致，避免污染 stdio 传输的 stdout。
    print(message, file=sys.stderr)


async def _drain_pending_tasks() -> None:
    """取消并回收当前事件循环上的残余任务，避免 loop.close 触发 pending 警告。

    排除自身任务后取消其余任务并带超时等待回收；超时后放弃等待交由循环关闭收尾，
    至多遗留 pending 警告，不阻塞进程退出。
    """
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current]
    for task in pending:
        task.cancel()
    if not pending:
        return
    _, unfinished = await asyncio.wait(pending, timeout=_DRAIN_PENDING_TIMEOUT_SECONDS)
    if unfinished:
        logger.warning(
            "回收残余任务超时，放弃等待 {} 个未退出任务",
            len(unfinished),
        )


def _run_streamable_http(
    host: str,
    port: int,
    auth_token: str,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    stateless: bool = False,
) -> None:
    """启动 streamable-http 传输。

    仅使用 MCPServer 公开接口 streamable_http_app() 获取 ASGI 应用；鉴权与请求体
    限制经本项目中间件承担，TLS 经 ssl_context_factory 强制最低 TLS 1.2。
    max_request_body_size 须显式传入活动配置的 http_max_body_size，SDK 默认 4MiB
    远低于本项目 base64 图片输入的 64MB 上限。

    显式管理事件循环：serve 返回后于同一循环运行共享资源清理与残余任务回收，
    HTTP 传输绑定该循环，跨循环 aclose 对底层传输无效。
    """
    import uvicorn

    from .resources import _cleanup_shared_resources, mcp

    transport_security = _transport_security_for_host(host)
    app = mcp.streamable_http_app(
        host=host,
        stateless_http=stateless,
        transport_security=transport_security,
        max_request_body_size=get_active_config().http_max_body_size,
    )
    if host not in _DNS_REBINDING_PROTECTED_HOSTS:
        if transport_security.enable_dns_rebinding_protection:
            logger.info(
                "非回环绑定 {} 已启用 SDK Host 校验，按 SEEDREAM_HTTP_ALLOWED_HOSTS 白名单放行",
                host,
            )
        else:
            logger.info(
                "非回环绑定 {} 已关闭 SDK 内层 Host 白名单，鉴权与 TLS 由本项目中间件承担",
                host,
            )
    _attach_streamable_http_middleware(app, host, auth_token)
    ssl_kwargs: dict[str, Any] = {}
    if ssl_certfile:
        ssl_kwargs["ssl_certfile"] = ssl_certfile
        ssl_kwargs["ssl_keyfile"] = ssl_keyfile
        ssl_kwargs["ssl_context_factory"] = _tls12_ssl_context_factory
        logger.info("streamable-http 已启用 TLS，最低协议版本 TLS 1.2")
    # uvicorn 缺省无限等待连接排空，长开 SSE 流会使 serve() 在 SIGTERM 后永不返回；
    # 取有界超时，超时后取消在途连接并进入本函数的退出清理。
    # log_config=None 跳过 uvicorn 内置 dictConfig，uvicorn.* 日志传播到 root
    # logger 经 InterceptHandler 汇入 loguru：access log 不再写 stdout 独立通道，
    # 控制字符防护与文件日志通道对 uvicorn 日志同样生效。
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            timeout_graceful_shutdown=int(_DRAIN_PENDING_TIMEOUT_SECONDS),
            log_config=None,
            **ssl_kwargs,
        )
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        # 两段清理各自拦 BaseException：二次 Ctrl+C 与清理被取消均不得跳过其后的
        # loop.close 与循环复位，吞掉后按既定顺序退出。
        try:
            loop.run_until_complete(_drain_pending_tasks())
        except BaseException as exc:
            logger.warning("streamable-http 残余任务回收失败: {}", exc)
        try:
            loop.run_until_complete(_cleanup_shared_resources())
        except BaseException as exc:
            logger.warning("streamable-http 退出清理失败: {}", exc)
        loop.close()
        # 复位线程事件循环引用：残留已关闭的循环会使后续 get_event_loop 取到不可用对象。
        asyncio.set_event_loop(None)
