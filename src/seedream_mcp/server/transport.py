"""streamable-http 传输层：ASGI 中间件与传输配置。

包含请求体大小限制、大请求体并发准入、Bearer 鉴权、健康检查、回环与非回环 Host 头防护、
Web 操作台同源 Origin 与跨站 Sec-Fetch 校验及内层异常边界八个 ASGI 中间件，
/.well-known/mcp 端点发现 303 重定向路由，以及 streamable-http 监听与 TLS 配置。
中间件经 Starlette add_middleware 装配到 MCPServer 的 streamable_http_app 外层，
按装配逆序执行。
MCPServer 实例 mcp 与共享资源清理函数在调用时从 runtime 模块延迟导入。
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import ipaddress
import json
import ssl
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final, cast
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import ClientDisconnect, Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config.host_syntax import (
    _bracket_ipv6_literal,
    _decompose_allowed_host_entry,
    _is_valid_host_port,
    bind_host_invalid_reason,
)
from ..config import get_active_config
from ..utils.core.errors import SeedreamConfigError
from ..utils.core.http_int import strict_http_int
from ..utils.core.logs import get_logger
from ..utils.core.loop_bound import loop_bound_semaphore
from ..utils.core.settle import await_settled_op, settle_detached_op

logger = get_logger()

# ==================== 传输层常量 ====================

# 回环白名单与 Host 守卫的基底地址集合，与 streamable_http_app 的默认防护
# 集合一致；localhost 解析依赖 hosts/DNS 可被污染，仅并入白名单不参与回环判定。
_DNS_REBINDING_PROTECTED_HOSTS = {"127.0.0.1", "::1", "localhost"}

# MCP 端点发现路径（RFC 8615 well-known URI）与实际端点路径；后者作为挂载路径传入 SDK。
WELL_KNOWN_MCP_PATH = "/.well-known/mcp"
MCP_ENDPOINT_PATH = "/mcp"

# 令牌部署恒豁免的精确路径，该路径只回无敏感信息的重定向。
ALWAYS_EXEMPT_EXACT_PATHS: frozenset[str] = frozenset({WELL_KNOWN_MCP_PATH})


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


def _ensure_printable_ascii_bind_host(host: str) -> None:
    """绑定地址守卫：空串与非可打印 ASCII 装配期拒绝，判定与文案取自 host_syntax 的共用谓词。"""
    invalid_reason = bind_host_invalid_reason(host)
    if invalid_reason is not None:
        raise SeedreamConfigError(f"streamable-http 绑定地址{invalid_reason}。")


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


def _merged_loopback_hosts(entries: Sequence[str]) -> tuple[str, ...]:
    """回环基底 Host 白名单并入绑定字面量或配置列表，保序去重。"""
    return tuple(dict.fromkeys([*_LOOPBACK_ALLOWED_HOSTS, *entries]))


def _loopback_bind_allowlists(host: str) -> tuple[list[str], list[str]]:
    """绑定地址字面量并入回环基底的 Host/Origin 白名单，供 SDK 内层防护配置。"""
    literal_forms = _bind_address_allowlist(host)
    if literal_forms is None:
        raise RuntimeError("DNS rebinding 防护地址不应判为通配绑定")
    hosts = list(_merged_loopback_hosts(literal_forms[0]))
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
    content_length: int | None = None,
) -> None:
    """发送统一格式的 JSON ASGI 响应，content-type 固定为 application/json。

    extra_headers 附加在标准头之后，供 www-authenticate 等响应头复用；
    content_length 显式声明时覆盖按 body 推得的值，供 HEAD 对齐 GET 响应头。
    """
    length = len(body) if content_length is None else content_length
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(length).encode("ascii")),
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


def _content_length_or_none(value: bytes | None) -> int | None:
    """解析 Content-Length 头值为 int，畸形形态经 strict_http_int 归 None 以免误判
    小申报绕过准入闸，None 入参策略由调用层定义。"""
    if value is None:
        return None
    return strict_http_int(value)


def _is_valid_host_port_bytes(port: bytes) -> bool:
    """bytes 端口经 host_syntax 的条目侧端口谓词判定，非 ASCII 字节按畸形拒绝。"""
    try:
        return _is_valid_host_port(port.decode("ascii"))
    except ValueError:
        return False


def _strip_host_port(host: bytes) -> bytes:
    """剥离 Host 头值的端口部分，IPv6 字面量如 [::1]:8000 保留方括号主机部分。

    端口形态共用 host_syntax 的条目侧端口谓词，畸形端口整值返回以偏离白名单
    条目；空端口形态（[::1]:）按 SDK 口径容忍；方括号后非冒号尾随内容同样
    整值返回。
    """
    if host.startswith(b"["):
        end = host.find(b"]")
        if end == -1:
            return host
        remainder = host[end + 1 :]
        if remainder in (b"", b":"):
            return host[: end + 1]
        if not remainder.startswith(b":") or not _is_valid_host_port_bytes(remainder[1:]):
            return host
        return host[: end + 1]
    idx = host.rfind(b":")
    if idx == -1:
        return host
    port = host[idx + 1 :]
    if port and not _is_valid_host_port_bytes(port):
        return host
    return host[:idx]


def _error_body(error: str, description: str) -> bytes:
    """构造携带 error 与 error_description 两键的错误响应体字节串，各错误发送点共用。"""
    return json.dumps({"error": error, "error_description": description}).encode("utf-8")


async def _send_rejection(send: Send, status: int, code: str, description: str) -> None:
    """尽力发送 JSON 拒绝响应并附 connection: close，全部拒绝发送点共用同一信封与吞没语义。"""
    await _send_best_effort(
        _send_asgi_json(
            send,
            status,
            _error_body(code, description),
            extra_headers=((b"connection", b"close"),),
        )
    )


async def _reject_body_too_large(send: Send) -> None:
    """发送 413 超限拒绝，早拒与流中判定两处共用同一错误码与描述。"""
    await _send_rejection(send, 413, "request_too_large", "Request body exceeds limit")


async def _send_best_effort(response: Awaitable[None]) -> None:
    """尽力发送短路响应，死连接上的直发失败降为 debug 静默，不冒泡为 ERROR 级 500。"""
    try:
        await response
    except Exception:
        logger.debug("短路响应直发失败，连接可能已关闭")


# ==================== ASGI 中间件 ====================


class _BearerTokenAuthMiddleware:
    """streamable-http Bearer 令牌鉴权 ASGI 中间件。

    校验请求 Authorization 头中的 Bearer 令牌，匹配则放行，否则 HTTP 流量返回 401。
    启用鉴权时 websocket 流量以 code 1008 关闭，其余非 http 流量除 lifespan 外直接
    丢弃，避免绕过 Bearer 校验。
    使用 hmac.compare_digest 做常数时间比较，避免时序侧信道泄露令牌。

    exempt_exact 与 exempt_prefixes 声明免鉴权路径：Web 操作台的静态页面组
    （浏览器原生导航无法携带 Authorization 头）与令牌部署恒豁免的端点发现
    路径（只回无敏感信息的重定向）使用。API 路径不得进入豁免表；路径含
    ``..`` 时一律不豁免，与路由层穿越防护构成纵深。exact 匹配忽略尾斜杠
    差异，使 /web 与 /web/ 的豁免判定一致。
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

        await _send_best_effort(self._send_unauthorized(send, token is not None))

    def _path_exempt(self, scope: Scope) -> bool:
        """判定路径是否命中豁免表，含上跳段的路径拒绝豁免。"""
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


