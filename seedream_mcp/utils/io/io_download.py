"""异步图片下载管理器，提供带 SSRF 多层防护的安全下载能力。

核心安全设计为四层 SSRF 防护，纵深防御逐层收紧：

1. 静态 URL 校验：由 ``_validate_url_static`` 实现，解析阶段即拒绝私网、保留地址及
   非公网 IP 字面量，把 ``file://``、``http://127.0.0.1`` 等直接伪造挡在网络层外。
2. DNS 公网解析与连接钉死：由 ``_resolve_public_ips`` 校验主机名解析结果均为公网 IP，
   会话连接器绑定 ``_PublicIpPinningResolver`` 把连接目标钉死为校验通过的公网 IP，
   aiohttp 不在连接前二次独立解析，从根本上闭合 DNS rebinding 窗口。
3. 连接后对端 IP 复核：由 ``_validate_connected_peer_ip`` 实现，实际建立连接后再次校验
   对端 IP，作为第二层钉死之上的纵深防御，应对解析器与连接之间的残余窗口。
4. 逐跳重定向校验：由 ``_attempt_download`` 的重定向循环实现，禁用自动重定向，每跳目标都
   重新走完整校验，防止经由重定向绕过跳转到内网；跳向不同源时剥离调用方定制请求头，
   仅保留通用头，防止定制头原样发给重定向目标。

其余关键设计：失败按递增延迟加随机抖动重试；下载先写 ``tempfile.mkstemp`` 生成的
不可预测随机名临时文件再 ``os.replace`` 原子替换，避免半写文件对外可见并规避可预测
路径被预置符号链接覆盖；响应须经 Content-Type 与字节签名双重校验后方落盘，防
Content-Type 伪造。
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import random
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiofiles
import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from ...version import __version__
from ..core.errors import SeedreamMCPError
from ..core.formats import (
    DEFAULT_MAX_FILE_SIZE,
    SNIFF_HEAD_BYTES_FLOOR,
    infer_extension_from_bytes,
    is_known_image_bytes,
)
from ..core.logs import get_logger, log_unretrieved_task_exception
from .io_file import atomic_replace_from_fd

logger = get_logger(__name__)

# 流式下载块大小：较大块减少多 MB 图片的 await write 循环次数。
_DOWNLOAD_CHUNK_SIZE = 256 * 1024

# 落盘批量写阈值：累积至该字节数才提交一次 aiofiles 写任务，减少执行器跳转次数。
_WRITE_BATCH_BYTES = 4 * 1024 * 1024

# 重定向上限：逐跳手动跟踪并限制跳数，防止经由重定向链绕过 SSRF 校验。
_MAX_REDIRECTS = 3

# 跨源重定向时保留的通用请求头：与目标主机无关、不含调用方定制信息。其余请求头
# （如为原主机定制的鉴权或跟踪头）在跳向不同源时剥离，不原样发给重定向目标。
_CROSS_ORIGIN_SAFE_REQUEST_HEADERS = frozenset({"user-agent", "accept"})

# DNS 缓存条目硬上限：超限时先清理过期条目，仍超限则按最旧 expires_at 强制驱逐，
# 防止长生命周期下持续解析不同 host 导致缓存无界增长。
_DNS_CACHE_MAX_SIZE = 256

# Windows getaddrinfo 失败抛 gaierror 时 errno 携带 winsock2.h 的 WSA 错误码，而非
# POSIX EAI_* 常量（Windows 的 Python 通常不暴露 EAI_* 别名），须以字面值并入终态
# 集合才能在 Windows 生效：
#   11001 WSAHOST_NOT_FOUND：主机不存在，对应 POSIX EAI_NONAME 终态。
#   11004 WSANO_DATA：无该类型 DNS 记录，对应 POSIX EAI_NODATA 终态。
#   11003 WSANO_RECOVERY：不可恢复的解析器故障，对应 POSIX EAI_FAIL 类终态。
# 11002 WSATRY_AGAIN 对应 EAI_AGAIN 瞬时故障，保持可重试不入终态集合。POSIX 平台的
# EAI_* 码为负值或个位小值，与上述正数值不相交，双平台码表经数值并集统一且无歧义。
_WSA_HOST_NOT_FOUND = 11001
_WSA_NO_DATA = 11004
_WSA_NO_RECOVERY = 11003

# getaddrinfo 的 gaierror 中属永久失败的错误码集合：域名不存在、查询参数不受支持或
# 不可恢复的解析器故障（EAI_FAIL 为 non-recoverable failure），重试无法恢复。其余
# 错误码一律按可重试分类，包括 EAI_AGAIN 等瞬时解析故障；瞬时抖动远多于永久错误，
# 且重试次数上限兜底。平台缺少对应常量时经 getattr 取 None 后从集合中剔除。
_TERMINAL_GAI_ERRNOS = frozenset(
    code
    for code in (
        getattr(socket, "EAI_NONAME", None),
        getattr(socket, "EAI_NODATA", None),
        getattr(socket, "EAI_FAIL", None),
        getattr(socket, "EAI_SERVICE", None),
        getattr(socket, "EAI_FAMILY", None),
        getattr(socket, "EAI_SOCKTYPE", None),
        getattr(socket, "EAI_BADFLAGS", None),
        _WSA_HOST_NOT_FOUND,
        _WSA_NO_DATA,
        _WSA_NO_RECOVERY,
    )
    if code is not None
)


def sanitize_url(url: str) -> str:
    """脱敏 URL 用于日志，保留 scheme/host/path，剥离凭据、查询参数与控制字符。

    控制字符 CRLF 等会被剥离，防止攻击者经由 URL 在日志中伪造行，注入误导性记录。

    Args:
        url: 原始 URL 字符串。

    Returns:
        脱敏后的 URL；解析失败返回 ``<invalid-url>``。
    """
    try:
        parsed = urlparse(url)
        # 重建不含 userinfo 的 netloc，避免 user:pass@ 凭据进入日志。
        # hostname 对 IPv6 字面量会剥离方括号，重建时需补回，否则 IPv6 字面量的
        # host 与端口边界将变得模糊。
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = host
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.query:
            result = f"{parsed.scheme}://{netloc}{parsed.path}?<query-redacted>"
        else:
            result = f"{parsed.scheme}://{netloc}{parsed.path}"
    except Exception:
        return "<invalid-url>"
    # 剥离控制字符，防止 CRLF 经 URL 注入伪造日志行。
    return re.sub(r"[\x00-\x1f\x7f]", "", result)


def _url_origin(url: str) -> tuple[str, str, int]:
    """返回 URL 的请求源三元组 (scheme, host, effective_port)，供跨源判定。

    未显式给出端口时按 scheme 取默认端口，使 ``https://a.example`` 与
    ``https://a.example:443`` 判定为同源。端口字段非法时按默认端口处理，该 URL
    的请求本身会因语法问题被下游拒绝，此处不提前报错。
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def _strip_custom_headers_for_cross_origin(headers: dict[str, str]) -> dict[str, str]:
    """剥离跨源跳转不应转发的定制请求头，仅保留通用安全头。"""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in _CROSS_ORIGIN_SAFE_REQUEST_HEADERS
    }


