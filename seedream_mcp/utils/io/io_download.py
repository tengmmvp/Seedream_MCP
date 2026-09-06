"""异步图片下载管理器，提供带 SSRF 多层防护的安全下载能力。

核心安全设计为四层 SSRF 防护，纵深防御逐层收紧：

1. 静态 URL 校验：解析阶段即拒绝非 http/https 协议、凭据、本地主机名与非公网 IP
   字面量。
2. DNS 公网解析与连接钉死：域名解析结果须全部为公网 IP，会话连接器绑定
   ``_PublicIpPinningResolver`` 把连接目标钉死为校验通过的 IP，闭合 DNS rebinding 窗口。
3. 连接后对端 IP 复核：连接建立后再次校验对端 IP，作为钉死之上的纵深防御。
4. 逐跳重定向校验：禁用自动重定向，每跳重新走完整校验；跳向不同源时剥离调用方
   定制请求头。

其余关键设计：失败按递增延迟加随机抖动重试；先写不可预测随机名临时文件再
``os.replace`` 原子替换；响应须经 Content-Type 与字节签名双重校验后方落盘。
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import random
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
from ..core.inflight import InflightEntry
from ..core.logs import get_logger
from .io_file import atomic_replace_from_fd
from .io_url import sanitize_url

logger = get_logger()

# 流式下载块大小：较大块减少多 MB 图片的 await write 循环次数。
_DOWNLOAD_CHUNK_SIZE = 256 * 1024

# 落盘批量写阈值：累积至该字节数才提交一次 aiofiles 写任务，减少执行器跳转次数。
_WRITE_BATCH_BYTES = 4 * 1024 * 1024

# 重定向上限：逐跳手动跟踪并限制跳数，防止经由重定向链绕过 SSRF 校验。
_MAX_REDIRECTS = 3

# 非终态响应体的排空读取上限：连接须消费完响应体方可回池复用，重定向与 HTTP 错误
# 的响应体通常很小，64KB 足以覆盖；残留更多时放弃复用，由连接关闭兜底。
_DRAIN_RESPONSE_BYTES = 65536

# 跨源重定向时保留的通用请求头：跳向不同源时鉴权或跟踪一类定制头被剥离，不原样
# 发给重定向目标。
_CROSS_ORIGIN_SAFE_REQUEST_HEADERS = frozenset({"user-agent", "accept"})

# 同一图片格式的等价扩展名类：.jpg 与 .jpeg、.heif 与 .heic 互为别名后缀。字节签名
# 嗅探结果与 URL 派生扩展名同属一个等价类时视为同格式，不改写落盘文件名。
_FORMAT_EQUIVALENT_EXTENSIONS: tuple[frozenset[str], ...] = (
    frozenset({".jpg", ".jpeg"}),
    frozenset({".heif", ".heic"}),
)


def _extensions_in_same_format(first: str, second: str) -> bool:
    """判断两个扩展名是否属于同一格式等价类，比较前归一化小写。"""
    normalized_first = first.lower()
    normalized_second = second.lower()
    for equivalence in _FORMAT_EQUIVALENT_EXTENSIONS:
        if normalized_first in equivalence and normalized_second in equivalence:
            return True
    return False


# DNS 缓存条目硬上限：超限时先清理过期条目，仍超限则按最旧 expires_at 强制驱逐。
_DNS_CACHE_MAX_SIZE = 256

# Windows getaddrinfo 失败抛 gaierror 时 errno 携带 winsock2.h 的 WSA 错误码而非
# POSIX EAI_* 常量，须以字面值并入终态集合：11001 主机不存在对应 EAI_NONAME，
# 11004 无该类型 DNS 记录对应 EAI_NODATA，11003 不可恢复故障对应 EAI_FAIL；11002
# 为瞬时故障保持可重试。POSIX 的 EAI_* 码为负值或个位小值，与正数 WSA 码经数值
# 并集统一且无歧义。
_WSA_HOST_NOT_FOUND = 11001
_WSA_NO_DATA = 11004
_WSA_NO_RECOVERY = 11003

# getaddrinfo 的 gaierror 中属永久失败的错误码集合：域名不存在、参数不受支持或不可
# 恢复的解析器故障，重试无法恢复；其余错误码含 EAI_AGAIN 等瞬时故障，一律可重试，
# 重试次数上限兜底。平台缺少对应常量时经 getattr 剔除。
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


def _url_origin(url: str) -> tuple[str, str, int]:
    """返回 URL 的请求源三元组 (scheme, host, effective_port)，供跨源判定。

    未显式给出端口时按 scheme 取默认端口，使 ``https://a.example`` 与
    ``https://a.example:443`` 判定为同源。端口字段非法时按默认端口处理，该 URL
    的请求本身会因语法问题被下游拒绝，此处不提前报错。

    Raises:
        DownloadError: URL 含 urlparse 无法解析的畸形语法，如不闭合的 IPv6 括号。
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        # urlparse 对不闭合 IPv6 括号等畸形输入抛 ValueError，归一为终态无效 URL 错误。
        raise DownloadError(f"无效的URL: {sanitize_url(url)}") from exc
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
_SITE_LOCAL_NETWORK = ipaddress.ip_network("fec0::/10")


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

    统一静态 URL、DNS 解析与连接对端 IP 三处校验：拒绝非公网地址、RFC 6598 CGNAT
    段及 6to4/Teredo 等可封装内网地址的 IPv6 段，IPv6 内嵌 IPv4 递归校验。
    组播与 IPv6 site-local 须显式拒绝：Python 的 is_global 对组播段（IPv4 224/4、
    IPv6 ff00::/8）与已废弃的 fec0::/10 返回 True，依赖隐式语义会随 Python 版本
    漂移，显式分支同时消除该不确定性。
    """
    if ip_obj.version == 4 and ip_obj in _CGNAT_NETWORK:
        return "CGNAT地址(100.64.0.0/10)"
    if ip_obj.is_multicast:
        return "组播地址"
    if ip_obj.version == 6 and ip_obj in _SITE_LOCAL_NETWORK:
        return "IPv6 site-local地址(fec0::/10)"
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

    重试循环中须先于 DownloadError 单独捕获，作为可重试故障而非终态错误处理。
    """

    pass