def _wrap_send_with_verdict_guard(
    send: Send,
    suppressed: Callable[[], bool],
    on_first_response: Callable[[], None] | None = None,
) -> tuple[Send, Callable[[], bool]]:
    """构造裁决压制的 send 包装与已转发查询，双响应防护收在单点。

    suppressed 为真后吞没下游输出，避免与已直发的错误响应构成双响应；置位
    在真实转发之前：send 中途抛异常时协议状态不明，宁可视为已转发。
    on_first_response 在首个响应帧转发前触发恰一次，供调用方结束仅覆盖读体期的监护。
    """
    forwarded = False

    def is_forwarded() -> bool:
        return forwarded

    async def wrapper(message: Message) -> None:
        nonlocal forwarded
        if suppressed():
            return
        if (
            on_first_response is not None
            and not forwarded
            and message.get("type", "").startswith("http.response.")
        ):
            on_first_response()
        forwarded = True
        await send(message)

    return wrapper, is_forwarded


class _BodyReadPolicy:
    """单请求的读体策略对象：申报头单点解析、接收字节单点累计、超限裁决单点共享。

    由外层请求体上限中间件创建并经 scope 键交给内层准入层，两层复用同一申报值
    与同一计数，不重复解析头、不重复累计字节；流中超限裁决同样经此共享，
    内层准入见裁决即收尾归还许可，413 发出后另经裁决监听主动送达，
    不依赖下游拉帧观察。
    """

    def __init__(self, scope: Scope) -> None:
        content_length = _header_value(scope, b"content-length")
        self.declared = _content_length_or_none(content_length)
        self.content_length_present = content_length is not None
        self.transfer_encoded = _header_value(scope, b"transfer-encoding") is not None
        self.total_received = 0
        self.too_large = False
        self.verdict_listener: Callable[[], None] | None = None

    def has_conflicting_framing(self) -> bool:
        """Content-Length 与 Transfer-Encoding 并存属请求走私形态（RFC 9112 §6.3）。"""
        return self.content_length_present and self.transfer_encoded

    def mark_too_large(self) -> None:
        """记录外层流中超限裁决，内层准入据此即刻收尾，免占许可等读体窗口。"""
        self.too_large = True

    def deliver_verdict(self) -> None:
        """413 发出后送达裁决：触发准入层注册的监听，静默客户端场景也即刻归还许可。"""
        if self.verdict_listener is not None:
            self.verdict_listener()

    def count_request_frame(self, message: Message) -> None:
        """累计 http.request 帧的接收字节数，其余消息类型不计。"""
        if message.get("type") == "http.request":
            self.total_received += len(message.get("body", b""))


