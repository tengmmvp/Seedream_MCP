"""streamable-http 传输层：ASGI 中间件与传输配置。

包含请求体大小限制、Bearer 鉴权、健康检查、回环与非回环 Host 头防护、Web 操作台
同源 Origin 与跨站 Sec-Fetch 校验及内层异常边界七个 ASGI 中间件，以及 streamable-http
监听与 TLS 配置。中间件经 Starlette add_middleware 装配到 MCPServer 的
streamable_http_app 外层，按装配逆序执行。MCPServer 实例 mcp 与共享资源清理函数在
调用时从 resources 模块延迟导入，传输层不依赖 server 模块。
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import ipaddress
import json
import ssl
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ._config_sources import _bracket_ipv6_literal, _decompose_allowed_host_entry
from .config import get_active_config
from .utils.core.logs import get_logger

logger = get_logger()

# ==================== 传输层常量 ====================

# 回环白名单与 Host 守卫的基底地址集合，与 streamable_http_app 的默认防护
# 集合一致；localhost 解析依赖 hosts/DNS 可被污染，仅并入白名单不参与回环判定。
_DNS_REBINDING_PROTECTED_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _strip_ipv6_brackets(host: str) -> str:
    """剥离绑定地址的方括号，非方括号与带尾随内容的形态原样返回。

    方括号语法与配置侧共用 _bracket_ipv6_literal，绑定地址不含端口，仅右方括号
    收尾的纯方括号形态剥离，其余形态按原样参与回环与通配判定。
    """
    if not host.startswith("[") or host.find("]") != len(host) - 1:
        return host
    literal = _bracket_ipv6_literal(host)
    return host if literal is None else literal


def _is_loopback_address_literal(address: str) -> bool:
    """按 IP 解析判定回环，127/8 与 ::1 的等价写法同判回环，非 IP 字面量为 False。

    IPv4 映射形改判内嵌 IPv4：<3.12.4 的 IPv6Address.is_loopback 不识别映射回环。
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address):
        mapped = parsed.ipv4_mapped
        if mapped is not None:
            return mapped.is_loopback
    return parsed.is_loopback


def is_loopback_bind_host(host: str) -> bool:
    """判定绑定地址是否为回环地址，方括号形态与等价拼写同判回环。

    cli 安全校验与本模块中间件共用；getaddrinfo 把 ::01 一类等价写法解析到
    回环，仅按字面量集合判定会把纯回环绑定按非回环口径强制令牌与 TLS。
    """
    return _is_loopback_address_literal(_strip_ipv6_brackets(host))


def _is_dns_rebinding_protected_host(host: str) -> bool:
    """判定绑定地址是否启用 SDK 内层 DNS rebinding 防护，回环等价写法与 localhost 同判。

    绑定判定有意不含 localhost，防护判定须包含它，剥括号口径覆盖 [localhost] 形态。
    """
    return _strip_ipv6_brackets(host) == "localhost" or is_loopback_bind_host(host)


def _host_literal(address: str) -> str:
    """地址字面量的 Host 头主机形态，IPv6 按头语法加方括号。"""
    return f"[{address}]" if ":" in address else address


def _address_allowlist_forms(address: str) -> tuple[list[str], list[str]]:
    """由绑定地址字面量派生 Host/Origin 白名单条目，裸形态与端口通配成对。

    IPv6 字面量按 Host/Origin 头的方括号形态生成；裸形态放行默认端口部署的
    无端口头，Origin 同时覆盖 http 与 https 两种 scheme 的有端口与无端口形态。
    """
    literal = _host_literal(address)
    allowed_hosts = [literal, f"{literal}:*"]
    allowed_origins = [
        f"http://{literal}",
        f"http://{literal}:*",
        f"https://{literal}",
        f"https://{literal}:*",
    ]
    return allowed_hosts, allowed_origins


def _bind_address_allowlist(host: str) -> tuple[list[str], list[str]] | None:
    """由具体绑定地址推导 Host/Origin 默认白名单，通配绑定返回 None。

    通配判定经 ipaddress 解析覆盖 0.0.0.0、::、::0、0:0:0:0:0:0:0:0 等字面写法，
    非 IP 字面量按具体地址派生白名单。
    """
    stripped = _strip_ipv6_brackets(host)
    try:
        if ipaddress.ip_address(stripped).is_unspecified:
            return None
    except ValueError:
        pass
    return _address_allowlist_forms(stripped)


