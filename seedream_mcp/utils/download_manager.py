"""异步图片下载管理器，提供带 SSRF 多层防护的安全下载能力。

核心安全设计为四层 SSRF 防护，纵深防御逐层收紧：

1. 静态 URL 校验：由 ``_validate_url_static`` 实现，解析阶段即拒绝私网、保留地址及
   非公网 IP 字面量，把 ``file://``、``http://127.0.0.1`` 等直接伪造挡在网络层外。
2. DNS 公网解析：由 ``_resolve_public_ips`` 实现，解析主机名后校验所有结果均为公网 IP，
   防 DNS rebinding 在静态校验通过后才切换到内网地址。
3. 连接后对端 IP 复核：由 ``_validate_connected_peer_ip`` 实现，实际建立连接后再次校验
   对端 IP，闭合解析与连接之间的 TOCTOU 窗口。
4. 逐跳重定向校验：由 ``download_image`` 的重定向循环实现，禁用自动重定向，每跳目标都
   重新走完整校验，防止经由重定向绕过跳转到内网。

其余关键设计：失败按递增延迟加随机抖动重试；下载先写 ``.part`` 临时文件再
``os.replace`` 原子替换，避免半写文件对外可见；响应须经 Content-Type 与字节签名
双重校验后方落盘，防 Content-Type 伪造。
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import random
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiofiles
import aiohttp

from ..version import __version__
from .errors import SeedreamMCPError
from .formats import DEFAULT_MAX_FILE_SIZE, is_known_image_bytes
from .logging import get_logger
from .os_utils import open_no_follow_fd

logger = get_logger(__name__)

# 流式下载块大小：较大块减少多 MB 图片的 await write 循环次数
_DOWNLOAD_CHUNK_SIZE = 256 * 1024

# 重定向上限：逐跳手动跟踪并限制跳数，防止经由重定向链绕过 SSRF 校验
_MAX_REDIRECTS = 3

# DNS 缓存条目上限：长生命周期下持续解析不同 host 会导致缓存无界增长，
# 超过此阈值时清理已过期条目，仍存活的条目保留以维持命中率
_DNS_CACHE_MAX_SIZE = 256


def sanitize_url(url: str) -> str:
    """脱敏 URL 用于日志，保留 scheme/host/path，剥离凭据、查询参数与控制字符。

    控制字符 CRLF 等会被剥离，防止攻击者经由 URL 在日志中伪造行，注入误导性记录。
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
    # 剥离控制字符，防止 CRLF 经 URL 注入伪造日志行
    return re.sub(r"[\x00-\x1f\x7f]", "", result)


# 部分服务以通用二进制类型返回图片，故即使非 image/* 也视为合法二进制响应
_BINARY_CONTENT_TYPES = frozenset(
    {"application/octet-stream", "application/binary", "binary/octet-stream"}
)

# RFC 6598 运营商级 NAT 地址段。Python 较新版本 is_global 已排除此段，
# 此处前置显式判断以给出精确拒绝原因，并对 is_global 实现差异保持纵深防御
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
# 可封装内网 IPv4 的 IPv6 段：NAT64、IPv4-mapped、IPv4-compatible
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")
_IPV4_MAPPED_NETWORK = ipaddress.ip_network("::ffff:0:0/96")
_IPV4_COMPAT_NETWORK = ipaddress.ip_network("::/96")


def _embedded_ipv4_in_six(ip_obj: Any) -> ipaddress.IPv4Address | None:
    """提取 NAT64/IPv4-mapped/IPv4-compatible 段内嵌的 IPv4 地址，其他返回 None。"""
    for network in (_NAT64_NETWORK, _IPV4_MAPPED_NETWORK, _IPV4_COMPAT_NETWORK):
        if ip_obj in network:
            return ipaddress.IPv4Address(int(ip_obj) & 0xFFFFFFFF)
    return None