# 读体策略的 scope 交接键：外层上限层发布、内层准入层取回，配对保证由取回守卫承担。
_BODY_READ_POLICY_SCOPE_KEY: Final[str] = "seedream_body_read_policy"


def _resolve_body_read_policy(scope: Scope) -> _BodyReadPolicy:
    """内层准入中间件取回单请求策略，键缺失即装配失配，抛 RuntimeError 快失败。"""
    try:
        return cast(_BodyReadPolicy, scope[_BODY_READ_POLICY_SCOPE_KEY])
    except KeyError:
        raise RuntimeError("读体策略键缺失：读体限流层与准入层必须经装配函数成对挂载") from None


class _LimitRequestBodyMiddleware:
    """streamable-http 请求体大小限制 ASGI 中间件。

    与 SDK 内层 RequestBodyLimitMiddleware 构成双层纵深：本层位于 Bearer 鉴权之外，
    申报超长 Content-Length 与 Content-Length/Transfer-Encoding 并存的走私形态均在
    鉴权前早拒（413/400，不读体）；内层覆盖全部请求方法兜底。本层先按头早拒，
    再包装 receive 累计实际接收字节数，防止谎报或缺失 Content-Length 的请求体
    撑爆内存；累计超限在判定点即经真实 send 直发 413，下游输出已转发时除外
    以免双响应，不等下游 app 收尾：客户端可能停发终帧，挂起的 app 会使迟发的 413
    永远发不出并把任务与连接钉住。超限后的 http.request 帧仅丢弃 body、
    保留真实帧的 more_body，其余帧原样透传，下游断连轮询在真实 receive 上自然
    挂起，客户端收到 413 断开或发完终帧后请求收尾。申报解析与字节累计
    收在本层创建的 _BodyReadPolicy，经 scope 键与内层准入层共享（机制见该类 docstring）。
    仅作用于 http 请求，其余流量原样透传。
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self._max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        policy = _BodyReadPolicy(scope)
        if policy.has_conflicting_framing():
            # 并存双申报使读体语义不确定，按 nginx 同款处置不读体直接 400。
            await _send_rejection(
                send,
                400,
                "conflicting_framing",
                "Content-Length and Transfer-Encoding headers are mutually exclusive",
            )
            return
        if policy.declared is not None and policy.declared > self._max_body_size:
            await _reject_body_too_large(send)
            return
        scope[_BODY_READ_POLICY_SCOPE_KEY] = policy

        async def receive_wrapper() -> Message:
            # 始终先等待真实 receive 再按需丢弃 body：同步合成帧会让下游断连
            # 轮询失去让出点、忙转冻结事件循环。
            message = await receive()
            if message.get("type") != "http.request":
                return message
            policy.count_request_frame(message)
            if policy.too_large or policy.total_received > self._max_body_size:
                if not policy.too_large:
                    # 裁决写入共享策略：内层准入见裁决即刻归还许可，免等读体窗口。
                    policy.mark_too_large()
                    if not is_forwarded():
                        # 判定点即直发 413：客户端可能停发终帧，等下游收尾再发会把
                        # 连接钉死在永远挂起的 app 上。
                        await _reject_body_too_large(send)
                        # 413 发出后才送达裁决，看门狗对已裁决形态亦不强收：取消不
                        # 落在直发窗口内，本次发送不被截断；收到 413 即静默的客户端
                        # 也不把许可钉死到读体窗口耗尽。
                        policy.deliver_verdict()
                return {
                    "type": "http.request",
                    "body": b"",
                    "more_body": bool(message.get("more_body", False)),
                }
            return message

        send_wrapper, is_forwarded = _wrap_send_with_verdict_guard(send, lambda: policy.too_large)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            # 下游读到被截断的空终帧后可能抛异常；超限时吞掉避免冒泡为 500。
            if not policy.too_large:
                raise

        if policy.too_large and is_forwarded():
            logger.warning("请求体超限但下游响应已转发，跳过补发 413 以避免双响应")


# 大请求体准入阈值的档位上限：生效阈值经 _large_body_admission_threshold 取本档与
# http_max_body_size 的较小者，上限配置较小时阈值与请求体上限重合，越阈请求由上限层直接
# 413，并发闸不独立拦截。按申报尺寸判定可被谎报绕过为已知限制，谎报超限由请求体
# 上限中间件按实际接收字节兜底。
_LARGE_BODY_ADMISSION_THRESHOLD_CEILING_BYTES = 4 * 1024 * 1024


def _large_body_admission_threshold(max_body_size: int) -> int:
    """大请求体准入生效阈值：4MB 档与请求体上限取较小者。"""
    return min(_LARGE_BODY_ADMISSION_THRESHOLD_CEILING_BYTES, max_body_size)


# 无体方法集：这些方法的请求体无下游消费者，无有效申报且无 Transfer-Encoding 头
# 属正常形态（SSE 建流、静态页与会话删除），免监督直通；TRACE 按 RFC 9110
# 禁止携带请求体一并列入。
_BODYLESS_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "DELETE", "OPTIONS", "TRACE"})

# 读体窗口耗尽 408 的错误码与运维提示：有效旋钮是读体超时长度。
_BODY_READ_TIMEOUT_408 = (
    "body_read_timeout",
    "请求体读取超时，慢速上传可调大 SEEDREAM_BODY_READ_TIMEOUT",
)
# 许可等待超时 408 的错误码与运维提示：有效旋钮是准入并发数，与读体超时分码供监控区分。
_LARGE_BODY_ADMISSION_TIMEOUT_408 = (
    "large_body_admission_timeout",
    "大请求体准入许可等待超时，可调大 SEEDREAM_LARGE_BODY_ADMISSION_CONCURRENCY",
)


class _BodyReadAdmission:
    """单请求的读体准入状态机：许可持有、看门狗、裁决送达与放弃收尾收为显式字段与方法。

    由 _LargeBodyAdmissionMiddleware 对每个进入监督的请求创建并持其引用，
    准入常量与下游 app 经引用复用；下游经子任务驱动执行，看门狗取消只打在子任务上，
    监督任务的取消记账不经跨层操纵，取消与超时协议经方法边界可见。
    """

    def __init__(
        self,
        middleware: _LargeBodyAdmissionMiddleware,
        scope: Scope,
        receive: Receive,
        send: Send,
        policy: _BodyReadPolicy,
    ) -> None:
        self._middleware = middleware
        self._scope = scope
        self._receive = receive
        self._send = send
        self._policy = policy
        self._loop = asyncio.get_running_loop()
        self._read_deadline = self._loop.time() + middleware.read_timeout
        self.gate: asyncio.Semaphore | None = None
        self.watchdog: asyncio.TimerHandle | None = None
        self.driven_task: asyncio.Task[None] | None = None
        self.timeout_send: asyncio.Task[None] | None = None
        self.abandoned = False
        self.body_complete = False
        self._waiting_for_permit = False
        self._send_wrapper, self._is_forwarded = _wrap_send_with_verdict_guard(
            send, lambda: self.abandoned, self._finish_read_supervision
        )
        self._policy.verdict_listener = self._on_outer_verdict

    def _stop_watchdog(self) -> None:
        """拆除看门狗定时器，终帧与各收尾路径共用。"""
        if self.watchdog is not None:
            self.watchdog.cancel()
            self.watchdog = None

    def _release_gate(self) -> None:
        """归还准入许可并即弃引用，防同一许可经多条收尾路径二次归还。"""
        if self.gate is not None:
            self.gate.release()
            self.gate = None

    def _finish_read_supervision(self) -> None:
        """响应转发开始即读体监护使命完成：拆看门狗并归还许可，长响应不占闸。"""
        self._stop_watchdog()
        self._release_gate()

    async def _send_timeout_response(self, code: str, description: str) -> None:
        # 双响应防护与 408 发送单点：下游已转发时跳过，未转发则尽力发送。
        if not self._is_forwarded():
            await _send_rejection(self._send, 408, code, description)

    def _timeout_rejection(self) -> tuple[str, str]:
        """408 分码单点：等待准入许可中为准入饥饿口径，否则为读体窗口口径。"""
        if self._waiting_for_permit:
            return _LARGE_BODY_ADMISSION_TIMEOUT_408
        return _BODY_READ_TIMEOUT_408

    async def _acquire_gate(self) -> asyncio.Semaphore | None:
        # 按累计窗口剩余时限取准入许可，失败路径即按放弃原因发 408，窗口基准单一。
        pending = loop_bound_semaphore(
            self._middleware.admission_concurrency, key="large_body_admission"
        )
        remaining = self._read_deadline - self._loop.time()
        if remaining <= 0:
            self._begin_timeout_response(*self._timeout_rejection())
            return None
        # 许可等待中窗口到期属准入饥饿而非慢速读体。
        self._waiting_for_permit = True
        try:
            await asyncio.wait_for(pending.acquire(), timeout=remaining)
        except asyncio.TimeoutError:
            self._begin_timeout_response(*self._timeout_rejection())
            return None
        finally:
            self._waiting_for_permit = False
        return pending

    def _mark_abandoned(self) -> None:
        """放弃收尾共用前置：置位放弃并拆看门狗，幂等。"""
        if self.abandoned:
            return
        self.abandoned = True
        self._stop_watchdog()

    def _on_outer_verdict(self) -> None:
        """外层 413 已发出：标记放弃并取消下游子任务，监督者经取消路径退栈归还许可。"""
        self._mark_abandoned()
        # 裁决监听仅在脱离子任务的读体内触发，回调运行时子任务必已赋值。
        task = self.driven_task
        assert task is not None
        if not task.done():
            task.cancel()

    def _begin_timeout_response(self, code: str, description: str) -> None:
        # 408 收尾在放弃前置之上经脱离任务发送，幂等防双发。
        if self.abandoned:
            return
        self._mark_abandoned()
        self.timeout_send = asyncio.ensure_future(self._send_timeout_response(code, description))

    def _expire(self) -> None:
        # 窗口到期的强制收尾：终帧后不再约束，读取侧停滞时取消下游子任务防许可钉死。
        if self.body_complete or self.abandoned:
            return
        if self._policy.too_large:
            # 外层已裁决超限：413 直发即终局动作，此间取消下游会截断响应体，
            # 写背压挂起由 uvicorn 与客户端连接自身收敛，取消留待裁决送达。
            return
        self._begin_timeout_response(*self._timeout_rejection())
        # 看门狗装设与子任务赋值之间无 await，回调运行时子任务必已赋值。
        task = self.driven_task
        assert task is not None
        if not task.done():
            task.cancel()

    async def _receive_wrapper(self) -> Message:
        """tee 边转发边复用外层读体计数：实际越阈补取许可，终帧归还许可，外层超限裁决即放弃。"""
        if self.abandoned:
            # 与外层 receive_wrapper 的不变量同源：合成断连帧前让出，防下游断连轮询忙转。
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}
        if self.body_complete:
            # 终帧后仅剩断连轮询，读体窗口不再约束响应期长连接。
            return await self._receive()
        # 看门狗以单一截止定时器强制累计读体窗口，帧读取直 await 免逐帧 wait_for 开销。
        message = await self._receive()
        if message.get("type") != "http.request":
            return message
        if self._policy.too_large:
            # 外层已直发 413：见裁决即放弃归还许可，无视 413 的滴流客户端不得占闸至窗口耗尽。
            self._mark_abandoned()
            return {"type": "http.disconnect"}
        more_body = bool(message.get("more_body", False))
        if not more_body:
            # 终帧即读体完成：拆看门狗并归还许可，窗口不约束响应期、长响应不占闸。
            self.body_complete = True
            self._stop_watchdog()
            self._release_gate()
            return message
        if (
            self.gate is None
            and not self._is_forwarded()
            and self._policy.total_received > self._middleware.admission_threshold
        ):
            # 判定按实际累计字节，小申报谎报越阈与未申报同经此补取；越阈后的
            # 许可等待超时按断连收尾；响应转发开始后监护使命已完成，不再补取。
            pending = await self._acquire_gate()
            if pending is None:
                # 放弃收尾回放断连使下游退栈，408 已按放弃原因发出。
                return {"type": "http.disconnect"}
            self.gate = pending
        return message

    async def _finalize(self) -> None:
        """收尾单点：拆看门狗、归还许可并等待 408 脱离发送落定，正常与异常路径共用。"""
        self._stop_watchdog()
        self._release_gate()
        if self.timeout_send is not None:
            # 408 在本中间件返回前等待落定：ASGI 语义下 app 返回即终结响应，
            # 脱离发送在服务器写背压时会被截断；取消打断时等待落定后放行取消，
            # 在飞原始异常不被取消顶替。
            await await_settled_op(self.timeout_send)

    async def run(self) -> None:
        """子任务驱动下游并监督读体：越阈申报进入即持闸，看门狗自接管起算覆盖全部读体。"""
        if (
            self._policy.declared is not None
            and self._policy.declared > self._middleware.admission_threshold
        ):
            # 到达监督路径的越阈申报进入即持闸：排队等待计入读体窗口，占满时
            # 挂起有界，临近窗口末获许的请求可随即超窗 408。
            acquired = await self._acquire_gate()
            if acquired is None:
                # 未取得的许可不释放，gate 保持 None 防 finally 过量归还。
                await self._finalize()
                return
            self.gate = acquired
        # 看门狗自接管即装：首帧前停滞与累计读体窗口共用同一截止定时器，
        # 懒拉起需另设首帧定时器并在首帧换装，常开销更高。
        self.watchdog = self._loop.call_later(
            max(0.0, self._read_deadline - self._loop.time()), self._expire
        )
        # 下游经子任务驱动：SDK 内层 anyio CancelScope 与本层取消若共用监督任务，
        # 取消计数无 1:1 配对保证，隔离到子任务后取消语义各自闭合。
        try:
            self.driven_task = asyncio.ensure_future(
                self._middleware.app(self._scope, self._receive_wrapper, self._send_wrapper)
            )
            await asyncio.shield(self.driven_task)
        except asyncio.CancelledError:
            # 取消仅在 await 点送达，而子任务在唯一 await 前已同步赋值。
            task = self.driven_task
            assert task is not None
            if not task.cancelled():
                # 取消来自监督任务自身（服务器断连或关停）：转发子任务并待其落定
                # 后照抛。落定等待取有界档：吞取消的下游无界驻留时不把监督者与
                # 准入许可钉死，窗口内重复取消立即放行经 finally 归还许可——脱离子
                # 任务可能短暂孤儿收尾，其后续写入由 uvicorn 丢弃。
                task.cancel()
                await settle_detached_op(task)
                raise
            # 子任务已被看门狗取消或自行以取消收尾：监督任务未被取消，
            # 吞没后按排定的 408 正常收尾；同回合到达的监督任务取消也被本分支吞没，
            # 以 408 收尾替代取消态终结是有意接受的有界分歧。
            supervisor = asyncio.current_task()
            if supervisor is not None:
                # 吞没同回合监督任务取消后 uncancel 平衡取消计数，外层结构化取消语境不把正常收尾误判为取消在途。
                supervisor.uncancel()
        except ClientDisconnect:
            if not self.abandoned:
                # 客户端已断连，外层补 500 无意义，就地收尾；持闸与否即阶段上下文。
                logger.info("读体窗口内客户端断开，放弃该请求（持闸: {}）", self.gate is not None)
        finally:
            await self._finalize()


class _LargeBodyAdmissionMiddleware:
    """大请求体并发准入 ASGI 中间件，覆盖 /mcp 与 /web/api 全部请求体载体。

    进入监督的请求由 _BodyReadAdmission 承载单请求状态机：申报与未申报两类大请求体
    走同一条流式准入路径，receive 包装为 tee 边转发下游边复用外层读体策略的累计字节
    （计数单点在外层，经 scope 键交接），许可只覆盖读体窗口，
    终帧转发即释放，解析与响应不占闸，长生成不阻塞后续大请求读体；申报 Content-Length
    超过阈值的请求进入即取许可，未申报与小申报免许可直至实际累计越过阈值且未到终帧才补取，
    许可等待按剩余窗口限时。除免监督直通形态外全部读体一律受自接管起算的累计读体窗口
    body_read_timeout 约束：小申报免并发许可但不免窗口，
    撒谎申报的慢速上传无法把连接与下游任务无界钉死；超窗回 408 并按持有时点归还许可。
    外层流中 413 裁决经共享策略即时送达：准入回放断连并取消下游子任务，
    继续滴流与收 413 即静默的请求都不占闸等读体窗口。终帧后的断连轮询不再受窗口约束，响应期长连接不受影响；
    响应转发开始即结束监护：拆看门狗并归还许可，携 Transfer-Encoding 永不终结的 GET
    不把已开始的响应流截断在读体窗口；读体停滞时下游经看门狗强制收尾：
    读取侧不再被调用也钉不死许可，下游输出已转发时跳过 408 以免双响应；
    越阈后的许可等待超窗则向下游回放断连使其退栈；
    内存上界仍由外层请求体上限的流式计数兜底，异常收尾由 finally 归还许可。
    零申报与无体方法的无有效申报形态免监督直通，不被在闸大上传阻塞；
    携带申报体或 Transfer-Encoding 的请求不论方法仍进监督，
    鉴权豁免路径的合法 GET/HEAD 形态已由免监督直通覆盖，不另设豁免表。
    仅作用于 http 请求，其余流量原样透传。
    """

    def __init__(
        self,
        app: ASGIApp,
        admission_concurrency: int,
        read_timeout: float,
        admission_threshold: int,
    ) -> None:
        self.app = app
        self.admission_concurrency = admission_concurrency
        self.read_timeout = read_timeout
        self.admission_threshold = admission_threshold

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        policy = _resolve_body_read_policy(scope)
        if self._bypasses_supervision(policy, scope):
            await self.app(scope, receive, send)
            return
        session = _BodyReadAdmission(self, scope, receive, send, policy)
        await session.run()

    def _bypasses_supervision(self, policy: _BodyReadPolicy, scope: Scope) -> bool:
        """判定免监督直通：零申报无体可读，或无有效申报且未携带 Transfer-Encoding 的无体方法。"""
        if policy.declared is not None:
            return policy.declared == 0
        return scope.get("method") in _BODYLESS_METHODS and not policy.transfer_encoded


class _HealthCheckMiddleware:
    """streamable-http 健康检查中间件，短路 GET 与 HEAD /health 返回进程存活状态。

    位于请求体限制与鉴权之外，探针无需令牌；回环绑定时位于 Host 校验之内，
    rebinding 请求连探活也被拒。HEAD 返回与 GET 相同的响应头，仅省略内容。
    仅做 liveness 判定，不探测上游 API。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("method") in ("GET", "HEAD")
            and scope.get("path") == "/health"
        ):
            body = b'{"status":"ok"}'
            if scope.get("method") == "HEAD":
                await _send_asgi_json(send, 200, b"", content_length=len(body))
            else:
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
            if host is None or _strip_host_port(host) not in self._allowed_hosts:
                if scope_type == "websocket":
                    # websocket 无 HTTP 状态码可回，按鉴权中间件模式以 1008 关闭。
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await _send_rejection(send, 403, "invalid_host", "Host not allowed")
                return
        await self.app(scope, receive, send)


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
                    await _send_rejection(send, 403, "invalid_host", "Host not allowed")
                return
        await self.app(scope, receive, send)

    def _host_permitted(self, host: bytes) -> bool:
        """按裸 host、精确端口、端口通配三形态判定 Host 头值。

        通配条目以剥离末端口后的主机部分比对基值：多冒号 Host 剥离后仍含内层
        端口，畸形端口整值返回，均不等基值而拒绝，比 SDK 的纯前缀
        startswith(base_host + ":") 放行更严，属有意的 fail-closed 分歧；其余
        形态与 SDK 语义一致。
        """
        if host in self._bare_hosts or host in self._exact_hosts:
            return True
        stripped = _strip_host_port(host)
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
                await _send_rejection(send, 403, "invalid_origin", "Cross-origin request rejected")
                return
            if not origin_allowed and self._cross_site_fetch(scope):
                await _send_rejection(send, 403, "cross_site_fetch", "Cross-site request rejected")
                return
        await self.app(scope, receive, send)

    def _origin_permitted(self, scope: Scope, origin: bytes) -> bool:
        """判定 Origin 同源或命中配置放行列表。

        同源比对忽略大小写；放行列表匹配与 CORS 层及 SDK 内层同为大小写敏感
        精确比较，配置条目经构建期校验恒为小写，浏览器 Origin 亦恒为小写。
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
    _LargeBodyAdmissionMiddleware,
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
        return _merged_loopback_hosts(config_hosts)
    literal_forms = _bind_address_allowlist(host)
    if literal_forms is None:
        return ()
    return _merged_loopback_hosts(literal_forms[0])


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
    web_enabled: bool = False,
) -> None:
    """向 streamable-http app 装配中间件栈，重复装配时跳过以保证幂等。

    中间件旋钮与 web Host 守卫白名单均自单次读取的活动配置派生。
    任何令牌部署均豁免端点发现重定向路径，客户端免令牌即可发现实际端点；
    web_enabled 开启时并入 Web 静态页面免鉴权路径。豁免表仅传入 Bearer 层；
    大请求体准入层不识豁免表，豁免路径的合法 GET/HEAD 形态由无体方法免监督直通覆盖，
    大请求体准入阈值自请求体上限经 _large_body_admission_threshold 派生。

    Starlette add_middleware 经 insert(0) 使后添加者为更外层。装配目标执行序为
    HostGuard（回环绑定为 LoopbackHostGuard，web 开启的非回环绑定为 AppHostGuard）
    -> CORS（配置 http_allowed_origins 时）-> HealthCheck -> LimitRequestBody ->
    ErrorBoundary（配置 origins 时）-> 鉴权层 -> LargeBodyAdmission -> app，
    鉴权层为 Bearer（配置令牌时）或 Web Origin 守卫（web_enabled 且无令牌时，仅守
    /web/api 前缀），大请求体准入居鉴权层之内，未鉴权请求不占准入许可；超长请求
    与双申报走私形态在鉴权前被 413/400 早拒且错误体经 CORS 层可被跨源客户端读取，
    健康探针同样经 CORS 层携带放行头，探针免令牌，rebinding 请求先于健康检查被拒。
    读体两层（准入先装、上限收尾）在本函数内成对装配，单请求策略经模块级 scope 键
    交接，配对缺失由取回守卫快失败。
    """
    _ensure_printable_ascii_bind_host(host)
    if _middleware_attached(app):
        logger.warning("streamable-http 中间件已装配，跳过重复装配以避免中间件栈叠加")
        return
    config = get_active_config()
    max_body_size = config.http_max_body_size
    allowed_origins = config.http_allowed_origins or ()
    large_body_admission_concurrency = config.large_body_admission_concurrency
    body_read_timeout = config.body_read_timeout
    # Bearer 部署的豁免表单点求值：恒豁免端点发现，Web 静态豁免仅 web 开启时并入；
    # 无令牌部署无 Bearer 层，不豁免任何路径。
    exempt_exact: frozenset[str] = frozenset()
    exempt_prefixes: tuple[str, ...] = ()
    if auth_token:
        exempt_exact = ALWAYS_EXEMPT_EXACT_PATHS
        if web_enabled:
            from ..webapp.constants import WEB_EXEMPT_EXACT_PATHS, WEB_EXEMPT_PATH_PREFIXES

            exempt_exact |= WEB_EXEMPT_EXACT_PATHS
            exempt_prefixes = WEB_EXEMPT_PATH_PREFIXES
            logger.info("Web 操作台已开启：静态页面免鉴权，/web/api 接口要求 Bearer 令牌")

    # 准入层先装居读体两层之内，与收尾装配的上限层经 scope 键交接读体计数。
    app.add_middleware(
        _LargeBodyAdmissionMiddleware,
        admission_concurrency=large_body_admission_concurrency,
        read_timeout=body_read_timeout,
        admission_threshold=_large_body_admission_threshold(max_body_size),
    )
    # 鉴权层装入读体两层之间，准入闸由此位于鉴权层之内。
    if auth_token:
        app.add_middleware(
            _BearerTokenAuthMiddleware,
            expected_token=auth_token,
            exempt_exact=exempt_exact,
            exempt_prefixes=exempt_prefixes,
        )
        logger.info("streamable-http 已启用 Bearer 令牌鉴权")
    elif web_enabled:
        from ..webapp.constants import WEB_API_PREFIX

        app.add_middleware(
            _WebOriginGuardMiddleware,
            api_prefix=f"{WEB_API_PREFIX}/",
            allowed_origins=allowed_origins,
        )
        logger.info("Web 操作台未配置令牌，已启用 /web/api 同源 Origin 与跨站 Sec-Fetch 校验")
    if allowed_origins:
        app.add_middleware(_ErrorBoundaryMiddleware)
    # 上限层收尾装配成为更外层，包住准入层并经 scope 键交出读体计数。
    app.add_middleware(_LimitRequestBodyMiddleware, max_body_size=max_body_size)
    app.add_middleware(_HealthCheckMiddleware)
    if allowed_origins:
        # CORS 层位于健康检查、请求体上限与鉴权层之外，预检直达应答，三类错误
        # 响应携带放行头。
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_methods=["GET", "HEAD", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],  # 供客户端读取会话 id 续连
            allow_private_network=True,  # PNA 预检放行，公网页面可达本地绑定
        )
        logger.info("streamable-http 已启用 CORS，放行 origin: {}", ", ".join(allowed_origins))
    if is_loopback_bind_host(host):
        guard_hosts = _loopback_bind_guard_hosts(host)
        app.add_middleware(_LoopbackHostGuardMiddleware, allowed_hosts=guard_hosts)
    elif web_enabled:
        allowlist = _web_app_host_allowlist(host, config.http_allowed_hosts or ())
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

    回环与 localhost 绑定启用防护并按回环白名单放行，已配置的 SEEDREAM_HTTP_ALLOWED_HOSTS
    在此分支不生效并输出告警；非回环的具体地址绑定默认
    按绑定地址启用 Host/Origin 白名单（Origin 校验为规范 MUST），活动配置了
    SEEDREAM_HTTP_ALLOWED_HOSTS 时改为按该列表放行，条目支持 host、host:port 与
    尾部 :* 端口通配。配置 SEEDREAM_HTTP_ALLOWED_ORIGINS 时统一并入各分支的
    Origin 白名单（合并收尾单点完成），与 CORS 层同列表放行，预检应答与真实请求
    判定一致。通配地址绑定（0.0.0.0/:: 及其等价写法）下实际访问地址不可预知，默认不启用并
    输出告警，防护由强制 Bearer 鉴权承担。列表外的 Host 由 SDK 以 421、Origin
    以 403 拒绝；不携带 Origin 的非浏览器 MCP 客户端不受影响。
    """
    _ensure_printable_ascii_bind_host(host)
    config = get_active_config()
    base_hosts: list[str]
    base_origins: list[str]
    if _is_dns_rebinding_protected_host(host):
        base_hosts, base_origins = _loopback_bind_allowlists(host)
        if config.http_allowed_hosts:
            logger.bind(security=True).warning(
                "streamable-http 回环绑定 {} 下 SEEDREAM_HTTP_ALLOWED_HOSTS 不生效，"
                "Host 校验按回环白名单放行",
                host,
            )
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