def _loopback_allowlists() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """对 DNS rebinding 防护地址集逐地址经绑定地址派生 Host/Origin 白名单成对返回。

    本机访问可经任一回环地址，缺一即误拒。
    """
    hosts: list[str] = []
    origins: list[str] = []
    # 排序使集合迭代有序，派生序列由测试锁定。
    for address in sorted(_DNS_REBINDING_PROTECTED_HOSTS):
        allowlist = _bind_address_allowlist(address)
        if allowlist is None:
            raise RuntimeError(f"回环地址 {address} 不应判为通配绑定")
        hosts.extend(allowlist[0])
        origins.extend(allowlist[1])
    return tuple(hosts), tuple(origins)


# 回环绑定 SDK 防护的 Host/Origin 白名单，Host 元组另供 Web 全 app 守卫共用。
_LOOPBACK_ALLOWED_HOSTS, _LOOPBACK_ALLOWED_ORIGINS = _loopback_allowlists()


def _loopback_bind_allowlists(host: str) -> tuple[list[str], list[str]]:
    """绑定地址字面量并入回环基底的 Host/Origin 白名单，供 SDK 内层防护配置。"""
    literal_forms = _bind_address_allowlist(host)
    if literal_forms is None:
        raise RuntimeError("DNS rebinding 防护地址不应判为通配绑定")
    hosts = list(dict.fromkeys([*_LOOPBACK_ALLOWED_HOSTS, *literal_forms[0]]))
    origins = list(dict.fromkeys([*_LOOPBACK_ALLOWED_ORIGINS, *literal_forms[1]]))
    return hosts, origins


# 回环绑定 Host 守卫的基底 bytes 集，由 DNS rebinding 防护地址集派生裸形态。
# 容忍 "localhost" 与绑定判定排除它并不矛盾：绑定 localhost 在 hosts 污染下
# 会实际暴露公网，须 fail-closed；rebinding 请求的 Host 恒为攻击者域名，
# 无法借污染携带 Host: localhost 抵达本机。派生不含 "::1"：Host 头中 IPv6
# 字面量必须带方括号，无方括号形态属畸形。
_LOOPBACK_GUARD_HOSTS = frozenset(
    _host_literal(address).encode("ascii") for address in _DNS_REBINDING_PROTECTED_HOSTS
)


def _loopback_bind_guard_hosts(host: str) -> frozenset[bytes]:
    """回环绑定的 Host 守卫 bytes 集：受保护集并入绑定字面量，等价写法漏并即误拒本机访问。"""
    return _LOOPBACK_GUARD_HOSTS | {_host_literal(_strip_ipv6_brackets(host)).encode("ascii")}


# 残余任务回收的最长等待秒数，超时即放弃等待交由循环关闭收尾；同时作为 uvicorn 优雅关停超时。
_DRAIN_PENDING_TIMEOUT_SECONDS = 5.0


# ==================== ASGI 辅助 ====================


