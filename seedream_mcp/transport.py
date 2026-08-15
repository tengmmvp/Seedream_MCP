"""streamable-http 传输层：ASGI 中间件与传输配置。

包含请求体大小限制、Bearer 鉴权、健康检查、回环 Host 头防护四个 ASGI 中间件，以及
streamable-http 监听与 TLS 配置。中间件经 Starlette add_middleware 装配到 FastMCP 的
streamable_http_app 外层，按装配逆序执行。FastMCP 实例 mcp 与共享资源清理函数在调用时
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

from .config import get_active_config
from .utils.core.logs import get_logger

logger = get_logger(__name__)

# ==================== 传输层常量 ====================

# streamable-http 可信回环地址集合：仅字面量回环地址免鉴权。
# 不含 “localhost”，其解析依赖 hosts/DNS，污染时可指向非回环地址，
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
    之后，供 www-authenticate 等响应头复用，消除四个中间件的手写重复。
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

        if content_length > self._max_body_size:
            await self._send_too_large(send)
            return

        total_received = 0
        too_large = False
        response_started = False

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
            # 防御性不可达路径：现行 receive_wrapper 截断后，下游在读到空终帧时应在发出
            # 响应头之前失败或正常返回。若下游已开始响应再补发 http.response.start 会违反
            # ASGI 单响应约定，此处仅记日志，连接异常交由服务器协议层处理。
            if response_started:
                logger.warning("请求体超限但下游已开始响应，跳过补发 413 以避免双响应")
                return
            await self._send_too_large(send)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            # 下游读到被截断的空终帧后可能抛异常；too_large 时吞掉并统一回 413，避免冒泡为 500。
            if too_large:
                await _finalize_too_large()
                return
            raise

        if too_large:
            await _finalize_too_large()

    async def _send_too_large(self, send: Any) -> None:
        body = json.dumps(
            {"error": "request_too_large", "error_description": "Request body exceeds limit"}
        ).encode("utf-8")
        await _send_asgi_json(send, 413, body, extra_headers=((b"connection", b"close"),))


class _HealthCheckMiddleware:
    """streamable-http 健康检查中间件，短路 GET /health 返回进程存活状态。

    位于请求体限制与 Bearer 鉴权之外执行，负载均衡与健康探针无需令牌即可探活；
    回环绑定时位于 Host 校验之内，rebinding 域名请求连探活也被拒，本机探针的 Host
    恒为回环字面量或 localhost，不受影响。仅做 liveness 判定，不探测上游 API，
    避免拖慢探针。
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

    回环绑定且未配置鉴权时，浏览器经 DNS rebinding 可对 127.0.0.1 发起同源请求，
    绕过 CORS 直达工具面枚举文件或盗用 API key。校验 Host 头为回环地址可阻断外部
    域名请求；本地以 127.0.0.1/localhost/[::1] 访问不受影响。http 与 websocket 流量
    均校验：websocket 无 HTTP 状态码可回，参照鉴权中间件模式以 1008 关闭；Host 头
    缺失（HTTP/1.0 等路径）按 403 拒绝，与整层 fail-closed 取向一致。
    """

    # 允许的 Host 头值（剥离端口后），均解析到回环或即回环字面量。此处保留 “localhost”
    # 与 _LOOPBACK_HOSTS 排除它并不矛盾：绑定判定决定服务实际监听位置，hosts 污染会使
    # 绑定 localhost 实际暴露公网，故必须 fail-closed；而远程攻击者无法借污染令请求
    # 携带 Host: localhost 抵达本机（rebinding 场景 Host 恒为攻击者域名，伪造该 Host
    # 需本机裸 socket，等价于本地访问），容忍本地方便默认以 localhost 访问的客户端。
    # 不含未加方括号的裸 “::1”：IPv6 字面量在 Host 头中必须带方括号，裸形态属畸形，
    # 剥端口逻辑会将其截断为非回环值，按 fail-closed 拒绝。
    _ALLOWED_HOSTS = frozenset({b"127.0.0.1", b"localhost", b"[::1]"})

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type in ("http", "websocket"):
            host = self._host_header(scope)
            if host is None or self._strip_port(host) not in self._ALLOWED_HOSTS:
                if scope_type == "websocket":
                    # websocket 握手无 HTTP 状态码可回，按本文件鉴权中间件模式以
                    # 1008 Policy Violation 关闭，阻断 rebinding 借 websocket 绕过校验。
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
        # IPv6 字面量形如 [::1]:8000，保留含方括号的主机部分。
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
    """向 streamable-http app 装配中间件栈，重复装配时跳过。

    streamable_http_app 每次调用新建 Starlette app 但复用缓存的 _session_manager，
    同一 app 实例重复进入装配会在其用户中间件栈上叠加重复层。装配前检测本模块任一
    中间件已存在即整体跳过，使装配幂等；全新 app 正常装配。测试需重建会话管理器时
    以 monkeypatch 重置 mcp._session_manager 为 None 强制重建，见 test_streamable_http_e2e。

    Starlette add_middleware 经 insert(0) 使后添加者为更外层。装配目标执行序为
    LoopbackHostGuard（仅回环绑定）-> HealthCheck -> LimitRequestBody -> Bearer -> app：
    Bearer 最先添加居最内，其后添加请求体限制使其位于鉴权之外，从而声明超长
    Content-Length 的请求在鉴权前即被 413 早拒；健康检查再外一层，探针免令牌探活；
    回环绑定时 Host 校验最后添加居最外层，rebinding 请求先于健康检查被拒。
    已认证的 chunked 请求由 receive 字节累计保护；未授权 chunked 请求不读 body 直接 401，
    其体积限制依赖 uvicorn 或反向代理层。
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
    # 健康检查位于请求体限制与鉴权之外短路 GET /health，探针无需令牌；回环绑定时位于
    # Host 校验之内，rebinding 域名请求连探活也被拒，负载均衡探针以真实回环 Host 访问
    # 不受影响。
    app.add_middleware(_HealthCheckMiddleware)
    # 回环绑定时启用 Host 头校验，阻断 DNS rebinding 使外部域名请求直达本机服务。
    # 最后添加使其成为最外层，先于健康检查拒掉外部域名请求，本地 127.0.0.1/localhost
    # 访问不受影响。
    if host in _LOOPBACK_HOSTS:
        app.add_middleware(_LoopbackHostGuardMiddleware)


