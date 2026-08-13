"""异步图片下载管理器，提供带 SSRF 多层防护的安全下载能力。

核心安全设计为四层 SSRF 防护，纵深防御逐层收紧：

1. 静态 URL 校验（``_validate_url_static``）：解析阶段即拒绝私网、保留地址及
   非公网 IP 字面量，把 ``file://``、``http://127.0.0.1`` 等直接伪造挡在网络层外。
2. DNS 公网解析（``_resolve_public_ips``）：解析主机名后校验所有结果均为公网 IP，
   防 DNS rebinding 在静态校验通过后才切换到内网地址。
3. 连接后对端 IP 复核（``_validate_connected_peer_ip``）：实际建立连接后再次校验
   对端 IP，闭合解析与连接之间的 TOCTOU 窗口。
4. 逐跳重定向校验（``download_image`` 重定向循环）：禁用自动重定向，每跳目标都
   重新走完整校验，防止经由重定向绕过跳转到内网。

其余关键设计：失败按递增延迟加随机抖动重试；下载先写 ``.part`` 临时文件再
``os.replace`` 原子替换，避免半写文件对外可见；响应须经 Content-Type 与字节签名
双重校验后方落盘，防 Content-Type 伪造。
"""

import asyncio
import ipaddress
import os
import random
import re
import socket
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiofiles
import aiohttp

from ..version import __version__
from .errors import SeedreamMCPError
from .formats import is_known_image_bytes
from .logging import get_logger
from .os_utils import open_no_follow_fd

logger = get_logger(__name__)

# 下载单文件大小上限默认值
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024

# 流式下载块大小：较大块减少多 MB 图片的 await write 循环次数
_DOWNLOAD_CHUNK_SIZE = 256 * 1024


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


def _embedded_ipv4_in_six(ip_obj: Any) -> Optional[ipaddress.IPv4Address]:
    """提取 NAT64/IPv4-mapped/IPv4-compatible 段内嵌的 IPv4 地址，其他返回 None。"""
    for network in (_NAT64_NETWORK, _IPV4_MAPPED_NETWORK, _IPV4_COMPAT_NETWORK):
        if ip_obj in network:
            return ipaddress.IPv4Address(int(ip_obj) & 0xFFFFFFFF)
    return None