async def _well_known_mcp_redirect(request: Request) -> Response:
    """端点发现路径的处理器，回 303 且 Location 相对指向实际端点。"""
    return RedirectResponse(MCP_ENDPOINT_PATH, status_code=303)


def _register_well_known_mcp_route() -> None:
    """注册 RFC 8615 端点发现重定向路由，幂等且须先于 streamable_http_app 调用。"""
    from .runtime import mcp

    # 幂等复查依赖 SDK 私有路由表，探测缺失时与 webapp 侧同口径告警后照常注册。
    custom_routes = getattr(mcp, "_custom_starlette_routes", None)
    if custom_routes is None:
        logger.error(
            "SDK 私有路由表 mcp._custom_starlette_routes 缺失，跳过幂等复查，" "请适配新版 MCP SDK"
        )
    else:
        for route in custom_routes:
            if getattr(route, "path", None) == WELL_KNOWN_MCP_PATH:
                return
    mcp.custom_route(WELL_KNOWN_MCP_PATH, methods=["GET"], include_in_schema=False)(
        _well_known_mcp_redirect
    )


def _build_streamable_app(host: str, stateless: bool, auth_token: str, web_enabled: bool) -> Any:
    """按生产装配序构造 streamable-http ASGI 应用并装配中间件，返回待 serve 的 app。

    装配序为：构造 transport_security -> 注册端点发现重定向路由 -> web 开启时注册
    Web 路由 -> streamable_http_app -> web 开启时挂载 Web 静态资源 -> 装配中间件。自定义
    路由注册必须先于 streamable_http_app：SDK 构造 app 时一次性拷贝路由表引用，
    事后追加不生效；静态挂载则在 app 构造后向活体路由表追加。仅使用 MCPServer 公开
    接口 streamable_http_app() 获取 ASGI 应用；max_request_body_size 显式传入活动配置的
    http_max_body_size，SDK 默认 4MB 远低于本项目 base64 图片输入的 64MB 上限，
    该上限同时供 SDK 内层与本项目中间件两层消费。run_streamable_http 与生产装配
    测试共用本函数构建同源栈。
    """
    from .runtime import mcp

    config = get_active_config()
    transport_security = _transport_security_for_host(host)
    _register_well_known_mcp_route()
    if web_enabled:
        from ..webapp import register_web_routes

        register_web_routes()
    app = mcp.streamable_http_app(
        host=host,
        stateless_http=stateless,
        streamable_http_path=MCP_ENDPOINT_PATH,
        transport_security=transport_security,
        max_request_body_size=config.http_max_body_size,
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
        from ..webapp import mount_web_static

        mount_web_static(app)
    _attach_streamable_http_middleware(app, host, auth_token, web_enabled=web_enabled)
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

    from .runtime import _cleanup_shared_resources

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