# ==================== streamable-http 传输配置 ====================


def _tls12_ssl_context_factory(
    config: Any, default_factory: Callable[[], ssl.SSLContext]
) -> ssl.SSLContext:
    """在 uvicorn 默认构造的 TLS 上下文之上强制最低协议版本 TLS 1.2。

    uvicorn 按 certfile/keyfile 自建的上下文未固定最低版本，旧客户端可能协商到
    已知弱点的 TLS 1.0/1.1。经 default_factory 取 uvicorn 默认上下文后抬高下限，
    证书加载等其余行为与 uvicorn 原生路径保持一致。
    """
    context = default_factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _resolve_http_auth_token(args: argparse.Namespace) -> str:
    """解析 streamable-http 鉴权令牌：CLI 参数优先，其次活动配置。"""
    token = args.auth_token or get_active_config().http_auth_token
    return (token or "").strip()


def _apply_http_bind_settings(host: str, port: int, stateless: bool, auth_enabled: bool) -> None:
    """
    将 streamable-http 监听配置写入 FastMCP settings，并就暴露风险与鉴权状态告警。

    生产链路由 cli_main 完成非回环绑定的鉴权与 TLS 前置校验后调用，非回环绑定必
    已启用鉴权；绕过 cli_main 直调本函数时无此保证，告警文案按传入的 auth_enabled
    据实输出。stateless 启用无状态模式，更适合远程多客户端与负载均衡场景。
    """
    from .resources import mcp

    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.stateless_http = stateless
    _warn_remote_exposure(host, auth_enabled)


def _warn_remote_exposure(host: str, auth_enabled: bool) -> None:
    """根据绑定地址与鉴权状态输出风险告警，同时写入日志与控制台。

    生产链路经 cli_main 调用时，非回环绑定的鉴权前置校验已 fail-closed 完成，
    非回环分支必走已启用鉴权文案；绕过 cli_main 直调本函数时鉴权可能未启用，
    未启用分支据实输出告警。任何调用路径都不得输出与生效配置相反的状态。
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
    请求返回 401。配置 TLS 证书时经 ssl_context_factory 构造最低 TLS 1.2 的服务端
    上下文交给 uvicorn 启用 HTTPS。仅使用 FastMCP 公开接口
    streamable_http_app() 获取 ASGI 应用，避免依赖其私有鉴权装配路径。

    显式管理事件循环：uvicorn Server.serve 返回后循环仍存活，于其上运行共享资源的异步
    清理，使连接池在绑定的原循环上优雅释放。共享 HTTP 资源经 app_lifespan 创建并使用于
    该循环，跨循环 aclose 对底层传输无效，故关闭须在同一循环。stateless_http 模式下
    lifespan 不在 teardown 清理以保留连接复用，退出清理依赖此处完成；stateful 模式正常
    在最后一个会话的 lifespan teardown 清理，此处兜底覆盖关闭时仍有在途会话的情形。
    关闭循环前取消并回收残余任务，避免连接处理任务的清理 finally 被跳过。
    """
    import uvicorn

    from .resources import _cleanup_shared_resources, mcp

    app = mcp.streamable_http_app()
    _attach_streamable_http_middleware(app, host, auth_token)
    ssl_kwargs: dict[str, Any] = {}
    if ssl_certfile:
        ssl_kwargs["ssl_certfile"] = ssl_certfile
        ssl_kwargs["ssl_keyfile"] = ssl_keyfile
        ssl_kwargs["ssl_context_factory"] = _tls12_ssl_context_factory
        logger.info("streamable-http 已启用 TLS，最低协议版本 TLS 1.2")
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
        # 复位线程事件循环引用：残留已关闭的循环会使后续 get_event_loop 取到不可用对象。
        asyncio.set_event_loop(None)