async def _send_asgi_json(
    send: Send,
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


def _header_value(scope: Scope, name: bytes) -> bytes | None:
    """返回 scope 请求头中首个匹配名称的原始值，缺失时为 None。"""
    for header_name, value in scope.get("headers", []):
        if header_name == name:
            return value  # type: ignore[no-any-return]
    return None


def _error_body(error: str, description: str) -> bytes:
    """构造携带 error 与 error_description 两键的错误响应体字节串，各错误发送点共用。"""
    return json.dumps({"error": error, "error_description": description}).encode("utf-8")


async def _send_forbidden(send: Send, error: str, description: str) -> None:
    """发送 403 JSON 拒绝响应并附 connection: close，Host 与 Origin 守卫共用。"""
    await _send_asgi_json(
        send, 403, _error_body(error, description), extra_headers=((b"connection", b"close"),)
    )


# ==================== ASGI 中间件 ====================


class _BearerTokenAuthMiddleware:
    """streamable-http Bearer 令牌鉴权 ASGI 中间件。

    校验请求 Authorization 头中的 Bearer 令牌，匹配则放行，否则 HTTP 流量返回 401。
    启用鉴权时 websocket 流量以 code 1008 关闭，其余非 http 流量除 lifespan 外直接
    丢弃，避免绕过 Bearer 校验。
    使用 hmac.compare_digest 做常数时间比较，避免时序侧信道泄露令牌。

    exempt_exact 与 exempt_prefixes 声明免鉴权路径：Web 操作台的静态页面组
    使用，浏览器原生导航无法携带 Authorization 头。API 路径不得进入豁免表；
    路径含 ``..`` 时一律不豁免，与路由层穿越防护构成纵深。exact 匹配忽略尾
    斜杠差异，使 /web 与 /web/ 的豁免判定一致。
    """

    def __init__(
        self,
        app: ASGIApp,
        expected_token: str,
        exempt_exact: frozenset[str] = frozenset(),
        exempt_prefixes: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self._expected = expected_token.encode("utf-8")
        self._exempt_exact = frozenset(entry.rstrip("/") or "/" for entry in exempt_exact)
        self._exempt_prefixes = exempt_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type != "http":
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        token = self._bearer_token(scope)
        if self._path_exempt(scope) or self._request_authorized(token):
            await self.app(scope, receive, send)
            return

        await self._send_unauthorized(send, token is not None)

    def _path_exempt(self, scope: Scope) -> bool:
        """判定请求路径是否命中免鉴权表，含上跳段的路径拒绝豁免。"""
        path = scope.get("path", "")
        if ".." in path:
            return False
        normalized = path.rstrip("/") or "/"
        return normalized in self._exempt_exact or any(
            path.startswith(prefix) for prefix in self._exempt_prefixes
        )

    def _bearer_token(self, scope: Scope) -> bytes | None:
        """提取 Authorization 头中的 Bearer 令牌，头缺失、非 Bearer 方案或空白令牌均视为未携带。"""
        value = _header_value(scope, b"authorization")
        if value is None or value[:7].lower() != b"bearer ":
            return None
        # strip 后空白令牌同未携带凭据，归 None 使质询不带 error 码（RFC 6750 §3.1）。
        return value[7:].strip() or None

    def _request_authorized(self, token: bytes | None) -> bool:
        """判定提取的 Bearer 令牌是否匹配期望值，未携带的 None 同样拒绝。"""
        return token is not None and hmac.compare_digest(token, self._expected)

    async def _send_unauthorized(self, send: Send, bearer_scheme: bool) -> None:
        """发送 401 质询，形态按 RFC 6750 §3.1 区分。

        请求未携带 Bearer 凭据时质询不附 error 码，携带令牌且不匹配才用
        invalid_token，响应体错误码与质询形态同步。
        """
        if bearer_scheme:
            challenge = b'Bearer error="invalid_token"'
            error = "invalid_token"
            description = "Invalid or expired token"
        else:
            challenge = b"Bearer"
            error = "missing_token"
            description = "Authentication required"
        await _send_asgi_json(
            send,
            401,
            _error_body(error, description),
            extra_headers=(
                (b"www-authenticate", challenge),
                (b"connection", b"close"),
            ),
        )


class _LimitRequestBodyMiddleware:
    """streamable-http 请求体大小限制 ASGI 中间件。

    与 SDK 内层 RequestBodyLimitMiddleware 构成双层纵深：本层位于 Bearer 鉴权之外，
    声明超长 Content-Length 的请求在鉴权前即被 413 早拒；内层覆盖全部请求方法兜底。
    本层先按 Content-Length 头早拒，再包装 receive 累计实际接收字节数，防止谎报或
    缺失 Content-Length 的请求体撑爆内存；累计超限在判定点即经真实 send 直发 413，
    下游输出已转发时除外以免双响应，不等下游 app 收尾：客户端可能停发终帧，挂起的
    app 会使迟发的 413 永远发不出并把任务与连接钉住。超限后的 http.request 帧仅
    丢弃 body、保留真实帧的 more_body，其余帧原样透传，下游断连轮询在真实 receive
    上自然挂起，客户端收到 413 断开或发完终帧后请求收尾。仅作用于 http 请求，其余
    流量原样透传。
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self._max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_length = 0
        value = _header_value(scope, b"content-length")
        if value is not None:
            try:
                content_length = int(value)
            except ValueError:
                pass

        if content_length > self._max_body_size:
            await self._send_too_large(send)
            return

        total_received = 0
        too_large = False
        # forwarded 标记下游输出已触达传输层。
        forwarded = False

        async def receive_wrapper() -> Message:
            nonlocal total_received, too_large
            # 始终先等待真实 receive 再按需丢弃 body：同步合成帧会让下游断连
            # 轮询失去让出点、忙转冻结事件循环。
            message = await receive()
            if message.get("type") != "http.request":
                return message
            total_received += len(message.get("body", b""))
            if too_large or total_received > self._max_body_size:
                if not too_large:
                    too_large = True
                    if not forwarded:
                        # 判定点即直发 413：客户端可能停发终帧，等下游收尾再发会把
                        # 连接钉死在永远挂起的 app 上。
                        try:
                            await self._send_too_large(send)
                        except Exception:
                            logger.debug("请求体超限的 413 直发失败，连接可能已关闭")
                return {
                    "type": "http.request",
                    "body": b"",
                    "more_body": bool(message.get("more_body", False)),
                }
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal forwarded
            # 一旦判定超限，吞掉下游响应，避免与已直发的 413 构成双响应。
            if too_large:
                return
            # 置位须在真实转发之前：send 中途抛异常时协议状态不明，宁可视为已
            # 转发也不冒双响应风险。
            forwarded = True
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            # 下游读到被截断的空终帧后可能抛异常；too_large 时吞掉避免冒泡为 500。
            if too_large:
                if forwarded:
                    logger.warning("请求体超限但下游响应已转发，跳过补发 413 以避免双响应")
                return
            raise

        if too_large and forwarded:
            logger.warning("请求体超限但下游响应已转发，跳过补发 413 以避免双响应")

    async def _send_too_large(self, send: Send) -> None:
        await _send_asgi_json(
            send,
            413,
            _error_body("request_too_large", "Request body exceeds limit"),
            extra_headers=((b"connection", b"close"),),
        )


class _HealthCheckMiddleware:
    """streamable-http 健康检查中间件，短路 GET 与 HEAD /health 返回进程存活状态。

    位于请求体限制与鉴权之外，探针无需令牌；回环绑定时位于 Host 校验之内，
    rebinding 请求连探活也被拒。HEAD 按探活语义返回 200 空 body。仅做 liveness
    判定，不探测上游 API。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("method") in ("GET", "HEAD")
            and scope.get("path") == "/health"
        ):
            method = scope.get("method")
            body = b'{"status":"ok"}' if method == "GET" else b""
            await _send_asgi_json(send, 200, body)
            return
        await self.app(scope, receive, send)


class _LoopbackHostGuardMiddleware:
    """回环绑定时校验 Host 头，防 DNS rebinding 使外部域名请求直达本机服务。

    回环绑定且未配置鉴权时，浏览器经 DNS rebinding 可绕过 CORS 直达工具面；校验
    Host 为回环地址可阻断，本地以 127.0.0.1/localhost/[::1] 访问不受影响。http 与
    websocket 均校验，websocket 以 1008 关闭；Host 头缺失按 403 拒绝。
    """

    # 默认允许的 Host 头值，比较前已剥离端口；等价写法绑定在装配时并入其字面量。
    _ALLOWED_HOSTS = _LOOPBACK_GUARD_HOSTS

    def __init__(self, app: ASGIApp, allowed_hosts: frozenset[bytes] | None = None) -> None:
        self.app = app
        self._allowed_hosts = self._ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type in ("http", "websocket"):
            host = _header_value(scope, b"host")
            # 与 SDK 内层 Host 校验同为大小写敏感精确比较：本层拒绝的大写回环
            # Host 在内层同样不匹配白名单，大写形态由此 403 拒绝。
            if host is None or self._strip_port(host) not in self._allowed_hosts:
                if scope_type == "websocket":
                    # websocket 无 HTTP 状态码可回，按鉴权中间件模式以 1008 关闭。
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await _send_forbidden(send, "invalid_host", "Host not allowed")
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _strip_port(host: bytes) -> bytes:
        """剥离 Host 头值的端口部分，IPv6 字面量如 [::1]:8000 保留方括号主机部分。"""
        if host.startswith(b"["):
            end = host.find(b"]")
            return host[: end + 1] if end != -1 else host
        idx = host.rfind(b":")
        return host if idx == -1 else host[:idx]


class _ErrorBoundaryMiddleware:
    """内层未捕获异常转 500 响应的边界中间件。

    仅配置 CORS 时装配：异常冒泡到服务器层的 500 不经 CORS 携带放行头，跨源
    浏览器只能看到不可读的网络错误；本层位于 CORS 之内补发 500 使错误状态与
    响应体可读。响应已开始后无法补发，异常重新上抛交服务器断连。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.opt(exception=True).error("streamable-http 请求处理未捕获异常")
            if response_started:
                raise
            await _send_asgi_json(send, 500, _error_body("internal_error", "Internal server error"))


class _AppHostGuardMiddleware:
    """Web 开启且非回环绑定时对全 app 校验 Host 头的允许列表中间件。

    SDK 内层 Host/Origin 校验只覆盖 /mcp 端点，Web 静态面经 Bearer 豁免表免鉴权，
    无 Host 校验纵深；本层对全 app 兜底，允许列表经 _web_app_host_allowlist 求值。
    裸 host 匹配无端口 Host、host:port 精确匹配、host 通配端口条目匹配任意端口，
    三形态与 SDK 同语义；多冒号 Host 如 api.example.com:8000:9000 本层拒绝而 SDK
    纯前缀匹配放行，属有意的 fail-closed 分歧。无 Host 头与未命中按 403 拒绝，
    websocket 以 1008 关闭。
    """

    def __init__(self, app: ASGIApp, allowed_hosts: tuple[str, ...]) -> None:
        self.app = app
        bare: list[str] = []
        exact: list[str] = []
        wildcard: list[str] = []
        for entry in allowed_hosts:
            decomposed = _decompose_allowed_host_entry(entry)
            if decomposed is None:
                continue
            host_part, port_part = decomposed
            if port_part == "":
                bare.append(host_part)
            elif port_part == ":*":
                wildcard.append(host_part)
            else:
                exact.append(f"{host_part}{port_part}")
        self._bare_hosts = frozenset(value.encode("ascii") for value in bare)
        self._exact_hosts = frozenset(value.encode("ascii") for value in exact)
        self._wildcard_hosts = frozenset(value.encode("ascii") for value in wildcard)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type in ("http", "websocket"):
            host = _header_value(scope, b"host")
            if host is None or not self._host_permitted(host):
                if scope_type == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await _send_forbidden(send, "invalid_host", "Host not allowed")
                return
        await self.app(scope, receive, send)

    def _host_permitted(self, host: bytes) -> bool:
        """按裸 host、精确端口、端口通配三形态判定 Host 头值。

        通配条目以剥离末端口后的主机部分比对基值：多冒号 Host 剥离后仍含内层
        端口，不等基值而拒绝，比 SDK 的纯前缀 startswith(base_host + ":") 放行
        更严，属有意的 fail-closed 分歧；其余形态与 SDK 语义一致。
        """
        if host in self._bare_hosts or host in self._exact_hosts:
            return True
        stripped = _LoopbackHostGuardMiddleware._strip_port(host)
        return host.startswith(stripped + b":") and stripped in self._wildcard_hosts


class _WebOriginGuardMiddleware:
    """无令牌 Web 部署下 /web/api 前缀请求的同源 Origin 与跨站加载校验中间件。

    仅在 web_enabled 且未配置令牌时装配：有令牌时 Bearer 已挡 drive-by，无需
    本层。无 Origin 头放行，覆盖 curl 等非浏览器客户端与本地进程；携带 Origin
    的请求取其经 urlsplit 解析出的 netloc 与 Host 头全值做忽略大小写的字符串
    相等比对，端口参与比对：同源页面的 Origin 与请求 Host 恒为同一 host:port，
    域名不同、端口不一致、Origin 为 null 与 Host 缺失均按跨源 403 拒绝；配置的
    SEEDREAM_HTTP_ALLOWED_ORIGINS 条目为显式信任声明，命中的 Origin 同样放行
    且不查跨站标记，与 CORS 层放行口径一致。Origin 守卫覆盖不了不带 Origin 头
    的跨站 img/no-cors 加载，GET 与 HEAD 请求另按浏览器管控不可伪造的
    Sec-Fetch-Site 头拒绝 same-site 与 cross-site 形态：same-site 覆盖同注册域
    兄弟子域的图片嵌入，HEAD 覆盖 no-cors 探测；same-origin、none 与无该头的
    旧客户端放行。仅守 API 前缀，静态页面本身无敏感数据不做校验。
    """

    # 拒绝的 Sec-Fetch-Site 取值集合；same-origin 与 none 是合法流量不在其列。
    _REJECTED_FETCH_SITES = frozenset({b"same-site", b"cross-site"})

    def __init__(
        self,
        app: ASGIApp,
        api_prefix: str,
        allowed_origins: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self._api_prefix = api_prefix
        self._allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("path", "").startswith(self._api_prefix):
            origin = _header_value(scope, b"origin")
            origin_allowed = origin is not None and self._origin_permitted(scope, origin)
            if origin is not None and not origin_allowed:
                await _send_forbidden(send, "invalid_origin", "Cross-origin request rejected")
                return
            if not origin_allowed and self._cross_site_fetch(scope):
                await _send_forbidden(send, "cross_site_fetch", "Cross-site request rejected")
                return
        await self.app(scope, receive, send)

    def _origin_permitted(self, scope: Scope, origin: bytes) -> bool:
        """判定 Origin 同源或命中配置放行列表。

        与 CORS 层及 SDK 内层同为大小写敏感精确比较，三层共用一条规则；配置
        条目经构建期校验恒为小写，浏览器 Origin 亦恒为小写。
        """
        if self._same_origin(scope, origin):
            return True
        return origin.decode("latin-1") in self._allowed_origins

    @staticmethod
    def _same_origin(scope: Scope, origin: bytes) -> bool:
        """判定 Origin 与 Host 是否同源：两者 netloc 全值忽略大小写相等。

        Origin 解析不出 netloc、头值畸形无法解析或 Host 头缺失时无法确立同源，
        均按跨源拒绝；头值按 ASGI 约定以 latin-1 解码。
        """
        host = _header_value(scope, b"host")
        if not host:
            return False
        try:
            origin_netloc = urlsplit(origin.decode("latin-1")).netloc
        except ValueError:
            # 畸形 Origin 如未闭合的 IPv6 方括号会使 urlsplit 抛 ValueError，按
            # 跨源拒绝而非穿透为 500。
            return False
        if not origin_netloc:
            return False
        return origin_netloc.lower() == host.decode("latin-1").lower()

    @classmethod
    def _cross_site_fetch(cls, scope: Scope) -> bool:
        """判定 GET/HEAD 请求是否自报跨站：Sec-Fetch-Site 值为 same-site 或 cross-site。"""
        if scope.get("method") not in ("GET", "HEAD"):
            return False
        site = _header_value(scope, b"sec-fetch-site")
        if site is None:
            return False
        return site.strip().lower() in cls._REJECTED_FETCH_SITES


# ==================== streamable-http 中间件装配 ====================

# 本模块装配到 streamable-http app 的全部中间件类，供重复装配检测使用。
_STREAMABLE_HTTP_MIDDLEWARE_CLASSES = (
    _BearerTokenAuthMiddleware,
    _LimitRequestBodyMiddleware,
    _LoopbackHostGuardMiddleware,
    _AppHostGuardMiddleware,
    _WebOriginGuardMiddleware,
    _HealthCheckMiddleware,
    _ErrorBoundaryMiddleware,
)


def _web_app_host_allowlist(host: str, config_hosts: tuple[str, ...]) -> tuple[str, ...]:
    """Web 全 app Host 允许列表：hosts 配置优先，未配置时按绑定地址派生。

    回环基础形态无条件并入各分支白名单：静态骨架无数据，/web/api 与 /mcp
    另有鉴权与 SDK 内层校验把守。通配绑定派生不出条目，返回空元组使 Host
    校验层不装配，启动告警已提示配置 SEEDREAM_HTTP_ALLOWED_HOSTS。
    """
    if config_hosts:
        return tuple(dict.fromkeys([*_LOOPBACK_ALLOWED_HOSTS, *config_hosts]))
    if _bind_address_allowlist(host) is None:
        return ()
    return tuple(_loopback_bind_allowlists(host)[0])


def _middleware_attached(app: Any) -> bool:
    """检测 app 的用户中间件栈中是否已含本模块装配的任一中间件。"""
    for middleware in getattr(app, "user_middleware", ()):
        if getattr(middleware, "cls", None) in _STREAMABLE_HTTP_MIDDLEWARE_CLASSES:
            return True
    return False


def _attach_streamable_http_middleware(
    app: Any,
    host: str,
    auth_token: str,
    max_body_size: int | None = None,
    web_enabled: bool = False,
    allowed_origins: tuple[str, ...] | None = None,
) -> None:
    """向 streamable-http app 装配中间件栈，重复装配时跳过以保证幂等。

    max_body_size 与 allowed_origins 未显式传入时回退读取活动配置；生产调用方
    已取过该配置时显式传入，避免同一配置重复解析。web_enabled 开启时向 Bearer
    中间件传入 Web 静态页面的免鉴权路径表，API 路径始终要求令牌。

    Starlette add_middleware 经 insert(0) 使后添加者为更外层。装配目标执行序为
    HostGuard（回环绑定为 LoopbackHostGuard，web 开启的非回环绑定为 AppHostGuard）
    -> CORS（配置 http_allowed_origins 时）-> HealthCheck -> LimitRequestBody ->
    ErrorBoundary（配置 origins 时）-> 鉴权层 -> app，鉴权层为 Bearer（配置令牌时）
    或 Web Origin 守卫（web_enabled 且无令牌时，仅守 /web/api 前缀）；超长请求在
    鉴权前被 413 早拒且错误体经 CORS 层可被跨源客户端读取，健康探针同样经 CORS
    层携带放行头，探针免令牌，rebinding 请求先于健康检查被拒。
    """
    if _middleware_attached(app):
        logger.warning("streamable-http 中间件已装配，跳过重复装配以避免中间件栈叠加")
        return
    if allowed_origins is None:
        allowed_origins = get_active_config().http_allowed_origins or ()
    if auth_token:
        if web_enabled:
            from .webapp.constants import WEB_EXEMPT_EXACT_PATHS, WEB_EXEMPT_PATH_PREFIXES

            app.add_middleware(
                _BearerTokenAuthMiddleware,
                expected_token=auth_token,
                exempt_exact=WEB_EXEMPT_EXACT_PATHS,
                exempt_prefixes=WEB_EXEMPT_PATH_PREFIXES,
            )
            logger.info("Web 操作台已开启：静态页面免鉴权，/web/api 接口要求 Bearer 令牌")
        else:
            app.add_middleware(_BearerTokenAuthMiddleware, expected_token=auth_token)
        logger.info("streamable-http 已启用 Bearer 令牌鉴权")
    elif web_enabled:
        from .webapp.constants import WEB_API_PREFIX

        app.add_middleware(
            _WebOriginGuardMiddleware,
            api_prefix=f"{WEB_API_PREFIX}/",
            allowed_origins=allowed_origins,
        )
        logger.info("Web 操作台未配置令牌，已启用 /web/api 同源 Origin 与跨站 Sec-Fetch 校验")
    if max_body_size is None:
        max_body_size = get_active_config().http_max_body_size
    if allowed_origins:
        app.add_middleware(_ErrorBoundaryMiddleware)
    app.add_middleware(_LimitRequestBodyMiddleware, max_body_size=max_body_size)
    app.add_middleware(_HealthCheckMiddleware)
    if allowed_origins:
        # CORS 层位于健康检查、请求体上限与鉴权层之外，预检直达应答，三类错误
        # 响应携带放行头。
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],  # 供客户端读取会话 id 续连
            allow_private_network=True,  # PNA 预检放行，公网页面可达本地绑定
        )
        logger.info("streamable-http 已启用 CORS，放行 origin: {}", ", ".join(allowed_origins))
    if is_loopback_bind_host(host):
        guard_hosts = _loopback_bind_guard_hosts(host)
        app.add_middleware(_LoopbackHostGuardMiddleware, allowed_hosts=guard_hosts)
    elif web_enabled:
        allowlist = _web_app_host_allowlist(host, get_active_config().http_allowed_hosts or ())
        if allowlist:
            app.add_middleware(_AppHostGuardMiddleware, allowed_hosts=allowlist)


# ==================== streamable-http 传输配置 ====================


def _tls12_ssl_context_factory(
    config: Any, default_factory: Callable[[], ssl.SSLContext]
) -> ssl.SSLContext:
    """在 uvicorn 默认构造的 TLS 上下文之上强制最低协议版本 TLS 1.2。"""
    context = default_factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def resolve_http_auth_token(args: argparse.Namespace) -> str:
    """解析 streamable-http 鉴权令牌：CLI 参数优先，其次活动配置。

    CLI 令牌 strip 后为空（纯空白）视为未提供，穿透到活动配置，与配置侧
    「空白视为未设置」的语义一致。
    """
    cli_token = (args.auth_token or "").strip()
    token = cli_token or get_active_config().http_auth_token
    return (token or "").strip()


def _transport_security_for_host(host: str) -> TransportSecuritySettings:
    """按实际绑定地址派生 SDK 内层 DNS rebinding 防护配置。

    回环与 localhost 绑定启用防护并按回环白名单放行；非回环的具体地址绑定默认
    按绑定地址启用 Host/Origin 白名单（Origin 校验为规范 MUST），活动配置了
    SEEDREAM_HTTP_ALLOWED_HOSTS 时改为按该列表放行，条目支持 host、host:port 与
    尾部 :* 端口通配。配置 SEEDREAM_HTTP_ALLOWED_ORIGINS 时统一并入各分支的
    Origin 白名单（合并收尾单点完成），与 CORS 层同列表放行，预检应答与真实请求
    判定一致。通配地址绑定（0.0.0.0/:: 及其等价写法）下实际访问地址不可预知，默认不启用并
    输出告警，防护由强制 Bearer 鉴权承担。列表外的 Host 由 SDK 以 421、Origin
    以 403 拒绝；不携带 Origin 的非浏览器 MCP 客户端不受影响。
    """
    config = get_active_config()
    base_hosts: list[str]
    base_origins: list[str]
    if _is_dns_rebinding_protected_host(host):
        base_hosts, base_origins = _loopback_bind_allowlists(host)
    elif config.http_allowed_hosts:
        base_hosts = list(config.http_allowed_hosts)
        base_origins = []
    else:
        default_allowlist = _bind_address_allowlist(host)
        if default_allowlist is None:
            # security 标记的 WARNING 告警不受配置级别过滤，见 logs 的 sink 过滤。
            logger.bind(security=True).warning(
                "streamable-http 绑定通配地址 {}，Host/Origin 校验关闭，防护由强制 Bearer 鉴权承担，"
                "配置 SEEDREAM_HTTP_ALLOWED_HOSTS 可恢复校验",
                host,
            )
            return TransportSecuritySettings(enable_dns_rebinding_protection=False)
        base_hosts, base_origins = default_allowlist
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=base_hosts,
        allowed_origins=[*base_origins, *(config.http_allowed_origins or [])],
    )


def warn_remote_exposure(host: str, auth_enabled: bool) -> None:
    """按绑定地址与鉴权状态输出风险告警，内容须与生效配置一致。

    security 标记使 WARNING 告警免受配置级别过滤，见 logs 的 sink 过滤。
    """
    # localhost 的解析依赖 hosts/DNS 可被污染指向非回环地址，告警按非回环口径表述。
    host_note = "按非回环地址要求校验" if host == "localhost" else "非回环地址"
    if is_loopback_bind_host(host):
        if auth_enabled:
            message = "streamable-http 已启用 Bearer 鉴权，本机访问需在 Authorization 头携带令牌。"
        else:
            message = (
                "streamable-http 未启用鉴权，仅限本机信任环境使用；远程访问请配置 --auth-token。"
            )
    elif auth_enabled:
        message = (
            f"streamable-http 绑定到 {host}（{host_note}）且已启用 Bearer 鉴权，"
            "请确认网络隔离与令牌妥善保管。"
        )
    else:
        message = (
            f"streamable-http 绑定到 {host}（{host_note}）且未启用鉴权，存在未授权访问风险，"
            "请配置 --auth-token。"
        )
    logger.bind(security=True).warning(message)


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


def _build_streamable_app(host: str, stateless: bool, auth_token: str, web_enabled: bool) -> Any:
    """按生产装配序构造 streamable-http ASGI 应用并装配中间件，返回待 serve 的 app。

    装配序为：构造 transport_security -> 注册 Web 路由（web）-> streamable_http_app
    -> 挂载 Web 静态资源（web）-> 装配中间件。Web 路由注册必须先于
    streamable_http_app：SDK 构造 app 时一次性拷贝自定义路由引用，事后追加不生效；
    静态挂载则在 app 构造后向活体路由表追加。仅使用 MCPServer 公开接口
    streamable_http_app() 获取 ASGI 应用；max_request_body_size 显式传入活动配置的
    http_max_body_size，SDK 默认 4MiB 远低于本项目 base64 图片输入的 64MB 上限，
    该上限同时供 SDK 内层与本项目中间件两层消费。run_streamable_http 与生产装配
    测试共用本函数构建同源栈。
    """
    from .resources import mcp

    config = get_active_config()
    transport_security = _transport_security_for_host(host)
    max_body_size = config.http_max_body_size
    allowed_origins = config.http_allowed_origins
    if web_enabled:
        from .webapp import register_web_routes

        register_web_routes()
    app = mcp.streamable_http_app(
        host=host,
        stateless_http=stateless,
        transport_security=transport_security,
        max_request_body_size=max_body_size,
    )
    if not _is_dns_rebinding_protected_host(host):
        if transport_security.enable_dns_rebinding_protection:
            if config.http_allowed_hosts:
                logger.info(
                    "非回环绑定 {} 已启用 SDK Host 校验，按 SEEDREAM_HTTP_ALLOWED_HOSTS 白名单放行",
                    host,
                )
            else:
                logger.info(
                    "非回环绑定 {} 已启用 SDK Host/Origin 校验，按绑定地址派生白名单放行",
                    host,
                )
        else:
            logger.info(
                "非回环绑定 {} 已关闭 SDK 内层 Host 白名单，鉴权与 TLS 由本项目中间件承担",
                host,
            )
    if web_enabled:
        from .webapp import mount_web_static

        mount_web_static(app)
    _attach_streamable_http_middleware(
        app,
        host,
        auth_token,
        max_body_size=max_body_size,
        web_enabled=web_enabled,
        allowed_origins=allowed_origins,
    )
    return app


def run_streamable_http(
    host: str,
    port: int,
    auth_token: str,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    stateless: bool = False,
    web_enabled: bool = False,
) -> None:
    """启动 streamable-http 传输。

    app 构造与中间件装配经 _build_streamable_app 完成；TLS 经 ssl_context_factory
    强制最低 TLS 1.2。显式管理事件循环：serve 返回后于同一循环运行共享资源清理与
    残余任务回收，HTTP 传输绑定该循环，跨循环 aclose 对底层传输无效。
    """
    import uvicorn

    from .resources import _cleanup_shared_resources

    app = _build_streamable_app(host, stateless, auth_token, web_enabled)
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
        # 先清理共享资源再回收残余任务：资源清理内部会等待后台清理任务收尾，
        # 先行取消会使被等待的任务已取消、等待形同虚设。两段清理各自拦
        # BaseException：二次 Ctrl+C 与清理被取消均不得跳过其后的 loop.close
        # 与循环复位，吞掉后按既定顺序退出。
        try:
            loop.run_until_complete(_cleanup_shared_resources())
        except BaseException as exc:
            logger.warning("streamable-http 退出清理失败: {}", exc)
        try:
            loop.run_until_complete(_drain_pending_tasks())
        except BaseException as exc:
            logger.warning("streamable-http 残余任务回收失败: {}", exc)
        loop.close()
        # 复位线程事件循环引用：残留已关闭的循环会使后续 get_event_loop 取到不可用对象。
        asyncio.set_event_loop(None)