def _public_ip_rejection_reason(ip_obj: Any) -> str | None:
    """返回 IP 不可作为公网下载目标的拒绝原因，None 表示通过。

    统一静态 URL、DNS 解析、连接对端 IP 三处校验：拒绝非公网地址、RFC 6598 CGNAT 段，
    以及 6to4/Teredo 等可封装内网地址的 IPv6 段。
    """
    if ip_obj.version == 4 and ip_obj in _CGNAT_NETWORK:
        return "CGNAT地址(100.64.0.0/10)"
    if not ip_obj.is_global:
        return "非公网地址"
    if ip_obj.version == 6:
        if getattr(ip_obj, "sixtofour", None) or getattr(ip_obj, "teredo", None):
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
    """可重试的下载错误，如 HTTP 5xx 等 CDN/网关瞬时故障。

    继承 DownloadError 以兼容既有 except DownloadError 捕获；在重试循环中需先于
    DownloadError 单独捕获，将其作为可重试故障而非终态错误处理。
    """

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
    ):
        """初始化下载管理器。

        Args:
            timeout: 下载超时时间（秒）
            max_retries: 最大重试次数
            retry_delay: 重试延迟时间（秒）
            max_file_size: 最大文件大小（字节）
            dns_cache_ttl: DNS 解析缓存 TTL（秒）
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_file_size = max_file_size
        self._dns_cache_ttl = max(1, dns_cache_ttl)
        self._dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
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

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """获取可用的 aiohttp 会话。

        首次创建用双检锁串行化，避免并发请求各自创建会话导致旧会话泄漏。
        """
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                        trust_env=False,
                        cookie_jar=aiohttp.DummyCookieJar(),
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

    @staticmethod
    def _temp_path_for(save_path: Path) -> Path:
        """返回 save_path 对应的临时文件路径，在原后缀后追加 ``.part``。"""
        suffix = save_path.suffix or ".bin"
        return save_path.with_suffix(f"{suffix}.part")

    @staticmethod
    def _cleanup_temp_file(temp_path: Path, *, created: bool | None = None) -> None:
        """清理临时文件。

        Args:
            temp_path: 临时文件路径
            created: 调用方对 temp 文件创建状态的标记。
                False 表示本次流程未创建该文件，跳过 exists 检查；
                None（默认）保持防御性 exists 检查，用于清理潜在残留。
        """
        if created is False:
            return
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("清理临时文件失败: {} -> {}", temp_path, exc)

    def _validate_url_static(self, url: str) -> tuple[str, bool]:
        """执行不依赖网络的 URL 静态安全校验，属 SSRF 第一层防护。

        解析阶段即拒绝非 http/https 协议、缺失主机名、携带凭据、本地主机名以及
        非公网 IP 字面量，把明显伪造的内网目标挡在网络层之外。

        Returns:
            (host, needs_dns_check): ``needs_dns_check`` 为 False 表示入参为已通过
            校验的 IP 字面量，调用方无需再做 DNS 解析校验。
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

        # 直接 IP 地址：仅允许公网，并拒绝 6to4/Teredo 等可封装内网地址的 IPv6 段
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
        """
        now = time.time()
        async with self._dns_cache_lock:
            cached = self._dns_cache.get(host)
            if cached is not None:
                expires_at, cached_ips = cached
                if expires_at > now:
                    return cached_ips
                self._dns_cache.pop(host, None)

        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
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

        ip_tuple = tuple(sorted(resolved_ips))
        async with self._dns_cache_lock:
            self._dns_cache[host] = (time.time() + self._dns_cache_ttl, ip_tuple)
            if len(self._dns_cache) > _DNS_CACHE_MAX_SIZE:
                self._evict_expired_dns_entries()
        return ip_tuple

    def _evict_expired_dns_entries(self) -> None:
        """清理已过期的 DNS 缓存条目，防止长生命周期下多 host 缓存无界增长。

        仅移除已过期条目，存活条目保留以维持缓存命中率；调用方须已持有
        ``_dns_cache_lock``。
        """
        now = time.time()
        for expired_host in [h for h, (exp, _) in self._dns_cache.items() if exp <= now]:
            self._dns_cache.pop(expired_host, None)

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
        """校验连接后的对端 IP 是否为公网地址，复用统一的公网判定逻辑。"""
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

        闭合 DNS rebinding 的 TOCTOU 窗口：即便解析阶段校验通过，连接的对端 IP 仍
        可能因 DNS 切换而落入内网，故对实际建立连接的 IP 再校验一次。无法提取对端
        IP 时 fail-closed 拒绝下载，避免因校验信息缺失而放行潜在的内网连接。
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
        """
        if response.status not in {301, 302, 303, 307, 308}:
            return None
        location = response.headers.get("location")
        if not location:
            raise DownloadError("重定向响应缺少 Location 头")
        if redirect_count >= _MAX_REDIRECTS:
            raise DownloadError("重定向次数过多")
        return urljoin(current_url, location)

    async def _download_response_to_temp(
        self,
        response: "aiohttp.ClientResponse",
        save_path: Path,
        temp_path: Path,
        content_type: str,
        attempt: int,
        start_time: float,
    ) -> dict[str, Any]:
        """将 200 响应体下载到临时文件，校验大小与字节签名后原子替换，返回结果字典。

        content-length 预检、流式写入累计上限、首字节签名校验三道关卡任一失败均抛出
        DownloadError，由调用方按终态或可重试分类处理。
        """
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                cl_value = int(content_length)
            except (TypeError, ValueError) as exc:
                raise DownloadError(f"非法 content-length: {content_length!r}") from exc
            if cl_value > self.max_file_size:
                raise DownloadError(f"文件过大: {cl_value} 字节")

        # 目录创建卸载到线程池；exist_ok=True 下重复创建无副作用，保留作防御性兜底
        await asyncio.to_thread(save_path.parent.mkdir, parents=True, exist_ok=True)

        # O_NOFOLLOW 打开最终分量防符号链接 TOCTOU；aiofiles 接管 fd 后异步写入
        total_size = 0
        head_bytes = b""
        fd = await asyncio.to_thread(
            open_no_follow_fd,
            str(temp_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        )
        fd_handed_off = False
        try:
            async with aiofiles.open(fd, "wb", closefd=True) as f:
                fd_handed_off = True
                async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
                    total_size += len(chunk)
                    if total_size > self.max_file_size:
                        raise DownloadError(f"文件过大: {total_size} 字节")
                    # 累计首 32 字节做签名校验，省一次 open+read
                    if len(head_bytes) < 32:
                        head_bytes += chunk[: 32 - len(head_bytes)]
                    await f.write(chunk)
        finally:
            # aiofiles 接管前失败则手动关闭 fd 避免泄漏
            if not fd_handed_off:
                os.close(fd)

        # 字节层校验：防止 Content-Type 伪造使非图片或可执行内容落盘
        if not is_known_image_bytes(head_bytes):
            raise DownloadError("下载内容字节签名非受支持图片格式，疑似 Content-Type 伪造")

        # 同文件系统内原子替换，避免读者看到半写文件
        await asyncio.to_thread(temp_path.replace, save_path)
        download_time = time.time() - start_time
        logger.info(
            "图片下载成功: {} ({} 字节, {:.2f}秒)",
            save_path,
            total_size,
            download_time,
        )
        return {
            "success": True,
            "file_path": str(save_path),
            "file_size": total_size,
            "download_time": download_time,
            "content_type": content_type,
            "attempts": attempt + 1,
        }

    async def download_image(
        self, url: str, save_path: Path, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """异步下载图片。

        Args:
            url: 图片URL
            save_path: 保存路径
            headers: 请求头

        Returns:
            下载结果信息

        Raises:
            DownloadError: 下载失败时抛出
        """
        if headers is None:
            headers = {"User-Agent": f"Seedream-MCP/{__version__}", "Accept": "image/*"}

        start_time = time.time()
        last_error: DownloadError | None = None
        temp_path = self._temp_path_for(save_path)

        for attempt in range(self.max_retries + 1):
            temp_created = False
            try:
                logger.info(
                    "开始下载图片 (尝试 {}/{}): {}",
                    attempt + 1,
                    self.max_retries + 1,
                    sanitize_url(url),
                )

                await asyncio.to_thread(self._cleanup_temp_file, temp_path)
                session = await self._ensure_session()
                current_url = url
                redirect_count = 0
                # SSRF 第四层防护：禁用自动重定向改为逐跳手动处理，每跳重新走静态、
                # DNS 与连接对端 IP 完整校验，防止经由重定向绕过跳转到内网地址
                while True:
                    # 依赖网络状态的 DNS 校验放在重试循环内，保障 max_retries 生效
                    await self._validate_url_for_request(current_url)
                    # 固有 TOCTOU 窗口：此处 DNS 校验通过后，aiohttp 内置解析器会在
                    # 真正建连前再次独立解析主机名，攻击者可借 DNS rebinding 让实际连接
                    # 落向内网。当前采用反应式对端 IP 复核，由 _validate_connected_peer_ip
                    # 在连接建立后立即阻断并拒绝下载，故内网连接最多完成 TCP 握手即被放弃，
                    # 不会读取或落盘响应。彻底闭合该窗口需用自定义 TCPConnector 钉住已校验
                    # 公网 IP，即 resolve-once-and-pin 策略，但该改动跨连接与重定向生命周期、
                    # 风险较高，暂不引入；当前下载 URL 均来自火山引擎 API 返回，属可信来源，
                    # 残余风险在可控边界内。
                    async with session.get(
                        current_url,
                        headers=headers,
                        allow_redirects=False,
                    ) as response:
                        self._validate_connected_peer_ip(response, current_url)

                        next_url = self._handle_redirect_response(
                            response, current_url, redirect_count
                        )
                        if next_url is not None:
                            redirect_count += 1
                            current_url = next_url
                            continue

                        if response.status != 200:
                            if 500 <= response.status < 600:
                                # 5xx 多为 CDN/网关瞬时故障，纳入重试而非终态失败
                                raise RetryableDownloadError(f"HTTP错误: {response.status}")
                            raise DownloadError(f"HTTP错误: {response.status}")

                        # 检查内容类型：HTML 错误页、JSON 等明确非图片类型直接拒绝
                        content_type = response.headers.get("content-type", "")
                        if not _is_image_compatible_content_type(content_type):
                            raise DownloadError(
                                f"响应内容类型非图片: {content_type.split(';')[0].strip()}"
                            )

                        # 标记 temp 即将由 _download_response_to_temp 创建，供外层 finally
                        # 在失败时清理残留；成功路径内部 replace 后 temp 已移走，exists 检查无害
                        temp_created = True
                        return await self._download_response_to_temp(
                            response,
                            save_path,
                            temp_path,
                            content_type,
                            attempt,
                            start_time,
                        )

            except RetryableDownloadError as e:
                # HTTP 5xx 等 CDN/网关瞬时故障，记录后落入退避重试
                last_error = e
                logger.warning("可重试的 HTTP 错误 (尝试 {}): {}", attempt + 1, e)

            except DownloadError:
                # 4xx、文件过大、字节签名、重定向等语义明确的终态错误，原样抛出不重试
                raise

            except asyncio.TimeoutError as e:
                last_error = DownloadError(f"下载超时: {e}")
                logger.warning("下载超时 (尝试 {}): {}", attempt + 1, sanitize_url(url))

            except aiohttp.ClientError as e:
                last_error = DownloadError(f"网络错误: {sanitize_url(url)} [{type(e).__name__}]")
                logger.warning(
                    "网络错误 (尝试 {}): {} [{}]",
                    attempt + 1,
                    sanitize_url(url),
                    type(e).__name__,
                    exc_info=True,
                )

            except OSError as e:
                last_error = DownloadError(f"文件系统错误: {e}")
                logger.warning("文件系统错误 (尝试 {}): {}", attempt + 1, e, exc_info=True)

            except Exception as e:
                # 编程 bug、序列化失败、值错误等非可重试意外错误直接抛出，不浪费退避等待。
                # 上方分支已精确覆盖可重试场景：HTTP 5xx、超时、网络/传输、文件系统错误。
                # 调用方 auto_save 仍有兜底 except Exception 负责降级返回原始 URL。
                logger.warning(
                    "下载出现非预期错误，不再重试 (尝试 {}): {}",
                    attempt + 1,
                    e,
                    exc_info=True,
                )
                raise
            finally:
                await asyncio.to_thread(self._cleanup_temp_file, temp_path, created=temp_created)

            # 非末次尝试则按线性递增延迟加随机抖动退避后重试，抖动避免并发任务重试同步
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay * (attempt + 1) + random.uniform(0, 1))

        # 所有重试均失败，抛出最后一次记录的错误
        logger.error("图片下载失败，已重试 {} 次: {}", self.max_retries, sanitize_url(url))
        raise last_error or DownloadError("下载失败")

    def validate_url(self, url: str) -> bool:
        """验证 URL 静态格式与主机安全性，不执行 DNS 解析。

        Args:
            url: 要验证的URL

        Returns:
            是否为静态可接受 URL
        """
        try:
            self._validate_url_static(url)
            return True
        except DownloadError:
            return False