def _public_ip_rejection_reason(ip_obj: Any) -> Optional[str]:
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
        self._dns_cache: Dict[str, Tuple[float, Tuple[str, ...]]] = {}
        self._dns_cache_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "DownloadManager":
        """进入上下文，确保会话就绪后返回自身。"""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """退出上下文时关闭底层会话。"""
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """获取可用的 aiohttp 会话。

        首次创建用双检查锁串行化，避免并发请求各自创建会话导致旧会话泄漏。
        """
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                        trust_env=False,
                    )
        return self._session

    async def close(self) -> None:
        """关闭底层会话资源。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    @staticmethod
    def _temp_path_for(save_path: Path) -> Path:
        """返回 save_path 对应的临时文件路径，在原后缀后追加 ``.part``。"""
        suffix = save_path.suffix or ".bin"
        return save_path.with_suffix(f"{suffix}.part")

    @staticmethod
    def _cleanup_temp_file(temp_path: Path, *, created: Optional[bool] = None) -> None:
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
            if temp_path.exists():
                temp_path.unlink()
        except OSError as exc:
            logger.warning("清理临时文件失败: {} -> {}", temp_path, exc)

    def _validate_url_static(self, url: str) -> Tuple[str, bool]:
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

    async def _resolve_public_ips(self, host: str) -> Tuple[str, ...]:
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
        return ip_tuple

    async def _validate_public_dns(self, host: str) -> None:
        """异步解析域名并确保解析结果全部为公网 IP。"""
        await self._resolve_public_ips(host)

    async def _validate_url_for_request(self, url: str) -> None:
        """执行请求前 URL 安全校验，串联第一层静态校验与第二层 DNS 校验。"""
        host, needs_dns_check = self._validate_url_static(url)
        if needs_dns_check:
            await self._validate_public_dns(host)

    @staticmethod
    def _extract_peer_ip(response: aiohttp.ClientResponse) -> Optional[str]:
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
            raise DownloadError(f"连接命中不安全地址({reason}): {peer_ip} ({source_url})")

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

    async def download_image(
        self, url: str, save_path: Path, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
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
        last_error: Optional[DownloadError] = None
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

                self._cleanup_temp_file(temp_path)
                session = await self._ensure_session()
                current_url = url
                redirect_count = 0
                # SSRF 第四层防护：禁用自动重定向改为逐跳手动处理，每跳重新走静态、
                # DNS 与连接对端 IP 完整校验，防止经由重定向绕过跳转到内网地址
                while True:
                    # 依赖网络状态的 DNS 校验放在重试循环内，保障 max_retries 生效
                    await self._validate_url_for_request(current_url)
                    async with session.get(
                        current_url,
                        headers=headers,
                        allow_redirects=False,
                    ) as response:
                        self._validate_connected_peer_ip(response, current_url)

                        if response.status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise DownloadError("重定向响应缺少 Location 头")
                            if redirect_count >= 3:
                                raise DownloadError("重定向次数过多")

                            next_url = urljoin(current_url, location)
                            self._validate_url_static(next_url)

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

                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                cl_value = int(content_length)
                            except (TypeError, ValueError) as exc:
                                raise DownloadError(
                                    f"非法 content-length: {content_length!r}"
                                ) from exc
                            if cl_value > self.max_file_size:
                                raise DownloadError(f"文件过大: {cl_value} 字节")

                        save_path.parent.mkdir(parents=True, exist_ok=True)

                        # 下载写入临时文件，成功后原子替换。
                        # 用 O_NOFOLLOW 打开最终分量防符号链接 TOCTOU，与 file_manager.save_bytes
                        # 防护对齐；再交由 aiofiles 异步写入，closefd=True 使上下文退出时关闭 fd
                        total_size = 0
                        head_bytes = b""
                        fd = open_no_follow_fd(
                            str(temp_path),
                            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                        )
                        fd_handed_off = False
                        try:
                            temp_created = True
                            async with aiofiles.open(fd, "wb", closefd=True) as f:
                                fd_handed_off = True
                                async for chunk in response.content.iter_chunked(
                                    _DOWNLOAD_CHUNK_SIZE
                                ):
                                    total_size += len(chunk)
                                    if total_size > self.max_file_size:
                                        raise DownloadError(f"文件过大: {total_size} 字节")
                                    # 在写入循环内累计首 32 字节，省一次 open+read 做签名校验
                                    if len(head_bytes) < 32:
                                        head_bytes += chunk[: 32 - len(head_bytes)]
                                    await f.write(chunk)
                        finally:
                            # 若 aiofiles 进入上下文前失败而未接管 fd，需手动关闭以避免泄漏
                            if not fd_handed_off:
                                os.close(fd)

                        # 字节层校验：防止 Content-Type 伪造使非图片或可执行内容落盘
                        if not is_known_image_bytes(head_bytes):
                            raise DownloadError(
                                "下载内容字节签名非受支持图片格式，疑似 Content-Type 伪造"
                            )

                        # os.replace 在同文件系统内原子替换，避免读者看到半写文件
                        temp_path.replace(save_path)
                        temp_created = False
                        download_time = time.time() - start_time

                        result = {
                            "success": True,
                            "file_path": str(save_path),
                            "file_size": total_size,
                            "download_time": download_time,
                            "content_type": content_type,
                            "attempts": attempt + 1,
                        }

                        logger.info(
                            "图片下载成功: {} ({} 字节, {:.2f}秒)",
                            save_path,
                            total_size,
                            download_time,
                        )
                        return result

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
                last_error = DownloadError(f"网络错误: {e}")
                logger.warning("网络错误 (尝试 {}): {}", attempt + 1, e, exc_info=True)

            except OSError as e:
                last_error = DownloadError(f"文件系统错误: {e}")
                logger.warning("文件系统错误 (尝试 {}): {}", attempt + 1, e, exc_info=True)

            except Exception as e:
                last_error = DownloadError(f"未知错误: {e}")
                logger.warning("下载失败 (尝试 {}): {}", attempt + 1, e, exc_info=True)
            finally:
                self._cleanup_temp_file(temp_path, created=temp_created)

            # 非末次尝试则按线性递增延迟加随机抖动退避后重试，抖动避免并发任务重试同步
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay * (attempt + 1) + random.uniform(0, 1))

        # 所有重试均失败，抛出最后一次记录的错误
        logger.error("图片下载失败，已重试 {} 次: {}", self.max_retries, sanitize_url(url))
        raise last_error or DownloadError("下载失败")

    def validate_url(self, url: str) -> bool:
        """验证 URL 静态格式与主机安全性（不含 DNS 解析）。

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