class _PublicIpPinningResolver(AbstractResolver):
    """自定义 DNS 解析器，把连接目标钉死为经 SSRF 公网校验的 IP，防 DNS rebinding。

    接管 aiohttp 的连接前解析，域名结果一律经 ``_resolve_public_ips`` 公网校验，
    只能连接到校验通过的公网 IP。返回结果保留 URL 原始主机名作为 ``hostname``，
    TLS 的 SNI 与证书校验目标不变，证书校验不被削弱；IP 字面量由连接器直接短路。
    """

    def __init__(self, manager: "DownloadManager") -> None:
        self._manager = manager

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        """解析主机名为经公网校验的 IP 列表，连接目标随之钉死为校验结果。

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
    """异步图片下载管理器，内置 SSRF 四层防护与递增退避重试。"""

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
            connection_limit: 底层连接器的并发连接上限，None 时沿用 aiohttp 默认；
                经构造参数传入使会话重建时连接器自动保持同一上限。
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_file_size = max_file_size
        self._connection_limit = connection_limit
        self._dns_cache_ttl = max(1, dns_cache_ttl)
        # DNS 缓存条目的 expires_at 以 time.monotonic 为基准，写入与过期比较须同基准。
        self._dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
        self._dns_inflight: dict[str, InflightEntry[tuple[str, ...]]] = {}
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

        上限按停滞超时的 120 倍推导且不低于 1 小时，默认配置下为 1 小时，只封堵
        恶意慢速滴流，不影响正常慢网络下大图片的完整下载。三处取同一值：无累计
        校验时，重试与重定向跳数会把单个保存任务的最长占用放大为单次封顶乘尝试
        数；统一后总占用被约束在单次封顶的小常数倍内。
        """
        return max(float(self.timeout) * 120, 3600.0)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """获取可用的 aiohttp 会话，无会话或已关闭时创建并返回。"""
        # 双检锁串行化创建，避免并发请求各自创建会话导致泄漏。
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    # 连接器绑定 _PublicIpPinningResolver，连接目标经 SSRF 公网校验后
                    # 钉死；connection_limit 在每次构造连接器时传入保持同一上限。
                    connector_kwargs: dict[str, Any] = {"resolver": _PublicIpPinningResolver(self)}
                    if self._connection_limit is not None:
                        connector_kwargs["limit"] = self._connection_limit
                    connector = aiohttp.TCPConnector(**connector_kwargs)
                    # sock_connect/sock_read 仅约束连接建立与单次读取停滞，慢滴流响应
                    # 可无限拖住任务，故另以宽松总时长上限封顶整个下载。
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
            DownloadError: 协议不受支持、主机名缺失、携带凭据、本地主机名、非公网
                IP 字面量，或 URL 含 urlparse 无法解析的畸形语法。
        """
        try:
            result = urlparse(url)
        except ValueError as exc:
            # urlparse 对不闭合 IPv6 括号等畸形输入抛 ValueError，归一为终态无效
            # URL 错误，validate_url 的 bool 契约不被异常逃逸破坏。
            raise DownloadError(f"无效的URL: {sanitize_url(url)}") from exc
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

        防 DNS rebinding：攻击者可在静态校验后把 DNS 切换到内网地址。结果带 TTL
        缓存；同 host 并发下载经 InflightEntry 在途去重共享一次解析，取消传播隔离
        与孤儿异常兜底由其统一承担。

        Args:
            host: 待解析并校验的主机名。

        Returns:
            该主机名全部校验通过的公网 IP 元组。

        Raises:
            DownloadError: 解析结果为空、含非法 IP 或命中非公网地址等终态失败。
            RetryableDownloadError: DNS 瞬时解析故障等可重试失败。
        """
        now = time.monotonic()
        async with self._dns_cache_lock:
            cached = self._dns_cache.get(host)
            if cached is not None:
                expires_at, cached_ips = cached
                if expires_at > now:
                    return cached_ips
                self._dns_cache.pop(host, None)
            entry = self._dns_inflight.get(host)
            if entry is None:
                # 缓存与在途登记在同一锁区间内完成且中间无 await，不会交错创建重复 task。
                entry = InflightEntry(asyncio.ensure_future(self._resolve_and_cache(host)))
                self._dns_inflight[host] = entry
        # 锁外以消费者身份等待共享 task，取消与孤儿异常兜底由 InflightEntry 统一处理。
        return await entry.consume()

    async def _resolve_and_cache(self, host: str) -> tuple[str, ...]:
        """执行单次解析与公网校验并写入缓存，供 _resolve_public_ips 在途去重。

        finally 清理在途 task，使后续缓存过期或失败后的请求可重新发起解析。
        """
        try:
            ip_tuple = await self._resolve_public_ips_uncached(host)
            async with self._dns_cache_lock:
                self._dns_cache[host] = (time.monotonic() + self._dns_cache_ttl, ip_tuple)
                if len(self._dns_cache) > _DNS_CACHE_MAX_SIZE:
                    self._enforce_dns_cache_limit()
            return ip_tuple
        finally:
            async with self._dns_cache_lock:
                self._dns_inflight.pop(host, None)

    async def _resolve_public_ips_uncached(self, host: str) -> tuple[str, ...]:
        """执行单次 getaddrinfo 解析与公网校验，不做缓存与在途去重。

        Raises:
            DownloadError: 解析结果为空、含非法 IP、命中非公网地址，或永久性解析
                失败。
            RetryableDownloadError: DNS 瞬时解析故障等可重试失败。
        """
        loop = asyncio.get_running_loop()
        try:
            # getaddrinfo 卸载到线程执行器且无内置超时，以 wait_for 施加上限；wait_for
            # 仅取消等待协程，底层线程继续运行但结果已丢弃，在途数受 max_retries 与
            # 下载并发上限约束。
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            # asyncio.TimeoutError 是内建 TimeoutError（OSError 子类）的别名，须先于
            # except OSError 捕获；超时交由 download_image 重试。
            raise
        except socket.gaierror as exc:
            # gaierror 是 OSError 子类，同样先于 OSError 捕获；errno 命中永久失败集合
            # 才保持终态，瞬时故障与不可靠 errno 一律按可重试上抛。
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
        now = time.monotonic()
        for expired_host in [h for h, (exp, _) in self._dns_cache.items() if exp <= now]:
            self._dns_cache.pop(expired_host, None)
        while len(self._dns_cache) > _DNS_CACHE_MAX_SIZE:
            oldest_host = min(self._dns_cache, key=lambda h: self._dns_cache[h][0])
            self._dns_cache.pop(oldest_host, None)

    async def _validate_url_for_request(self, url: str) -> None:
        """执行请求前 URL 安全校验，串联第一层静态校验与第二层 DNS 校验。"""
        host, needs_dns_check = self._validate_url_static(url)
        if needs_dns_check:
            await self._resolve_public_ips(host)

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

        作为公网解析钉死之上的纵深防御：解析器与连接之间的残余窗口使对端
        IP 落入内网时仍据此拒绝；无法提取对端 IP 时 fail-closed 拒绝下载。

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
            DownloadError: 重定向响应缺少 Location 头、超过跳数上限、目标协议不允许
                降级，或 Location 目标含 urlparse 无法解析的畸形语法。
        """
        if response.status not in {301, 302, 303, 307, 308}:
            return None
        location = response.headers.get("location")
        if not location:
            raise DownloadError("重定向响应缺少 Location 头")
        if redirect_count >= _MAX_REDIRECTS:
            raise DownloadError("重定向次数过多")
        try:
            next_url = urljoin(current_url, location)
            # 拒绝协议降级：https 起始的下载不允许经重定向落到明文 http；scheme 归一化
            # 小写后比较，与 _url_origin 等既有口径一致。
            current_scheme = (urlparse(current_url).scheme or "").lower()
            next_scheme = (urlparse(next_url).scheme or "").lower()
        except ValueError as exc:
            # urljoin/urlparse 对不闭合 IPv6 括号等畸形目标抛 ValueError，归一为终态
            # 无效 URL 错误。
            raise DownloadError(f"无效的URL: {sanitize_url(location)}") from exc
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
        wall_start_time: float,
        fsync: bool = False,
    ) -> dict[str, Any]:
        """将 200 响应体流式下载到临时文件，经校验后原子替换，返回结果字典。

        扩展名以实际字节签名为准：与 save_path 的 URL 派生扩展名不一致且不属同一
        格式等价类时，落盘路径修正为嗅探扩展名。

        Args:
            response: 已确认状态 200 的响应对象。
            save_path: 目标保存路径。
            temp_suffix: 临时文件命名的可读性后缀，实际路径由落盘骨架随机生成。
            content_type: 响应内容类型，原样进入结果字典。
            attempt: 当前尝试序号，结果字典中 attempts 记为其加一。
            wall_start_time: 挂钟基准起始时间。
            fsync: 原子替换前是否对临时文件执行 os.fsync。

        Returns:
            含 success/file_path/file_size/download_time/content_type/attempts 的
            结果字典。

        Raises:
            DownloadError: content-length 预检、流式累计上限或字节签名校验失败。
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
            sniffed = infer_extension_from_bytes(head_bytes)
            if sniffed != save_path.suffix.lower() and not _extensions_in_same_format(
                sniffed, save_path.suffix
            ):
                final_save_path = save_path.with_suffix(sniffed)
                return final_save_path
            return None

        # 落盘协议由 io_file.atomic_replace_from_fd 统一提供，与 io_storage.save_bytes
        # 复用同一骨架。
        await atomic_replace_from_fd(save_path, _writer, suffix=temp_suffix, fsync=fsync)

        download_time = time.time() - wall_start_time
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
        wall_start_time: float,
        fsync: bool = False,
    ) -> dict[str, Any]:
        """执行单次下载尝试，含逐跳重定向循环，返回成功落盘结果字典。

        SSRF 第四层防护：禁用自动重定向，每跳重新走静态、DNS 与连接对端 IP 完整
        校验；会话连接器已把校验通过的公网 IP 钉死为连接目标，DNS rebinding 无从
        把实际连接切向内网。重定向链逐跳累计校验总预算：会话 total 超时按单次请求
        计窗，跟随下一跳前按起始时间累计校验，慢滴流重定向链不得把单次尝试拖至
        跳数倍封顶。跳向不同源时剥离调用方定制请求头，仅保留通用头。HTTP 5xx 抛
        RetryableDownloadError 由外层纳入退避重试，其余语义明确的终态错误原样上抛
        不重试。
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
                    # 每跳的会话 total 超时独立计窗，跟随前按单调钟累计校验总预算。
                    elapsed = time.monotonic() - start_time
                    if elapsed >= self._download_total_budget():
                        raise asyncio.TimeoutError(
                            f"重定向链累计耗时 {elapsed:.0f} 秒已超总预算 "
                            f"{self._download_total_budget():.0f} 秒: {sanitize_url(url)}"
                        )
                    # 跳向不同源时从调用方原始头重建安全头集合；同源跳转保持当前头不变，
                    # 一旦剥离后续跳回原源也不恢复定制头。
                    if _url_origin(next_url) != _url_origin(current_url):
                        current_headers = _strip_custom_headers_for_cross_origin(headers)
                    # 排空本跳残留响应体，连接方可回池被下一跳复用，免于逐跳重建 TCP+TLS。
                    await response.content.read(_DRAIN_RESPONSE_BYTES)
                    redirect_count += 1
                    current_url = next_url
                    continue

                if response.status != 200:
                    # 排空错误响应体使连接回池，重试与后续下载复用连接，免于重建 TCP+TLS。
                    await response.content.read(_DRAIN_RESPONSE_BYTES)
                    # 5xx 与 408/429 多为瞬时故障或限流，纳入重试避免批量下载触发限流
                    # 后自动保存静默丢弃本地副本。
                    if response.status in (408, 429) or 500 <= response.status < 600:
                        raise RetryableDownloadError(f"HTTP错误: {response.status}")
                    raise DownloadError(f"HTTP错误: {response.status}")

                content_type = response.headers.get("content-type", "")
                if not _is_image_compatible_content_type(content_type):
                    raise DownloadError(f"响应内容类型非图片: {content_type.split(';')[0].strip()}")

                return await self._download_response_to_temp(
                    response,
                    save_path,
                    temp_suffix,
                    content_type,
                    attempt,
                    wall_start_time,
                    fsync=fsync,
                )

    async def download_image(
        self,
        url: str,
        save_path: Path,
        headers: dict[str, str] | None = None,
        fsync: bool = False,
    ) -> dict[str, Any]:
        """异步下载图片。

        Args:
            url: 图片 URL。
            save_path: 保存路径，最终扩展名以字节签名嗅探结果为准。
            headers: 请求头；None 时使用内置默认头。
            fsync: 写入后、原子替换前是否对临时文件执行 os.fsync 刷入稳定存储。

        Returns:
            下载结果字典，含 file_path、file_size、download_time、content_type 与
            attempts。

        Raises:
            DownloadError: URL 校验、内容校验等终态失败或重试耗尽；文件系统永久
                错误与无效 URL 同为终态。非下载语义的意外异常按原类型上抛。
        """
        if headers is None:
            headers = {"User-Agent": f"Seedream-MCP/{__version__}", "Accept": "image/*"}

        # 预算基准取单调钟：墙钟受系统对时回拨影响，会使累计预算判定失真；
        # download_time 的人读挂钟度量另取 wall_start_time。
        start_time = time.monotonic()
        wall_start_time = time.time()
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
                    session,
                    url,
                    headers,
                    save_path,
                    temp_suffix,
                    attempt,
                    start_time,
                    wall_start_time,
                    fsync=fsync,
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
                # 编程 bug 等非可重试意外错误直接抛出不浪费退避等待；调用方 auto_save
                # 仍有兜底 except Exception 负责降级返回原始 URL。
                logger.warning(
                    "下载出现非预期错误，不再重试 (尝试 {}): {}",
                    attempt + 1,
                    e,
                    exc_info=True,
                )
                raise

            # 非末次尝试则按线性递增延迟加随机抖动退避后重试，抖动避免并发任务重试同步。
            if attempt < self.max_retries:
                # 跨尝试累计预算耗尽即停止重试，防止重试按尝试数成倍放大单个保存
                # 任务的占用；预算取值见 _download_total_budget。
                elapsed = time.monotonic() - start_time
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