# 部分服务以通用二进制类型返回图片，故即使非 image/* 也视为合法二进制响应。
_BINARY_CONTENT_TYPES = frozenset(
    {"application/octet-stream", "application/binary", "binary/octet-stream"}
)

# RFC 6598 运营商级 NAT 地址段。Python 较新版本 is_global 已排除此段，
# 此处前置显式判断以给出精确拒绝原因，并对 is_global 实现差异保持纵深防御。
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
# 可封装内网 IPv4 的 IPv6 段：NAT64、IPv4-mapped、IPv4-compatible。
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")
_IPV4_MAPPED_NETWORK = ipaddress.ip_network("::ffff:0:0/96")
_IPV4_COMPAT_NETWORK = ipaddress.ip_network("::/96")


def _embedded_ipv4_in_six(
    ip_obj: ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | None:
    """提取 NAT64/IPv4-mapped/IPv4-compatible 段内嵌的 IPv4 地址，其他返回 None。"""
    for network in (_NAT64_NETWORK, _IPV4_MAPPED_NETWORK, _IPV4_COMPAT_NETWORK):
        if ip_obj in network:
            return ipaddress.IPv4Address(int(ip_obj) & 0xFFFFFFFF)
    return None


def _public_ip_rejection_reason(
    ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str | None:
    """返回 IP 不可作为公网下载目标的拒绝原因，None 表示通过。

    统一静态 URL、DNS 解析、连接对端 IP 三处校验：拒绝非公网地址、RFC 6598 CGNAT 段，
    以及 6to4/Teredo 等可封装内网地址的 IPv6 段。入参覆盖 ip_address 解析可能返回的
    IPv4 与 IPv6 两类地址，递归校验 IPv6 内嵌 IPv4 时传入提取出的 IPv4Address。
    """
    if ip_obj.version == 4 and ip_obj in _CGNAT_NETWORK:
        return "CGNAT地址(100.64.0.0/10)"
    if not ip_obj.is_global:
        return "非公网地址"
    # isinstance 收窄到 IPv6Address，使内嵌段提取与 sixtofour/teredo 属性访问均受类型校验。
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.sixtofour is not None or ip_obj.teredo is not None:
            return "6to4/Teredo地址"
        embedded = _embedded_ipv4_in_six(ip_obj)
        if embedded is not None:
            reason = _public_ip_rejection_reason(embedded)
            if reason:
                return f"IPv6内嵌{reason}"
    return None


def _is_image_compatible_content_type(content_type: str) -> bool:
    """判断响应内容类型是否兼容图片下载。

    image/* 与通用二进制类型视为兼容；空类型视为兼容，交由字节签名嗅探兜底。
    明确的非图片类型（text/html、application/json 等）不兼容，调用方应据此拒绝下载。
    SVG 虽属 image/*，但本质是 XML 文本，可内嵌脚本与外部实体，存在 XSS 与 XXE 风险，
    不作为图片下载。
    """
    normalized = content_type.split(";")[0].strip().lower()
    if not normalized:
        return True
    if normalized in {"image/svg+xml", "image/svg"}:
        return False
    return normalized.startswith("image/") or normalized in _BINARY_CONTENT_TYPES


class DownloadError(SeedreamMCPError):
    """下载失败异常。"""

    pass


class RetryableDownloadError(DownloadError):
    """可重试的下载错误，如 HTTP 5xx、DNS 瞬时解析失败等瞬时故障。

    继承 DownloadError，可被按 DownloadError 捕获的调用方统一处理；重试循环中
    须先于 DownloadError 单独捕获，将其作为可重试故障而非终态错误处理。
    """

    pass


class _PublicIpPinningResolver(AbstractResolver):
    """自定义 DNS 解析器，把连接目标钉死为经 SSRF 公网校验的 IP，防 DNS rebinding。

    aiohttp 默认解析器会在连接前独立解析主机名，存在静态校验与连接之间的 DNS
    rebinding 窗口。本解析器接管解析，域名结果一律经 ``_resolve_public_ips`` 公网
    校验，使 aiohttp 只能连接到校验通过的公网 IP。返回结果保留 URL 原始主机名作为
    ``hostname``，aiohttp 据此设置 TLS 的 SNI 与证书校验目标，故证书校验不被削弱。
    IP 字面量由 aiohttp 连接器在调用解析器前直接短路，此处不再重复处理。
    """

    def __init__(self, manager: "DownloadManager") -> None:
        self._manager = manager

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        """解析主机名为经公网校验的 IP 列表，连接目标随之钉死为校验结果。

        返回的 ResolveResult 保留 URL 原始主机名作为 hostname，供 aiohttp 设置 TLS
        的 SNI 与证书校验目标。解析失败的异常语义由 ``_resolve_public_ips`` 定义。

        Args:
            host: 待解析的主机名。
            port: 目标端口，原样写入解析结果。
            family: 调用方请求的地址族，本实现忽略。

        Returns:
            全部校验通过 IP 及其真实地址族构成的解析结果列表。

        Raises:
            DownloadError: 解析结果为空、含非法 IP 或命中非公网地址等终态失败。
            RetryableDownloadError: DNS 瞬时解析故障等可重试失败。
        """
        # 忽略调用方请求的 family，返回全部校验通过 IP 及其真实 family，由 aiohttp 选择。
        ips = await self._manager._resolve_public_ips(host)
        results: list[ResolveResult] = []
        for ip in ips:
            ip_obj = ipaddress.ip_address(ip)
            ip_family = socket.AF_INET6 if ip_obj.version == 6 else socket.AF_INET
            results.append(
                ResolveResult(
                    hostname=host,
                    host=ip,
                    port=port,
                    family=ip_family,
                    proto=socket.IPPROTO_TCP,
                    flags=socket.AI_NUMERICHOST,
                )
            )
        return results

    async def close(self) -> None:
        pass


class DownloadManager:
    """异步图片下载管理器，内置 SSRF 四层防护与递增退避重试。

    Attributes:
        timeout: 下载超时时间（秒）。
        max_retries: 最大重试次数。
        retry_delay: 重试延迟时间（秒）。
        max_file_size: 最大文件大小（字节）。
    """

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        dns_cache_ttl: int = 60,
        connection_limit: int | None = None,
    ):
        """初始化下载管理器。

        Args:
            timeout: 下载超时时间（秒）。
            max_retries: 最大重试次数。
            retry_delay: 重试延迟时间（秒）。
            max_file_size: 最大文件大小（字节）。
            dns_cache_ttl: DNS 解析缓存 TTL（秒）。
            connection_limit: 底层连接器的并发连接上限，None 时沿用 aiohttp 默认。供
                资源层施加进程级下载并发上限；经构造参数传入使会话因 close 重建时
                连接器自动保持同一上限，不依赖调用方在会话建立后二次注入。
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_file_size = max_file_size
        self._connection_limit = connection_limit
        self._dns_cache_ttl = max(1, dns_cache_ttl)
        self._dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
        self._dns_inflight: dict[str, asyncio.Task[tuple[str, ...]]] = {}
        self._dns_cache_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> DownloadManager:
        """进入上下文，确保会话就绪后返回自身。"""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """退出上下文时关闭底层会话。"""
        await self.close()

    def _download_total_budget(self) -> float:
        """返回单次下载会话、跨尝试累计与重定向链逐跳累计共用的总时长上限，单位秒。

        上限按停滞超时的 120 倍推导且不低于 1 小时，默认配置下为 1 小时（50MB 上限
        对应约 14KB/s 平均速率），只封堵恶意慢速滴流，不影响正常慢网络下大图片的完整
        下载。会话 timeout、download_image 的跨尝试累计预算与 _attempt_download 的
        重定向链逐跳累计校验取同一值：无累计校验时慢滴流响应每次尝试都可耗满单次
        封顶，重定向链每跳又各享全额单次窗口，重试与跳数把单个保存任务的最长占用
        放大为单次封顶乘尝试数；三处取同一值后总占用被约束在单次封顶的小常数倍内。
        """
        return max(float(self.timeout) * 120, 3600.0)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """获取可用的 aiohttp 会话。

        首次创建用双检锁串行化，避免并发请求各自创建会话导致旧会话泄漏。会话绑定
        ``_PublicIpPinningResolver`` 的连接器，使 aiohttp 连接目标经 SSRF 公网校验后
        钉死，不在连接前二次独立解析，闭合 DNS rebinding 窗口。构造期注入的
        connection_limit 在每次构造连接器时传入，会话重建分支自动保持同一上限。

        超时策略：sock_connect 与 sock_read 仅约束连接建立与单次读取停滞，服务端按
        停滞阈值间歇发送字节的慢滴流响应可无限拖住任务，故另设宽松的总时长上限
        封顶整个下载，上限值经 _download_total_budget 推导。
        """
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    connector_kwargs: dict[str, Any] = {"resolver": _PublicIpPinningResolver(self)}
                    if self._connection_limit is not None:
                        connector_kwargs["limit"] = self._connection_limit
                    connector = aiohttp.TCPConnector(**connector_kwargs)
                    self._session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(
                            total=self._download_total_budget(),
                            sock_connect=self.timeout,
                            sock_read=self.timeout,
                        ),
                        trust_env=False,
                        cookie_jar=aiohttp.DummyCookieJar(),
                        connector=connector,
                    )
        return self._session

    async def close(self) -> None:
        """关闭底层会话资源。

        与 ``_ensure_session`` 共用 ``_session_lock`` 串行化会话的创建与关闭，
        避免并发关闭与创建交错导致会话泄漏或误用。实际 ``session.close()`` 置于
        锁外执行，不在 await I/O 期间持锁。
        """
        async with self._session_lock:
            session = self._session
            self._session = None
        if session is not None and not session.closed:
            await session.close()

    def _validate_url_static(self, url: str) -> tuple[str, bool]:
        """执行不依赖网络的 URL 静态安全校验，属 SSRF 第一层防护。

        解析阶段即拒绝非 http/https 协议、缺失主机名、携带凭据、本地主机名以及
        非公网 IP 字面量，把明显伪造的内网目标挡在网络层之外。

        Args:
            url: 待校验的下载 URL。

        Returns:
            (host, needs_dns_check): ``needs_dns_check`` 为 False 表示入参为已通过
            校验的 IP 字面量，调用方无需再做 DNS 解析校验。

        Raises:
            DownloadError: 协议不受支持、主机名缺失、携带凭据、本地主机名或非公网
                IP 字面量。
        """
        result = urlparse(url)
        scheme = (result.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise DownloadError(f"不支持的URL协议: {scheme or '<empty>'}")

        if not result.netloc or not result.hostname:
            raise DownloadError("URL 缺少主机名")

        if result.username or result.password:
            raise DownloadError("URL 不允许包含账号或密码")

        host = result.hostname.strip().lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise DownloadError(f"不安全的本地主机地址: {host}")

        # 直接 IP 地址：仅允许公网，并拒绝 6to4/Teredo 等可封装内网地址的 IPv6 段。
        try:
            ip = ipaddress.ip_address(host)
            reason = _public_ip_rejection_reason(ip)
            if reason:
                raise DownloadError(f"不安全的IP地址({reason}): {host}")
            return host, False
        except ValueError:
            return host, True

    async def _resolve_public_ips(self, host: str) -> tuple[str, ...]:
        """解析域名并校验所有解析结果为公网 IP，属 SSRF 第二层防护。

        防 DNS rebinding：攻击者可能让静态校验阶段解析到公网 IP，随后在真正
        发起连接前将 DNS 切换到内网地址。解析结果带 TTL 缓存以减少重复查询。

        同 host 并发下载在缓存冷启动时经在途 task 去重共享一次 getaddrinfo：缓存 miss
        且无在途 task 时创建并登记，并发调用 await 同一 task，避免 N 个并发下载各自触发
        系统解析。在途 task 经 asyncio.shield 隔离取消传播，创建者被取消时底层 task 继续
        运行至完成并写入缓存，保护共享同一 task 的其他等待者。

        Args:
            host: 待解析并校验的主机名。

        Returns:
            该主机名全部校验通过的公网 IP 元组。

        Raises:
            DownloadError: 解析结果为空、含非法 IP 或命中非公网地址等终态失败。
            RetryableDownloadError: DNS 瞬时解析故障等可重试失败。
        """
        now = time.time()
        async with self._dns_cache_lock:
            cached = self._dns_cache.get(host)
            if cached is not None:
                expires_at, cached_ips = cached
                if expires_at > now:
                    return cached_ips
                self._dns_cache.pop(host, None)
            inflight = self._dns_inflight.get(host)
            if inflight is None:
                # 缓存与在途登记在同一锁区间内完成，中间无 await，单线程事件循环下
                # 并发协程不会交错创建重复 task。
                inflight = asyncio.ensure_future(self._resolve_and_cache(host))
                self._dns_inflight[host] = inflight
                # 检索共享 task 的异常结果：创建者被取消后 shield 的 outer 不再消费
                # task 结果，无其他等待者时避免 "Task exception was never retrieved" 噪音。
                inflight.add_done_callback(log_unretrieved_task_exception)
        # 锁外 await 共享在途 task；shield 使创建者被取消时不连带取消底层 task。
        return await asyncio.shield(inflight)

    async def _resolve_and_cache(self, host: str) -> tuple[str, ...]:
        """执行单次解析与公网校验并写入缓存，供 _resolve_public_ips 在途去重。

        finally 清理在途 task，使后续缓存过期或失败后的请求可重新发起解析。
        """
        try:
            ip_tuple = await self._resolve_public_ips_uncached(host)
            async with self._dns_cache_lock:
                self._dns_cache[host] = (time.time() + self._dns_cache_ttl, ip_tuple)
                if len(self._dns_cache) > _DNS_CACHE_MAX_SIZE:
                    self._enforce_dns_cache_limit()
            return ip_tuple
        finally:
            async with self._dns_cache_lock:
                self._dns_inflight.pop(host, None)

    async def _resolve_public_ips_uncached(self, host: str) -> tuple[str, ...]:
        """执行单次 getaddrinfo 与公网校验，不做缓存与在途去重。

        getaddrinfo 由事件循环卸载到线程执行器，无内置超时；以 wait_for 施加上限，超时
        抛 asyncio.TimeoutError 交由 download_image 重试。wait_for 超时仅取消等待协程，
        底层 getaddrinfo 工作线程无法被中断，会继续运行至系统解析完成后丢弃结果；该线程
        一次性且其结果已被丢弃。经 _resolve_public_ips 的在途去重，同一 host 至多一个在途
        登记的解析；超时重试期间被放弃的旧线程可能与新线程短暂并存，但在途数受 max_retries
        与 max_concurrent 下载并发上限约束，故不额外施加并发信号量。asyncio.TimeoutError
        是内建 TimeoutError（OSError 子类）的别名，须在调用方先于
        except OSError 捕获以保持可重试语义。gaierror 按错误码分类：域名不存在类错误为
        终态 DownloadError，EAI_AGAIN 等瞬时解析故障为可重试错误，同样交由重试。
        """
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            raise
        except socket.gaierror as exc:
            # gaierror 是 OSError 子类，须先于 OSError 捕获；errno 命中永久失败集合才
            # 保持终态，瞬时故障与不可靠 errno 一律按可重试上抛。
            if exc.errno is not None and exc.errno in _TERMINAL_GAI_ERRNOS:
                raise DownloadError(f"域名解析失败: {host} ({exc})") from exc
            raise RetryableDownloadError(f"域名解析失败: {host} ({exc})") from exc
        except OSError as exc:
            raise DownloadError(f"域名解析失败: {host} ({exc})") from exc

        if not infos:
            raise DownloadError(f"域名解析结果为空: {host}")

        resolved_ips: set[str] = set()
        for info in infos:
            resolved_ip = info[4][0]
            try:
                ip_obj = ipaddress.ip_address(resolved_ip)
            except ValueError as exc:
                raise DownloadError(f"域名解析返回非法IP: {host} -> {resolved_ip}") from exc
            reason = _public_ip_rejection_reason(ip_obj)
            if reason:
                raise DownloadError(f"域名解析到不安全地址({reason}): {host} -> {resolved_ip}")
            resolved_ips.add(resolved_ip)

        if not resolved_ips:
            raise DownloadError(f"域名解析结果为空: {host}")

        return tuple(sorted(resolved_ips))

    def _enforce_dns_cache_limit(self) -> None:
        """强制 DNS 缓存条目数不超过上限，防止长生命周期下多 host 缓存无界增长。

        先清理已过期条目；若仍超限则按最旧 expires_at 强制驱逐至阈值内，使上限成为
        硬限制而非软限制。调用方须已持有 ``_dns_cache_lock``。
        """
        now = time.time()
        for expired_host in [h for h, (exp, _) in self._dns_cache.items() if exp <= now]:
            self._dns_cache.pop(expired_host, None)
        while len(self._dns_cache) > _DNS_CACHE_MAX_SIZE:
            oldest_host = min(self._dns_cache, key=lambda h: self._dns_cache[h][0])
            self._dns_cache.pop(oldest_host, None)

    async def _validate_public_dns(self, host: str) -> None:
        """异步解析域名并确保解析结果全部为公网 IP。"""
        await self._resolve_public_ips(host)

    async def _validate_url_for_request(self, url: str) -> None:
        """执行请求前 URL 安全校验，串联第一层静态校验与第二层 DNS 校验。"""
        host, needs_dns_check = self._validate_url_static(url)
        if needs_dns_check:
            await self._validate_public_dns(host)

    @staticmethod
    def _extract_peer_ip(response: aiohttp.ClientResponse) -> str | None:
        """从底层传输连接中提取实际对端 IP，供连接后复核使用。"""
        connection = response.connection
        if connection is None or connection.transport is None:
            return None

        peername = connection.transport.get_extra_info("peername")
        if not peername:
            return None

        if isinstance(peername, tuple) and peername:
            return str(peername[0])
        return None

    @staticmethod
    def _ensure_public_peer_ip(peer_ip: str, source_url: str) -> None:
        """校验连接后的对端 IP 是否为公网地址，复用统一的公网判定逻辑。

        Args:
            peer_ip: 连接建立后的对端 IP 字符串。
            source_url: 来源 URL，仅经脱敏后进入错误信息。

        Raises:
            DownloadError: 对端 IP 无法解析为合法地址，或命中不可作为公网下载目标
                的地址。
        """
        try:
            ip_obj = ipaddress.ip_address(peer_ip)
        except ValueError as exc:
            raise DownloadError(f"连接返回非法IP地址: {peer_ip}") from exc

        reason = _public_ip_rejection_reason(ip_obj)
        if reason:
            raise DownloadError(
                f"连接命中不安全地址({reason}): {peer_ip} ({sanitize_url(source_url)})"
            )

    def _validate_connected_peer_ip(
        self, response: aiohttp.ClientResponse, source_url: str
    ) -> None:
        """连接建立后再次校验对端 IP，属 SSRF 第三层防护。

        第二层 resolve-and-pin 已把连接目标钉死为校验通过的公网 IP，本层作为纵深
        防御：即便存在解析器与连接之间的残余窗口使对端 IP 落入内网，仍据此拒绝。
        无法提取对端 IP 时 fail-closed 拒绝下载，避免因校验信息缺失而放行潜在的内网连接。

        Args:
            response: 已建立连接的响应对象。
            source_url: 来源 URL，仅经脱敏后进入错误信息。

        Raises:
            DownloadError: 无法提取对端 IP，或对端 IP 命中不可作为公网下载目标的地址。
        """
        peer_ip = self._extract_peer_ip(response)
        if not peer_ip:
            raise DownloadError(f"无法获取连接对端IP，拒绝下载: {sanitize_url(source_url)}")

        self._ensure_public_peer_ip(peer_ip, source_url)

    def _handle_redirect_response(
        self,
        response: "aiohttp.ClientResponse",
        current_url: str,
        redirect_count: int,
    ) -> str | None:
        """处理重定向响应，返回下一跳绝对 URL；非重定向返回 None。

        重定向目标回到调用方循环顶部由 _validate_url_for_request 执行完整静态与
        DNS 校验，本方法仅判定跳数上限与 Location 解析。缺 Location 或超 _MAX_REDIRECTS
        抛出终态错误。

        Args:
            response: 当前跳的响应对象。
            current_url: 发起当前请求的绝对 URL，用于解析相对 Location。
            redirect_count: 已完成的重定向跳数。

        Returns:
            下一跳绝对 URL；当前响应非重定向时返回 None。

        Raises:
            DownloadError: 重定向响应缺少 Location 头、超过跳数上限或目标协议不允许
                降级。
        """
        if response.status not in {301, 302, 303, 307, 308}:
            return None
        location = response.headers.get("location")
        if not location:
            raise DownloadError("重定向响应缺少 Location 头")
        if redirect_count >= _MAX_REDIRECTS:
            raise DownloadError("重定向次数过多")
        next_url = urljoin(current_url, location)
        # 拒绝协议降级：https 起始的下载不允许经重定向落到 http，消除降级到明文链路
        # 的攻击面，与逐跳完整校验共同收紧重定向信任边界。scheme 先归一化小写再
        # 比较，与 _url_origin 和 _validate_url_static 的既有口径一致。
        current_scheme = (urlparse(current_url).scheme or "").lower()
        next_scheme = (urlparse(next_url).scheme or "").lower()
        if current_scheme == "https" and next_scheme != "https":
            raise DownloadError("重定向目标协议不允许降级到 https 之外")
        return next_url

    async def _download_response_to_temp(
        self,
        response: "aiohttp.ClientResponse",
        save_path: Path,
        temp_suffix: str,
        content_type: str,
        attempt: int,
        start_time: float,
    ) -> dict[str, Any]:
        """将 200 响应体下载到临时文件，校验大小与字节签名后原子替换，返回结果字典。

        落盘协议由 io_file.atomic_replace_from_fd 统一提供，与 io_storage.save_bytes
        复用同一骨架：随机名临时文件规避符号链接 TOCTOU，写入后 os.replace 原子替换，失败
        清理临时文件。writer 以 closefd=False 包装 fd，骨架独占 fd 关闭。content-length
        预检、流式写入累计上限、首字节签名校验三道关卡任一失败均抛出 DownloadError，由
        调用方按终态或可重试分类处理。

        扩展名以实际字节签名为准：签名校验通过后用 head_bytes 推断真实格式，与
        save_path 的 URL 派生扩展名不一致时经 writer 返回值把最终路径修正为嗅探结果，
        与 base64 保存路径的 infer_extension_from_bytes 行为对齐，避免无后缀的签名
        URL 恒落 .jpeg 或扩展名与内容不符。
        """
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                cl_value = int(content_length)
            except (TypeError, ValueError) as exc:
                raise DownloadError(f"非法 content-length: {content_length!r}") from exc
            if cl_value < 0:
                raise DownloadError(f"非法 content-length: {cl_value}")
            if cl_value > self.max_file_size:
                raise DownloadError(f"文件过大: {cl_value} 字节")

        # 目录创建卸载到线程池；exist_ok=True 下重复创建无副作用，保留作防御性兜底。
        await asyncio.to_thread(save_path.parent.mkdir, parents=True, exist_ok=True)

        total_size = 0
        head_bytes = b""
        final_save_path = save_path

        async def _writer(fd: int) -> Path | None:
            nonlocal total_size, head_bytes, final_save_path
            # closefd=False：aiofiles 退出时仅 flush 不关闭 fd，由骨架统一关闭，避免双重关闭。
            async with aiofiles.open(fd, "wb", closefd=False) as f:
                # 批量累积写入：aiofiles 每次 write 提交一次执行器任务，逐 256KB chunk
                # 跳转在批量下载时占用默认执行器线程槽，累积至 _WRITE_BATCH_BYTES 再落盘。
                write_buffer = bytearray()
                async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
                    total_size += len(chunk)
                    if total_size > self.max_file_size:
                        raise DownloadError(f"文件过大: {total_size} 字节")
                    # 累计首部字节做签名校验，省一次 open+read；窗口取各格式最小
                    # 长度下界的最大值，保证 is_known_image_bytes 对全部格式可判定。
                    if len(head_bytes) < SNIFF_HEAD_BYTES_FLOOR:
                        head_bytes += chunk[: SNIFF_HEAD_BYTES_FLOOR - len(head_bytes)]
                    write_buffer += chunk
                    if len(write_buffer) >= _WRITE_BATCH_BYTES:
                        await f.write(write_buffer)
                        write_buffer.clear()
                if write_buffer:
                    await f.write(write_buffer)
            # 字节层校验：防止 Content-Type 伪造使非图片或可执行内容落盘。
            if not is_known_image_bytes(head_bytes):
                raise DownloadError("下载内容字节签名非受支持图片格式，疑似 Content-Type 伪造")
            # 签名已确认受支持，推断必然命中具体格式；与 URL 派生扩展名不一致时修正最终路径。
            sniffed = infer_extension_from_bytes(head_bytes)
            if sniffed != save_path.suffix.lower():
                final_save_path = save_path.with_suffix(sniffed)
                return final_save_path
            return None

        # temp_suffix 仅用于随机临时文件命名的可读性后缀，实际路径由骨架内 mkstemp 随机生成。
        await atomic_replace_from_fd(save_path, _writer, suffix=temp_suffix)

        download_time = time.time() - start_time
        logger.info(
            "图片下载成功: {} ({} 字节, {:.2f}秒)",
            final_save_path,
            total_size,
            download_time,
        )
        return {
            "success": True,
            "file_path": str(final_save_path),
            "file_size": total_size,
            "download_time": download_time,
            "content_type": content_type,
            "attempts": attempt + 1,
        }

    async def _attempt_download(
        self,
        session: "aiohttp.ClientSession",
        url: str,
        headers: dict[str, str],
        save_path: Path,
        temp_suffix: str,
        attempt: int,
        start_time: float,
    ) -> dict[str, Any]:
        """执行单次下载尝试，含逐跳重定向循环，返回成功落盘结果字典。

        SSRF 第四层防护：禁用自动重定向改为逐跳手动处理，每跳重新走静态、DNS 与连接
        对端 IP 完整校验，防止经由重定向绕过跳转到内网地址。依赖网络状态的 DNS 校验
        在本方法内执行，保障外层 download_image 的 max_retries 对网络故障生效。
        resolve-and-pin：会话连接器绑定 _PublicIpPinningResolver，校验通过的公网 IP 被
        钉死为连接目标，aiohttp 不再独立二次解析，DNS rebinding 无从把实际连接切向内网；
        _validate_connected_peer_ip 作为纵深防御保留。

        重定向链逐跳累计校验总预算：会话 total 超时按单次请求计窗，每跳各享全额窗口，
        跟随下一跳前按起始时间累计校验，超限抛 asyncio.TimeoutError 交由外层按超时
        分类重试，恶意慢滴流重定向链不得把单次尝试拖至跳数倍封顶。

        请求头跨源防护：调用方为原始主机定制的请求头在重定向跳向不同源时剥离，仅保留
        User-Agent 与 Accept 等通用头，防止定制头原样发给重定向目标泄露内部信息。

        HTTP 5xx 抛 RetryableDownloadError 由外层纳入退避重试；4xx、文件过大、字节签名、
        重定向等语义明确的终态错误抛 DownloadError 由外层原样上抛不重试。
        """
        current_url = url
        current_headers = headers
        redirect_count = 0
        while True:
            await self._validate_url_for_request(current_url)
            async with session.get(
                current_url,
                headers=current_headers,
                allow_redirects=False,
            ) as response:
                self._validate_connected_peer_ip(response, current_url)

                next_url = self._handle_redirect_response(response, current_url, redirect_count)
                if next_url is not None:
                    # 跟随下一跳前按起始时间累计校验总预算：每跳的会话 total 超时独立
                    # 计窗，无累计校验时慢滴流重定向链可把单次尝试拖至跳数倍封顶。
                    elapsed = time.time() - start_time
                    if elapsed >= self._download_total_budget():
                        raise asyncio.TimeoutError(
                            f"重定向链累计耗时 {elapsed:.0f} 秒已超总预算 "
                            f"{self._download_total_budget():.0f} 秒: {sanitize_url(url)}"
                        )
                    # 跳向不同源时从调用方原始头重建安全头集合；同源跳转保持当前头不变，
                    # 一旦剥离后续跳回原源也不恢复定制头。
                    if _url_origin(next_url) != _url_origin(current_url):
                        current_headers = _strip_custom_headers_for_cross_origin(headers)
                    redirect_count += 1
                    current_url = next_url
                    continue

                if response.status != 200:
                    if 500 <= response.status < 600:
                        # 5xx 多为 CDN/网关瞬时故障，纳入重试而非终态失败。
                        raise RetryableDownloadError(f"HTTP错误: {response.status}")
                    raise DownloadError(f"HTTP错误: {response.status}")

                # 检查内容类型：HTML 错误页、JSON 等明确非图片类型直接拒绝。
                content_type = response.headers.get("content-type", "")
                if not _is_image_compatible_content_type(content_type):
                    raise DownloadError(f"响应内容类型非图片: {content_type.split(';')[0].strip()}")

                return await self._download_response_to_temp(
                    response,
                    save_path,
                    temp_suffix,
                    content_type,
                    attempt,
                    start_time,
                )

    async def download_image(
        self, url: str, save_path: Path, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """异步下载图片。

        Args:
            url: 图片 URL。
            save_path: 保存路径，最终扩展名以字节签名嗅探结果为准。
            headers: 请求头；None 时使用内置默认头。

        Returns:
            下载结果字典，含 file_path、file_size、download_time、content_type 与
            attempts。

        Raises:
            DownloadError: URL 校验、内容校验等终态失败，或重试耗尽仍失败；
                文件系统永久错误与无效 URL 同样以终态方式抛出。非下载语义的
                意外异常按原类型上抛，不包装为 DownloadError。
        """
        if headers is None:
            headers = {"User-Agent": f"Seedream-MCP/{__version__}", "Accept": "image/*"}

        start_time = time.time()
        last_error: DownloadError | None = None
        # 临时文件的可读性后缀：原扩展名后追加 .part，实际路径由原子落盘骨架内 mkstemp 随机生成。
        temp_suffix = (save_path.suffix or ".bin") + ".part"

        for attempt in range(self.max_retries + 1):
            try:
                logger.info(
                    "开始下载图片 (尝试 {}/{}): {}",
                    attempt + 1,
                    self.max_retries + 1,
                    sanitize_url(url),
                )

                session = await self._ensure_session()
                return await self._attempt_download(
                    session, url, headers, save_path, temp_suffix, attempt, start_time
                )

            except RetryableDownloadError as e:
                # HTTP 5xx、DNS 瞬时解析失败等瞬时故障，记录后落入退避重试。
                last_error = e
                logger.warning("可重试的下载错误 (尝试 {}): {}", attempt + 1, e)

            except DownloadError:
                # 4xx、文件过大、字节签名、重定向等语义明确的终态错误，原样抛出不重试。
                raise

            except asyncio.TimeoutError as e:
                last_error = DownloadError(f"下载超时: {e}")
                logger.warning("下载超时 (尝试 {}): {}", attempt + 1, sanitize_url(url))

            except aiohttp.ClientError as e:
                # InvalidUrlClientError 是 URL 语法层面的永久错误，重试无意义，直接终态抛出；
                # 其余 ClientError 多为连接类瞬时故障，纳入退避重试。
                if isinstance(e, aiohttp.InvalidUrlClientError):
                    raise DownloadError(
                        f"无效的URL: {sanitize_url(url)} [{type(e).__name__}]"
                    ) from e
                last_error = DownloadError(f"网络错误: {sanitize_url(url)} [{type(e).__name__}]")
                logger.warning(
                    "网络错误 (尝试 {}): {} [{}]",
                    attempt + 1,
                    sanitize_url(url),
                    type(e).__name__,
                    exc_info=True,
                )

            except PermissionError as e:
                # 权限拒绝属永久性错误，重试仅徒增延迟，直接终态抛出。
                raise DownloadError(f"权限拒绝，不可重试: {e}") from e

            except OSError as e:
                # 只读文件系统、磁盘满与配额超限等永久性错误不重试；其余瞬时 OSError 保持重试。
                if e.errno in {errno.EROFS, errno.ENOSPC, errno.EDQUOT}:
                    raise DownloadError(f"文件系统永久错误，不可重试: {e}") from e
                last_error = DownloadError(f"文件系统错误: {e}")
                logger.warning("文件系统错误 (尝试 {}): {}", attempt + 1, e, exc_info=True)

            except Exception as e:
                # 编程 bug、序列化失败、值错误等非可重试意外错误直接抛出，不浪费退避等待。
                # 上方分支已精确覆盖可重试场景：HTTP 5xx、超时、网络/传输、可重试文件系统错误。
                # 调用方 auto_save 仍有兜底 except Exception 负责降级返回原始 URL。
                logger.warning(
                    "下载出现非预期错误，不再重试 (尝试 {}): {}",
                    attempt + 1,
                    e,
                    exc_info=True,
                )
                raise

            # 非末次尝试则按线性递增延迟加随机抖动退避后重试，抖动避免并发任务重试同步。
            if attempt < self.max_retries:
                # 跨尝试累计时长预算从 start_time 起算，耗尽即停止重试：慢滴流响应
                # 单次尝试可耗满会话总时长上限，无累计预算时重试按尝试数成倍放大
                # 单个保存任务的占用；预算与单次封顶取同一值，见 _download_total_budget。
                elapsed = time.time() - start_time
                if elapsed >= self._download_total_budget():
                    logger.warning(
                        "下载累计耗时 {:.0f} 秒已超总预算 {:.0f} 秒，停止重试: {}",
                        elapsed,
                        self._download_total_budget(),
                        sanitize_url(url),
                    )
                    break
                await asyncio.sleep(self.retry_delay * (attempt + 1) + random.uniform(0, 1))

        logger.error("图片下载失败，已重试 {} 次: {}", self.max_retries, sanitize_url(url))
        raise last_error or DownloadError("下载失败")

    def validate_url(self, url: str) -> bool:
        """验证 URL 静态格式与主机安全性，不执行 DNS 解析。

        Args:
            url: 要验证的 URL。

        Returns:
            静态校验通过返回 True，否则返回 False。
        """
        try:
            self._validate_url_static(url)
            return True
        except DownloadError:
            return False
