"""异步图片下载管理器，提供带 SSRF 多层防护的安全下载能力。

核心安全设计为四层 SSRF 防护，纵深防御逐层收紧：

1. 静态 URL 校验：解析阶段即拒绝非 http/https 协议、凭据、本地主机名与非公网 IP
   字面量。
2. DNS 公网解析与连接钉死：域名解析结果须全部为公网 IP，会话连接器绑定
   ``_PublicIpPinningResolver`` 把连接目标钉死为校验通过的 IP，闭合 DNS rebinding 窗口。
3. 连接后对端 IP 复核：连接建立后再次校验对端 IP，作为钉死之上的纵深防御。
4. 逐跳重定向校验：禁用自动重定向，每跳重新走完整校验；跳向不同源时剥离调用方
   定制请求头。

其余关键设计：失败按递增延迟加随机抖动重试，携带 Range/If-Range 断点续传，
源变化或服务端不支持时退回全量重下；先写不可预测随机名临时文件再
``os.replace`` 原子替换；响应须经 Content-Type 与字节签名双重校验后方落盘，
压缩容器字节在终验处经解码回退复核后以解码字节落盘。
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import ipaddress
import os
import random
import socket
import time
import zlib
from pathlib import Path
from typing import Any, NamedTuple, NoReturn, cast
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from ...version import __version__
from ..core.errors import SeedreamMCPError
from ..core.executors import run_in_cpu_pool
from ..core.formats import (
    DEFAULT_MAX_FILE_SIZE,
    FORMAT_FAMILIES,
    MIME_BY_EXTENSION,
    SNIFF_HEAD_BYTES_FLOOR,
    format_file_too_large_bytes,
    infer_extension_from_bytes,
    is_known_image_bytes,
)
from ..core.http_int import strict_http_int
from ..core.logs import get_logger
from ..core.loop_bound import LoopKeyedInflight
from ..core.settle import await_settled_op
from .io_file import (
    _cleanup_temp_file,
    _close_fd_quietly,
    finalize_atomic_replace,
    open_no_follow_read,
    open_no_follow_write_fd,
    open_temp_fd,
)
from .io_url import sanitize_url

logger = get_logger()

# 流式下载块大小：较大块减少多 MB 图片的 await write 循环次数。
_DOWNLOAD_CHUNK_SIZE = 256 * 1024

# 落盘批量写阈值：累积至该字节数才提交一次写入线程任务，减少执行器跳转次数。
_WRITE_BATCH_BYTES = 4 * 1024 * 1024

# 重定向上限：逐跳手动跟踪并限制跳数，防止经由重定向链绕过 SSRF 校验。
_MAX_REDIRECTS = 3

# 非终态响应体的排空读取上限：连接须消费完响应体方可回池复用，重定向、HTTP 错误
# 与内容类型拒绝的响应体通常很小，64KB 足以覆盖；残留更多时放弃复用，由连接关闭兜底。
_DRAIN_RESPONSE_BYTES = 65536


async def _drain_response_quietly(response: aiohttp.ClientResponse) -> None:
    """尽力排空响应体使连接回池，排空自身失败不影响调用方的错误语义。"""
    with contextlib.suppress(Exception):
        await response.content.read(_DRAIN_RESPONSE_BYTES)


# 跨源重定向时保留的通用请求头：跳向不同源时鉴权或跟踪一类定制头被剥离，不原样
# 发给重定向目标；Accept-Encoding 为 identity 钉死项，随跳保留。
_CROSS_ORIGIN_SAFE_REQUEST_HEADERS = frozenset({"user-agent", "accept", "accept-encoding"})

# 同一图片格式的等价扩展名类，由 formats 格式家族表派生：多扩展名家族即等价类，
# .jpg 与 .jpeg、.heif 与 .heic 互为别名后缀。字节签名嗅探结果与 URL 派生扩展名
# 同属一个等价类时视为同格式，不改写落盘文件名。
_FORMAT_EQUIVALENT_EXTENSIONS: tuple[frozenset[str], ...] = tuple(
    frozenset(family_extensions)
    for family_extensions in FORMAT_FAMILIES.values()
    if len(family_extensions) > 1
)

# 字节签名校验失败的统一拒绝文案，流内即时判定与流末兜底两处共用。
_SIGNATURE_REJECTION_MESSAGE = "下载内容字节签名非受支持图片格式，疑似 Content-Type 伪造"


def _is_gzip_head(content: bytes) -> bool:
    """判定字节头是否为 gzip 魔数（0x1f8b），供终验解码回退嗅探容器形态。"""
    return content[:2] == b"\x1f\x8b"


def _is_zlib_head(content: bytes) -> bool:
    """判定字节头是否为 zlib 包装：首字节 0x78 且头两字节按大端为 31 的倍数。"""
    return len(content) >= 2 and content[0] == 0x78 and int.from_bytes(content[:2], "big") % 31 == 0


def _is_compression_container_head(content: bytes) -> bool:
    """判定字节头是否为终验解码回退受理的压缩容器（gzip 或 zlib 包装 deflate）。"""
    return _is_gzip_head(content) or _is_zlib_head(content)


def _inflate_part_file_capped(path: Path, wbits: int, max_output: int) -> bytearray:
    """分块流式读取临时文件解压并以 max_output 封顶解码输出，防解压炸弹。

    压缩体不整段驻留内存，按 _DOWNLOAD_CHUNK_SIZE 逐块读取喂 decompressobj，
    峰值收敛为约 1×解码输出加固定块缓冲。产出超限、输入尽而未收束、收束后
    仍有残留输入（块内 unused_data/unconsumed_tail 与文件未读入字节）抛
    ValueError，流损坏由 zlib 抛 zlib.error，调用方统一归为终态拒绝；
    max_length 限额逐轮取回 unconsumed_tail，单轮产出有界。
    """
    decomp = zlib.decompressobj(wbits=wbits)
    out = bytearray()
    with open_no_follow_read(path) as handle:
        while not decomp.eof:
            chunk = handle.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                # 输入已尽而流未收束：压缩流被截断。
                raise ValueError("压缩流未完整收束")
            pending: bytes = chunk
            while pending:
                produced = decomp.decompress(pending, _DOWNLOAD_CHUNK_SIZE)
                out += produced
                if len(out) > max_output:
                    raise ValueError("解码输出超过上限")
                if decomp.eof:
                    # eof 即收束：跨 max_length 轮次收束时残留输入滞留 unconsumed_tail，
                    # 循环内不再消费，残留判定统一移到循环外。
                    break
                pending = decomp.unconsumed_tail
        if handle.read(1) or decomp.unused_data or decomp.unconsumed_tail:
            raise ValueError("压缩流含残留尾部")
    return out


# 只读文件系统、磁盘满与配额超限类永久文件系统 errno：解码回退写回与重试循环
# 共用的终态判定集。
_PERMANENT_FS_ERRNOS = frozenset({errno.EROFS, errno.ENOSPC, errno.EDQUOT})


def _write_all(fd: int, data: bytes | bytearray) -> None:
    """把 data 全量写入 fd：os.write 契约允许部分写入，memoryview 切片免复制续写至写尽。"""
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = os.write(fd, view[written:])
        if count == 0:
            # 零进展写入若重试即空转：上抛 OSError，失败分级归调用方站点。
            raise OSError("写入零字节")
        written += count


def _rewrite_part_file(path: Path, data: bytes | bytearray) -> None:
    """以 no-follow 写打开续传临时文件并把 data 全量重写，供解码回退接管内容。

    截断失败文件未动、截断后写段的永久文件系统 errno 均原样上抛（瞬时保留
    状态重试、永久终态）；写段其余失败自截断起文件已毁，包装为可重试错误由
    调用方作废续传状态退回全量重下；打开段 PermissionError 文件未动，原样
    上抛由调用方保留状态。
    """
    fd = open_no_follow_write_fd(path)
    try:
        # 截断失败文件未动：原样上抛，持锁由调用方转可重试，其余走重试循环 errno 分类。
        os.ftruncate(fd, 0)
        try:
            _write_all(fd, data)
        except Exception as exc:
            if isinstance(exc, OSError) and exc.errno in _PERMANENT_FS_ERRNOS:
                # 永久文件系统错误重试不可恢复：原样上抛走终态分类，不烧重试预算。
                raise
            raise RetryableDownloadError("解码回退写回失败，临时文件已毁") from exc
    finally:
        _close_fd_quietly(fd)


def _append_part_bytes(path: Path, offset: int, data: bytearray) -> None:
    """以 no-follow 纪律打开续传临时文件，按偏移把 data 全量写入。

    fd 的开关收在函数内部，落定等待可取有界档：重复取消放行后线程仍自闭 fd，
    不与调用方关闭的续传 fd 竞争同号。
    """
    fd = open_no_follow_write_fd(path)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        _write_all(fd, data)
    finally:
        _close_fd_quietly(fd)


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
# 64:ff9b:1::/48 为 NAT64 本地段，IANA 注册为非全球可达，但 Python <3.12.4 的
# is_global 对其误报 True，故与 CGNAT 同口径显式前置拒绝。
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL_NETWORK = ipaddress.ip_network("64:ff9b:1::/48")
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
    if ip_obj.version == 6 and ip_obj in _NAT64_LOCAL_NETWORK:
        return "NAT64本地段(64:ff9b:1::/48)"
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


class TransientFileLockError(RetryableDownloadError):
    """已存在 .part 的读取、写入、撕裂尾截断、重开与终局替换遭杀毒软件等瞬时持锁的可重试错误。

    此类持锁一律可重试，新文件创建的权限拒绝维持终态。
    """

    pass


def _raise_transient_file_lock(operation: str, exc: PermissionError) -> NoReturn:
    """把瞬时持锁 PermissionError 单点转为可重试 TransientFileLockError。"""
    raise TransientFileLockError(f"{operation}瞬时持锁，重试") from exc


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


def _resume_validator(response: "aiohttp.ClientResponse") -> str | None:
    """取断点续传的源校验器：仅强 ETag 可用，无强 ETag 返回 None。

    弱 ETag 禁作 If-Range 验证器（RFC 9110 §13.1.5）；Last-Modified 弱语义且仅
    1 秒粒度（RFC 9110 §8.8.2），同秒重写的表示携同一值骗过 If-Range 取回新表示
    的 206，旧前缀拼新后缀即静默坏图，故不作回退，续传请求退回全量重下，由 200
    回退路径自愈。
    """
    etag = response.headers.get("etag")
    if etag and not etag.startswith("W/"):
        return etag
    return None


class _ContentRange(NamedTuple):
    """Content-Range 单点解析结果载体：三分量各位置可缺，unsatisfied 表未满足形态。

    unsatisfied 为真表示 ``bytes */*`` 的不可满足且总长未知声明，与畸形值可区分；
    畸形分量对应位置为 None，总长为 ``*`` 未知形态同样为 None。
    """

    start: int | None = None
    end: int | None = None
    total: int | None = None
    unsatisfied: bool = False


def _parse_content_range(content_range: str | None) -> _ContentRange:
    """解析 Content-Range 头的区间起点、区间终点与总长（``bytes 100-199/1234``）。

    畸形分量对应位置返回 None，总长为 ``*`` 未知形态同样返回 None；区间 ``*``
    的不可满足声明（``bytes */总长``）起点终点为 None，仅完整 ``bytes */*``
    形态置 unsatisfied。
    """
    if not content_range:
        return _ContentRange()
    parts = content_range.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bytes":
        return _ContentRange()
    if parts[1] == "*/*":
        return _ContentRange(unsatisfied=True)
    range_text, _, total_text = parts[1].partition("/")
    start_text, _, end_text = range_text.partition("-")
    return _ContentRange(
        strict_http_int(start_text),
        strict_http_int(end_text),
        strict_http_int(total_text),
    )


# 出站 Range/If-Range 恒由续传状态派生，调用方同名头在入口剥离。
_RESERVED_RANGE_HEADERS = frozenset({"range", "if-range"})


class _ResumeState:
    """断点续传状态：跨尝试复用的临时文件与已确认字节、嗅探窗口、源校验器。

    offset 只计入已确认落盘的字节，失败尝试的撕裂尾部由下一趟打开时的截断修正；
    已存字节覆盖嗅探窗口且持有校验器时才发起续传，否则退回全量重下。expected_total
    记录声明总长（206 以 Content-Range 总长为准、总长未知时沿用上趟已知总长，200
    由 Content-Length 声明，416 完成态由 ``bytes */总长`` 声明或 ``bytes */*``
    补记已确认偏移）并跨尝试留存，参与短体与越界判定；offset 恰达总长即完成态，
    200 无声明总长与开放式 206 的未知总量均以流正常收尾补记总长，短体与越界交付
    均不判完成，完成态重试不再发请求，直接重走落盘终局。
    """

    __slots__ = (
        "fd",
        "path",
        "offset",
        "head_bytes",
        "validator",
        "expected_total",
        "content_type",
    )

    def __init__(self) -> None:
        self.fd: int | None = None
        self.path: Path | None = None
        self.offset = 0
        self.head_bytes = b""
        self.validator: str | None = None
        self.expected_total: int | None = None
        self.content_type = ""

    def is_completed(self) -> bool:
        """判断是否为完成态：已确认字节恰达已知总长，越界交付不判完成。"""
        return self.expected_total is not None and self.offset == self.expected_total

    def has_file(self) -> bool:
        """已持有临时文件 fd 与路径时为真。"""
        return self.fd is not None and self.path is not None

    def ensure_file(self, directory: Path, suffix: str) -> bool:
        """按需创建随机名临时文件，返回是否本次新建，重复调用幂等。"""
        if self.has_file():
            return False
        self.fd, self.path = open_temp_fd(directory, suffix=suffix)
        return True

    def request_headers(self) -> dict[str, str]:
        """返回续传请求头，未覆盖嗅探窗口或无校验器时返回空字典退回全量。"""
        if self.validator is None or self.offset < SNIFF_HEAD_BYTES_FLOOR:
            return {}
        return {"Range": f"bytes={self.offset}-", "If-Range": self.validator}

    def restart(self) -> None:
        """作废已存部分字节，下一趟从零重下，嗅探窗口一并复位。"""
        self.offset = 0
        self.head_bytes = b""

    async def invalidate_prefix_and_raise_retryable(
        self, response: aiohttp.ClientResponse, message: str
    ) -> NoReturn:
        """排空响应体使连接回池，作废已存部分前缀并抛可重试错误，重试退回全量、
        不再携带注定失败的同一 Range。
        """
        await _drain_response_quietly(response)
        self.restart()
        raise RetryableDownloadError(message)

    async def dispose(self) -> None:
        """关闭残留 fd 并删除未落成的临时文件，成功替换后为空操作。"""
        if self.fd is not None:
            fd, self.fd = self.fd, None
            _close_fd_quietly(fd)
        if self.path is not None:
            path, self.path = self.path, None
            # 取消安全等待清理线程落定后再放行取消，避免 .part 残留；清理失败经
            # _cleanup_temp_file 告警降级，不顶替原始下载错误。
            await await_settled_op(
                asyncio.to_thread(_cleanup_temp_file, path), absorb_all_cancels=True
            )


class _RangeClassification(NamedTuple):
    """终态响应的续传分类描述符：Content-Range 单点解析并经校验后的唯一载体。

    continuing 为真表示 206 已核对区间与表示身份（ETag）为已存前缀的续传应答，
    区间终点与总长随描述符下传落盘侧推导声明总长与分片长，区间起点恒等于已确认
    偏移不再单独携带；
    completed 为真表示 416 证明区间起点已达表示总长（``bytes */总长`` 恰等偏移
    或总长未知的 ``bytes */*``），声明或补记总长随 range_total 携带由下载侧写
    回、完整数据已在本地；全默认值即非续传形态。
    """

    continuing: bool = False
    range_end: int | None = None
    range_total: int | None = None
    completed: bool = False


async def _ensure_etag_matches_resume(
    response: "aiohttp.ClientResponse", resume: _ResumeState
) -> None:
    """核验响应 ETag 与源校验器一致，不符时作废已存前缀退回全量重下。

    206 续传应答与 416 完成态声明共用同一表示身份核验口径：携带而不符即表示
    已变化，已确认前缀不得拼入新表示或判完成落盘；未携带时无法核验维持现状。
    """
    response_etag = response.headers.get("etag")
    if response_etag is not None and response_etag != resume.validator:
        await resume.invalidate_prefix_and_raise_retryable(
            response,
            f"续传响应ETag与源校验器不符: {response_etag!r}",
        )


async def _classify_range_response(
    response: "aiohttp.ClientResponse",
    resume: _ResumeState,
    range_requested: bool,
) -> _RangeClassification:
    """单点分类终态响应的续传语义：解析一次 Content-Range、校验后返回描述符。

    出站请求携带 Range 才把 206/416 解读为续传应答（出站 Range 单源，调用方
    同名头已在入口剥离）：206 校验区间起点与已确认偏移一致且声明总长不倒退，
    声明总长与上趟已知总长不符或响应 ETag 与源校验器不符（携带而不符即表示
    已变化；未携带时无法核验维持现状）时作废已存前缀退回全量重下；416 解析
    RFC 9110 §15.5.17
    约定的 ``bytes */总长`` 声明，声明总长与已知总长不符时作废已存前缀退回
    全量重下，总长不小于已确认偏移时排空响应体，总长随描述符下传由调用方
    写回续传状态，恰等于偏移判完成态，总长未知的 ``bytes */*`` 不可满足声明
    对开放式区间同样判完成，已知总长大于偏移时与不可满足声明矛盾同样作废
    退回全量重下，两完成态先经与 206 同口径的 ETag 核验，不符同样作废退回
    全量重下，大于偏移属对端违约按终态失败，缺失声明或总长倒退作废已存前缀
    退回全量重下；未携带 Range 或非 206/416 时返回非续传分类，由调用方按
    非 200 终态处理，未校验的 206 不进入落盘侧。

    Raises:
        DownloadError: 416 声明总长大于已确认偏移，区间按声明本可满足，重试
            请求逐字节相同，首现即终态失败。
        RetryableDownloadError: 206 缺失或畸形 Content-Range、区间与已确认偏移不符、
            声明总长倒退或与已知总长不符、响应 ETag 与源校验器不符（206 与 416
            完成态同口径），或 416 声明缺失、畸形、倒退及与已知总长不符（含
            ``*/*`` 声明与已知总长矛盾）；均作废已存前缀退回全量。
    """
    if not range_requested or response.status not in (206, 416):
        return _RangeClassification()
    if response.status == 416:
        declared = _parse_content_range(response.headers.get("content-range"))
        declared_total = declared.total
        # 声明总长与已知总长不符即表示已变化：旧前缀不得判完成落盘，同 206 口径。
        if (
            declared_total is not None
            and resume.expected_total is not None
            and declared_total != resume.expected_total
        ):
            await resume.invalidate_prefix_and_raise_retryable(
                response,
                f"416 声明总长与已知总长不符: {declared_total}/{resume.expected_total}",
            )
        if declared_total is not None and declared_total >= resume.offset:
            # 声明总长不小于已确认偏移：排空响应体，总长随描述符下传，完成与违约终态共用。
            await _drain_response_quietly(response)
            if declared_total == resume.offset:
                # 服务器证明全部字节已收讫；完成态同 206 口径核验表示身份，
                # 已变化前缀不得判完成落盘。
                await _ensure_etag_matches_resume(response, resume)
                return _RangeClassification(completed=True, range_total=declared_total)
            # 区间按声明本可满足，重试请求逐字节相同：对端违约首现即终态，不烧重试预算。
            raise DownloadError(
                f"HTTP错误: {response.status}，声明总长 {declared_total} 大于"
                f"已确认偏移 {resume.offset}，区间应可满足"
            )
        if declared.unsatisfied:
            # ``bytes */*`` 对开放式区间即起点已达表示总长：已确认字节即完整
            # 数据，补记偏移为总长同样判完成；完成态同 206 口径核验表示身份。
            if resume.expected_total is not None and resume.expected_total > resume.offset:
                # 不可满足声明意味着新表示总长已缩至偏移以内，与已知总长矛盾
                # 即表示已变化：作废前缀退回全量重下。
                await resume.invalidate_prefix_and_raise_retryable(
                    response,
                    f"416 不可满足声明与已知总长矛盾: " f"{resume.expected_total}/{resume.offset}",
                )
            await _drain_response_quietly(response)
            await _ensure_etag_matches_resume(response, resume)
            return _RangeClassification(completed=True, range_total=resume.offset)
        # 缺失声明或声明总长倒退：已存前缀与当前表示不符，作废退回全量重下。
        await resume.invalidate_prefix_and_raise_retryable(
            response, f"HTTP错误: {response.status}，区间不可满足，退回全量重下"
        )
    content_range = _parse_content_range(response.headers.get("content-range"))
    # Content-Range 缺失或畸形时无法定位分片起点。
    if content_range.start is None:
        await resume.invalidate_prefix_and_raise_retryable(
            response,
            "续传响应缺少有效 content-range: " f"{response.headers.get('content-range')!r}",
        )
    # 区间与已确认偏移不符或声明总长倒退：源侧视图与已存前缀已不一致。
    if content_range.start != resume.offset or (
        content_range.total is not None and content_range.total < resume.offset
    ):
        await resume.invalidate_prefix_and_raise_retryable(
            response,
            f"续传响应区间与已存偏移不符: {response.headers.get('content-range')!r}",
        )
    # 声明总长与上趟已知总长不符即表示已变化：旧前缀不得拼入新表示，与 ETag
    # 核验同口径；总长缺失或无已知总长时无法比对维持现状。
    if (
        content_range.total is not None
        and resume.expected_total is not None
        and content_range.total != resume.expected_total
    ):
        await resume.invalidate_prefix_and_raise_retryable(
            response,
            f"续传响应声明总长与已知总长不符: {content_range.total}/{resume.expected_total}",
        )
    # 响应 ETag 与源校验器不符即表示已变化：无视 If-Range 的源站回新表示字节时，
    # 新尾部不得拼接到旧前缀。
    await _ensure_etag_matches_resume(response, resume)
    return _RangeClassification(True, content_range.end, content_range.total)


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
        self._dns_inflight: LoopKeyedInflight[str, tuple[str, ...]] = LoopKeyedInflight()
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
                    # auto_decompress=False：禁用透明解压使等量核算以传输字节执行，
                    # 不把口径寄托在服务器遵守 identity 提示上。
                    self._session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(
                            total=self._download_total_budget(),
                            sock_connect=self.timeout,
                            sock_read=self.timeout,
                        ),
                        trust_env=False,
                        auto_decompress=False,
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
        缓存；同 host 并发下载经单飞注册表在途去重共享一次解析，取消传播隔离
        与孤儿异常兜底由 InflightEntry 统一承担。

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
        # 在途检查与登记之间无 await 点，锁外单飞不会交错创建重复解析 task。
        return await self._dns_inflight.consume_shared(
            host, lambda: asyncio.ensure_future(self._resolve_and_cache(host))
        )

    async def _resolve_and_cache(self, host: str) -> tuple[str, ...]:
        """执行单次解析与公网校验并写入缓存，供 _resolve_public_ips 在途去重。

        在途登记由创建方经完成回调按条目身份清理，失败后的后续请求重新发起解析。
        """
        ip_tuple = await self._resolve_public_ips_uncached(host)
        async with self._dns_cache_lock:
            self._dns_cache[host] = (time.monotonic() + self._dns_cache_ttl, ip_tuple)
            if len(self._dns_cache) > _DNS_CACHE_MAX_SIZE:
                self._enforce_dns_cache_limit()
        return ip_tuple

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

        resolved_ips: list[str] = []
        for info in infos:
            # sockaddr 首元素在主机名解析下恒为地址字符串；联合类型中的 int 首位仅来自 disable-ipv6 构建变体，常规构建运行时不出现。
            resolved_ip = cast("str", info[4][0])
            try:
                ip_obj = ipaddress.ip_address(resolved_ip)
            except ValueError as exc:
                raise DownloadError(f"域名解析返回非法IP: {host} -> {resolved_ip}") from exc
            reason = _public_ip_rejection_reason(ip_obj)
            if reason:
                raise DownloadError(f"域名解析到不安全地址({reason}): {host} -> {resolved_ip}")
            resolved_ips.append(resolved_ip)

        if not resolved_ips:
            raise DownloadError(f"域名解析结果为空: {host}")

        # 保序去重，维持 getaddrinfo 的可达性序；字符串排序会把不可达 IPv6 排到首位。
        return tuple(dict.fromkeys(resolved_ips))

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

    async def _stream_response_to_resume(
        self,
        response: "aiohttp.ClientResponse",
        resume: _ResumeState,
        save_path: Path,
        temp_suffix: str,
        content_type: str,
        attempt: int,
        wall_start_time: float,
        fsync: bool,
        classification: _RangeClassification,
    ) -> dict[str, Any]:
        """把单趟响应体追加到续传临时文件，流末校验后原子替换，返回结果字典。

        续传语义以 classification 描述符为准（Content-Range 已由
        _classify_range_response 单点解析校验）：206 在已确认偏移后续写，声明
        总长以 Content-Range 为准，总长未知时沿用上趟已知总长；分片长以
        Content-Range 区间为准、区间缺失时以 Content-Length 回退，总长未知时按
        分片长校验实际接收。200 响应作废已确认字节从零重下并刷新源校验器。失败时
        保留临时文件与已确认偏移供重试续传，撕裂尾部由下一趟截断修正。扩展名以
        实际字节签名为准：与 save_path 的 URL 派生扩展名不一致且不属同一格式
        等价类时，落盘路径修正为嗅探扩展名。流正常收尾且恰达声明总长即置完成
        态，完成态重试不再发请求，直接重走落盘终局；短体按可重试上抛，越界交付
        属对端违约按终态失败，断流趟在尽力落盘已收字节后同口径复核，无声明总长
        的响应以流干净收尾补记总长判完成（出站区间恒开至表示尾）。

        Raises:
            DownloadError: Content-Length 与 Content-Range 总长预检、流式累计上限、
                字节签名校验失败，或交付越过声明总长。
            RetryableDownloadError: 响应体流收尾正常但未达声明总长或声明分片，或撕裂尾截断
                与落盘写入遭瞬时持锁。
        """
        continuing = classification.continuing
        base_offset = resume.offset if continuing else 0
        resume.content_type = content_type
        previous_total = resume.expected_total
        # 声明总长在局部收敛为单值后一次写回：Content-Range 总长优先，续传无总长
        # 声明时沿用上趟已知总长，200 由 Content-Length 声明。
        expected_total: int | None = None
        # 分片声明长度：206 实际接收量的校验基准。
        segment_expected: int | None = None
        content_length = response.headers.get("content-length")
        cl_value: int | None = None
        if content_length is not None:
            cl_value = strict_http_int(content_length)
            if cl_value is None:
                # 携带而非缺失的畸形整数按终态拒绝，宽松形态不得错误锚定总量。
                raise DownloadError(f"非法 content-length: {content_length!r}")
            if not continuing:
                if cl_value > self.max_file_size:
                    raise DownloadError(format_file_too_large_bytes(cl_value, self.max_file_size))
                expected_total = cl_value
        if continuing:
            range_end = classification.range_end
            range_total = classification.range_total
            # 声明总长以 Content-Range 为准：区间上限代理交付不足总长时，完成判定
            # 仍锚定总长，短体不得判完成。
            if range_total is not None:
                # 覆盖前同口径执行上限校验：虚高总长不得绕过文件过大早拒。
                if range_total > self.max_file_size:
                    raise DownloadError(
                        format_file_too_large_bytes(range_total, self.max_file_size)
                    )
                expected_total = range_total
            elif previous_total is not None:
                # 总长未知的续传应答沿用上趟已知总长：干净终止的短体不得因声明
                # 缺失而判成功。
                expected_total = previous_total
            # 分片长单点赋值：区间明确时以区间为准，全量长度的违规 Content-Length
            # 不参与推导；区间缺失或倒退时以 206 的分片体长度回退。
            if range_end is not None and range_end >= resume.offset:
                segment_expected = range_end - resume.offset + 1
            elif cl_value is not None:
                segment_expected = cl_value
        resume.expected_total = expected_total

        # ensure_file 在工作线程内完成 fd 与 .part 赋值，取消安全等待落定后即可
        # 读取；
        # 重试趟经与 ensure_file 幂等判定同源的 has_file 谓词跳过无 IO 的线程派发。
        if resume.has_file():
            fresh_file = False
        else:
            fresh_file = await await_settled_op(
                asyncio.to_thread(resume.ensure_file, save_path.parent, temp_suffix),
                absorb_all_cancels=True,
            )
        # fd 与 path 已持有或刚由 ensure_file 赋值，必非 None，cast 仅收窄 Optional 类型。
        fd = cast(int, resume.fd)
        part_path = cast(Path, resume.path)
        if not continuing:
            # 全量体由首趟、If-Range 失配或服务端不支持 Range 回退而来，
            # 作废已确认字节并刷新源校验器。
            resume.restart()
            resume.validator = _resume_validator(response)

        head_bytes = resume.head_bytes
        # 签名判定锁存为趟内局部：每趟恰评估一次，续传趟预填窗口亦重走。
        signature_verified = False
        # 截断失败尝试的撕裂尾部；执行器跳转守取消纪律，落定等待吸收任意次
        # 取消，dispose 关 fd 前在途 op 已落定，防 fd 复用后迟到截断误伤。
        # 新建空文件长度为 0，截断是空操作，跳过执行器往返。
        if not fresh_file:

            def _truncate_torn_tail() -> None:
                os.ftruncate(fd, resume.offset)

            try:
                await await_settled_op(
                    asyncio.to_thread(_truncate_torn_tail), absorb_all_cancels=True
                )
            except PermissionError as exc:
                # 撕裂尾截断遭杀软瞬时持锁：文件未动、状态保留，归可重试。
                _raise_transient_file_lock("撕裂尾截断", exc)

        # 批量累积写入：每次落盘提交一次执行器任务，逐 256KB chunk 跳转在批量
        # 下载时占用执行器线程槽，累积至 _WRITE_BATCH_BYTES 再提交。
        write_buffer = bytearray()

        async def _flush_received() -> None:
            if not write_buffer:
                return
            try:
                # 有界档：写入线程自持 fd 开关，重复取消放行不与 fd 关闭竞争。
                await await_settled_op(
                    asyncio.to_thread(_append_part_bytes, part_path, resume.offset, write_buffer)
                )
            except PermissionError as exc:
                # 杀软瞬时持锁归可重试，已确认偏移保留续传。
                _raise_transient_file_lock("落盘写入", exc)
            resume.offset += len(write_buffer)
            write_buffer.clear()

        async def _flush_best_effort() -> None:
            # 失败路径尽力落盘已缓冲字节：二次失败不掩盖原始异常。
            with contextlib.suppress(Exception):
                await _flush_received()

        def _raise_if_overshoot() -> None:
            # 交付越过声明总长属对端违约，尾部字节不可信：与虚高总长同口径按终态
            # 超限失败，断流与干净收流共用同一判定，恒定超长源不得每次重试
            # 全量重下耗尽预算。
            if expected_total is not None and resume.offset > expected_total:
                raise DownloadError(
                    format_file_too_large_bytes(resume.offset, expected_total, label="响应体")
                )

        try:
            async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
                write_buffer += chunk
                # 内存上限锚定已确认偏移加未刷盘缓冲，与实际占用量恒等。
                if resume.offset + len(write_buffer) > self.max_file_size:
                    raise DownloadError(
                        format_file_too_large_bytes(
                            resume.offset + len(write_buffer), self.max_file_size
                        )
                    )
                # 累计首部字节做签名校验，省一次 open+read；窗口取各格式最小
                # 长度下界的最大值，保证 is_known_image_bytes 对全部格式可判定；
                # 续传趟次的窗口取前趟留存，流末兜底校验复核。
                if len(head_bytes) < SNIFF_HEAD_BYTES_FLOOR:
                    head_bytes += chunk[: SNIFF_HEAD_BYTES_FLOOR - len(head_bytes)]
                    resume.head_bytes = head_bytes
                # 签名集满每趟锁存判定一次，防逐块重跑；
                # 伪造 Content-Type 的响应在首个 chunk 后被拒，
                # 不白耗后续带宽与磁盘；压缩容器头放行至终验，解码复核需全部字节。
                if not signature_verified and len(head_bytes) >= SNIFF_HEAD_BYTES_FLOOR:
                    if not is_known_image_bytes(head_bytes) and not _is_compression_container_head(
                        head_bytes
                    ):
                        raise DownloadError(_SIGNATURE_REJECTION_MESSAGE)
                    signature_verified = True
                if len(write_buffer) >= _WRITE_BATCH_BYTES:
                    await _flush_received()
        except RetryableDownloadError:
            # 可重试失败把已接收字节落盘供重试续传，撕裂尾部由下一趟截断修正。
            await _flush_best_effort()
            _raise_if_overshoot()
            raise
        except DownloadError:
            # 终态失败立即作废临时文件，已缓冲字节不再白写。
            raise
        except Exception:
            # 断流等意外异常与可重试同口径落盘已收字节，供重试续传。
            await _flush_best_effort()
            _raise_if_overshoot()
            raise
        await _flush_received()
        if expected_total is not None and resume.offset < expected_total:
            # 区间上限代理一类短体响应流收尾正常但未达声明总长：按可重试上抛，
            # 已确认偏移供下一趟续传补齐。
            raise RetryableDownloadError(f"响应体短于声明总量: {resume.offset}/{expected_total}")
        _raise_if_overshoot()
        if (
            expected_total is None
            and segment_expected is not None
            and resume.offset - base_offset < segment_expected
        ):
            # 总长未知时分片长是唯一声明基准：实际接收短于分片按可重试短体上抛。
            raise RetryableDownloadError(
                f"响应体短于声明分片: {resume.offset - base_offset}/{segment_expected}"
            )
        if expected_total is None:
            # 无声明总长（200 无 Content-Length 或开放式 206 的 ``*`` 总量）
            # 以流干净收尾为完成点：出站区间恒开至表示尾，干净收尾即尾部已尽；
            # 截断与连接中断仍经短体判定与异常路径走可重试。
            resume.expected_total = resume.offset
        return await self._finalize_completed_resume(
            resume, save_path, attempt, wall_start_time, fsync
        )

    async def _finalize_completed_resume(
        self,
        resume: _ResumeState,
        save_path: Path,
        attempt: int,
        wall_start_time: float,
        fsync: bool,
    ) -> dict[str, Any]:
        """完成态落盘终局：签名复核、路径修正与原子替换，返回结果字典。

        首趟流正常收尾与完成态重试共用本终局。字节头非受支持图片时先经
        _decode_compressed_resume 解码回退复核，解不出维持签名拒绝终态。替换前把
        临时文件截齐至已确认偏移：完成态捷径不经流写前的撕裂尾修复，部分写残留
        在此单点截除，正常完成为幂等空操作。上一趟终局可能在替换前已关闭 fd，
        此处经 no-follow 纪律在工作线程重开临时文件补做 fsync 与替换；重开发现
        临时文件已不存在时作废已确认字节，退回全量重下自愈；重开、终局截齐与
        终局替换的瞬时持锁均保留完成态与数据，下一趟重试重走终局。

        Raises:
            DownloadError: 字节签名校验失败（含解码回退不受理）或续传临时文件缺失。
            RetryableDownloadError: 重开的临时文件已不存在（作废状态退回全量重下），
                重开、终局截齐、终局替换、解码回退读取与写回打开、截断段遭瞬时
                持锁（保留状态，下一趟重试），或解码回退写回自截断起失败（作废
                状态退回全量重下）。
        """
        path = resume.path
        if path is None:
            raise DownloadError("续传临时文件缺失，无法落盘")
        # 字节层校验：小于嗅探窗口的响应体在流结束时兜底判定；压缩容器头由
        # 解码回退在全部原始字节确认后解压复核。
        if not is_known_image_bytes(resume.head_bytes):
            if not await self._decode_compressed_resume(resume):
                raise DownloadError(_SIGNATURE_REJECTION_MESSAGE)
        head_bytes = resume.head_bytes
        final_save_path = save_path
        sniffed = infer_extension_from_bytes(head_bytes)
        if sniffed != save_path.suffix.lower() and not _extensions_in_same_format(
            sniffed, save_path.suffix
        ):
            final_save_path = save_path.with_suffix(sniffed)
        fd = resume.fd
        if fd is None:
            try:
                # 取消时已重开的 fd 就地关闭防泄漏，.part 清理归 resume.dispose()。
                fd = await await_settled_op(
                    asyncio.to_thread(open_no_follow_write_fd, path),
                    dispose_on_cancel=_close_fd_quietly,
                    absorb_all_cancels=True,
                )
            except FileNotFoundError as exc:
                # .part 已不存在时重开不可恢复：作废完成态退回全量重下，而非空烧重试。
                resume.restart()
                raise RetryableDownloadError("续传临时文件已不存在，退回全量重下") from exc
            except PermissionError as exc:
                # .part 仍在但被杀毒等瞬时持锁：保留完成态与数据，下一趟直接重试重开。
                _raise_transient_file_lock("续传临时文件重开", exc)
            # 重开即持有化：截齐在途取消经 resume.fd 引用善后，防本地 fd 泄漏。
            resume.fd = fd
        try:
            # 撕裂尾终局截齐：部分写后可重试失败可致磁盘超前已确认偏移，完成态
            # 捷径不经流写前截断；disk==offset 时为幂等空操作。
            await await_settled_op(
                asyncio.to_thread(os.ftruncate, fd, resume.offset), absorb_all_cancels=True
            )
        except PermissionError as exc:
            # 截齐遭杀软瞬时持锁：关 fd 防泄漏，保留完成态与数据，下一趟重走终局。
            _close_fd_quietly(fd)
            resume.fd = None
            _raise_transient_file_lock("终局截齐", exc)
        except OSError:
            # 其余 OSError 关 fd 后原样上抛，交重试循环按 errno 归类。
            _close_fd_quietly(fd)
            resume.fd = None
            raise
        try:
            await finalize_atomic_replace(fd, path, final_save_path, fsync=fsync)
        except PermissionError as exc:
            # fsync/replace 遭杀软等瞬时持锁：保留完成态与数据，退避后重试重走终局。
            _raise_transient_file_lock("终局落盘", exc)
        finally:
            resume.fd = None
        # 替换已成，临时路径随之消亡；失败路径保留供重试重走终局。
        resume.path = None

        download_time = time.time() - wall_start_time
        logger.info(
            "图片下载成功: {} ({} 字节, {:.2f}秒)",
            final_save_path,
            resume.offset,
            download_time,
        )
        return {
            "success": True,
            "file_path": str(final_save_path),
            "file_size": resume.offset,
            "download_time": download_time,
            "content_type": resume.content_type,
            "attempts": attempt + 1,
        }

    async def _decode_compressed_resume(self, resume: _ResumeState) -> bool:
        """终验解码回退：解压临时文件中的压缩体并接管续传状态，不受理返回 False。

        无视 identity 强返 gzip 或 zlib 包装 deflate 的源站在此获得挽救：原始
        字节已全部确认，解压经 CPU 池分块流式执行并以 max_file_size 封顶输出防
        解压炸弹；解码异常、超限或解码字节仍非受支持图片返回 False，由调用方维持
        签名拒绝终态。成功时临时文件重写为解码字节，head_bytes/offset/
        expected_total/content_type 同步改写，扩展名与 MIME 以解码字节重新嗅探，
        后续终局按普通完成态落盘。

        Raises:
            RetryableDownloadError: 解码读取与写回打开、截断段遭瞬时持锁（文件
                未动，保留原始状态，下一趟重走解码与终局），或写回自截断起失败
                （临时文件已毁，作废续传状态退回全量重下自愈）。
        """
        path = resume.path
        if path is None or not _is_compression_container_head(resume.head_bytes):
            return False
        if _is_gzip_head(resume.head_bytes):
            wbits = 16 + zlib.MAX_WBITS
        else:
            wbits = zlib.MAX_WBITS
        try:
            decoded = await run_in_cpu_pool(
                _inflate_part_file_capped, path, wbits, self.max_file_size
            )
        except (ValueError, zlib.error):
            return False
        except PermissionError as exc:
            # 读取遭杀软瞬时持锁：文件未动、状态保留，归可重试。
            _raise_transient_file_lock("解码回退读取", exc)
        # 签名与扩展名嗅探仅依赖首部窗口：取头部切片免整段 bytes 复制。
        head = bytes(decoded[:SNIFF_HEAD_BYTES_FLOOR])
        if not is_known_image_bytes(head):
            return False
        try:
            await await_settled_op(
                asyncio.to_thread(_rewrite_part_file, path, decoded), absorb_all_cancels=True
            )
        except PermissionError as exc:
            # 打开或截断段持锁文件未动：保留原始状态，下一趟重走解码与终局。
            _raise_transient_file_lock("解码回退写回", exc)
        except RetryableDownloadError:
            # 自截断起文件已毁：作废完成态，重试退回全量重下自愈。
            resume.restart()
            resume.expected_total = None
            raise
        resume.head_bytes = head
        resume.offset = len(decoded)
        resume.expected_total = len(decoded)
        resume.content_type = MIME_BY_EXTENSION[infer_extension_from_bytes(head)]
        return True

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
        fsync: bool,
        resume: _ResumeState,
    ) -> dict[str, Any]:
        """执行单次下载尝试，含逐跳重定向循环，返回成功落盘结果字典。

        SSRF 第四层防护：禁用自动重定向，每跳重新走静态、DNS 与连接对端 IP 完整
        校验；会话连接器已把校验通过的公网 IP 钉死为连接目标，DNS rebinding 无从
        把实际连接切向内网。重定向链逐跳累计校验总预算：会话 total 超时按单次请求
        计窗，跟随下一跳前按起始时间累计校验，慢滴流重定向链不得把单次尝试拖至
        跳数倍封顶。跳向不同源时剥离调用方定制请求头，仅保留通用头。HTTP 5xx 抛
        RetryableDownloadError 由外层纳入退避重试，其余语义明确的终态错误原样上抛
        不重试。续传语义由 _classify_range_response 单点分类，分类描述符下传落盘侧；
        跨源跳转剥离续传头即作废已存前缀，按非续传续行，全量重下成为唯一路径。
        """
        current_url = url
        current_headers = headers
        redirect_count = 0
        while True:
            await self._validate_url_for_request(current_url)
            # 出站是否携带 Range 逐跳从将发送头判定：跨源剥离续传头后自动失效。
            range_requested = any(key.lower() == "range" for key in current_headers)
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
                        if range_requested:
                            # 跨源剥离续传头后出站无 Range：作废已存前缀按非续传续行，
                            # 他源 206 恰合偏移也不得与异源已存字节拼接。
                            resume.restart()
                        current_headers = _strip_custom_headers_for_cross_origin(headers)
                    await _drain_response_quietly(response)
                    redirect_count += 1
                    current_url = next_url
                    continue

                classification = await _classify_range_response(response, resume, range_requested)
                if classification.completed:
                    # 416 声明总长恰达已确认偏移：声明总长自描述符写回续传状态，
                    # 保住终局可重试失败后的完成态捷径；完整数据已确认，
                    # 直接重走落盘终局。
                    resume.expected_total = classification.range_total
                    return await self._finalize_completed_resume(
                        resume, save_path, attempt, wall_start_time, fsync
                    )
                if response.status != 200 and not classification.continuing:
                    await _drain_response_quietly(response)
                    # 5xx 与 408/429 多为瞬时故障或限流，纳入重试避免批量下载触发限流
                    # 后自动保存静默丢弃本地副本。
                    if response.status in (408, 429) or 500 <= response.status < 600:
                        raise RetryableDownloadError(f"HTTP错误: {response.status}")
                    raise DownloadError(f"HTTP错误: {response.status}")

                content_type = response.headers.get("content-type", "")
                if not _is_image_compatible_content_type(content_type):
                    await _drain_response_quietly(response)
                    raise DownloadError(f"响应内容类型非图片: {content_type.split(';')[0].strip()}")

                return await self._stream_response_to_resume(
                    response,
                    resume,
                    save_path,
                    temp_suffix,
                    content_type,
                    attempt,
                    wall_start_time,
                    fsync,
                    classification,
                )

    async def _download_with_retries(
        self,
        url: str,
        save_path: Path,
        temp_suffix: str,
        base_headers: dict[str, str],
        start_time: float,
        wall_start_time: float,
        fsync: bool,
        resume: _ResumeState,
    ) -> dict[str, Any]:
        """按线性退避重复下载尝试直至成功、终态失败或重试耗尽。

        异常分类：可重试错误（HTTP 5xx/408/429、DNS 瞬时故障、瞬时 OSError、
        网络类 ClientError）记录后落入退避重试；语义明确的终态错误、权限拒绝与
        只读文件系统等永久错误、非下载语义的意外错误原样上抛不烧重试预算；跨
        尝试累计预算耗尽即停止重试，防止重试按尝试数成倍放大单个保存任务的占用。

        Raises:
            DownloadError: 终态失败原样上抛，或重试耗尽后以最后一次可重试错误收尾。
        """
        last_error: DownloadError | None = None
        # max_retries<0 时循环零次执行，预绑定防末行失败日志读 attempt 未定义。
        attempt = -1
        for attempt in range(self.max_retries + 1):
            try:
                # 防御性兜底：父目录可能在退避等待或并发清理期间被删除，每趟尝试前
                # 确保存在；在 try 内使其 OSError 按下方分支归一（权限/只读盘终态，
                # 其余重试）。
                await asyncio.to_thread(save_path.parent.mkdir, parents=True, exist_ok=True)
                if resume.is_completed():
                    # 完成态：响应体已全部确认落盘，重试不再发请求，
                    # 直接重走落盘终局；200 与无总长声明的 416 会作废已完整前缀
                    # 退回全量重下，空 206 更会把空文件归档顶替完整数据。
                    return await self._finalize_completed_resume(
                        resume, save_path, attempt, wall_start_time, fsync
                    )
                logger.info(
                    "开始下载图片 (尝试 {}/{}): {}",
                    attempt + 1,
                    self.max_retries + 1,
                    sanitize_url(url),
                )

                session = await self._ensure_session()
                attempt_headers = {**base_headers, **resume.request_headers()}
                return await self._attempt_download(
                    session,
                    url,
                    attempt_headers,
                    save_path,
                    temp_suffix,
                    attempt,
                    start_time,
                    wall_start_time,
                    fsync=fsync,
                    resume=resume,
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
                logger.warning(
                    "下载超时 (尝试 {}): {} ({})",
                    attempt + 1,
                    sanitize_url(url),
                    e,
                )

            except aiohttp.ClientError as e:
                # InvalidUrlClientError 是 URL 语法层面的永久错误，重试无意义，直接终态抛出；
                # 其余 ClientError 多为连接类瞬时故障，纳入退避重试。
                if isinstance(e, aiohttp.InvalidUrlClientError):
                    raise DownloadError(
                        f"无效的URL: {sanitize_url(url)} [{type(e).__name__}]"
                    ) from e
                last_error = DownloadError(f"网络错误: {sanitize_url(url)} [{type(e).__name__}]")
                logger.opt(exception=True).warning(
                    "网络错误 (尝试 {}): {} [{}]",
                    attempt + 1,
                    sanitize_url(url),
                    type(e).__name__,
                )

            except PermissionError as e:
                # 保存目录不可写等新文件创建权限拒绝属永久性错误，重试仅徒增延迟，
                # 直接终态抛出；已存在 .part 的读取、写入、撕裂尾截断、重开与
                # 终局替换持锁已单点转为可重试。
                raise DownloadError(f"权限拒绝，不可重试: {e}") from e

            except OSError as e:
                # 只读文件系统、磁盘满与配额超限等永久性错误不重试；其余瞬时 OSError 保持重试。
                if e.errno in _PERMANENT_FS_ERRNOS:
                    raise DownloadError(f"文件系统永久错误，不可重试: {e}") from e
                last_error = DownloadError(f"文件系统错误: {e}")
                logger.opt(exception=True).warning("文件系统错误 (尝试 {}): {}", attempt + 1, e)

            except Exception as e:
                # 编程 bug 等非可重试意外错误直接抛出不浪费退避等待；调用方 auto_save
                # 仍有兜底 except Exception 负责降级返回原始 URL。
                logger.opt(exception=True).warning(
                    "下载非预期错误，不再重试 (尝试 {}): {}",
                    attempt + 1,
                    e,
                )
                raise

            # 非末次尝试则按线性递增延迟加随机抖动退避后重试，抖动避免并发任务重试同步。
            if attempt < self.max_retries:
                # 跨尝试累计预算耗尽即停止重试，
                # 防止重试按尝试数成倍放大单个保存任务的占用。
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

        logger.error("图片下载失败，已尝试 {} 次: {}", attempt + 1, sanitize_url(url))
        raise last_error or DownloadError("下载失败")

    async def download_image(
        self,
        url: str,
        save_path: Path,
        headers: dict[str, str] | None = None,
        fsync: bool = False,
    ) -> dict[str, Any]:
        """异步下载图片。

        重试携带 Range/If-Range 断点续传：响应携带强 ETag 且已存部分字节时
        从已确认偏移续写，源变化或服务端不支持时退回全量重下；出站 Range 恒由
        续传状态派生，调用方头中的同名键在入口剥离；出站请求恒钉死
        Accept-Encoding 为 identity，等量核算的传输字节与声明总长保持同口径。

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
        # 临时文件的可读性后缀：原扩展名后追加 .part；续传临时文件跨尝试复用，
        # 实际路径由 open_temp_fd 随机生成。
        temp_suffix = (save_path.suffix or ".bin") + ".part"
        # 调用方头中的 Range/If-Range 与续传出站单源冲突：入口一次剥离并告警，
        # 重试各趟直接复用剥离结果。
        stripped_caller_headers = [key for key in headers if key.lower() in _RESERVED_RANGE_HEADERS]
        if stripped_caller_headers:
            logger.warning(
                "调用方头 {} 与断点续传出站 Range 单源冲突，已剥离", stripped_caller_headers
            )
        base_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in _RESERVED_RANGE_HEADERS and key.lower() != "accept-encoding"
        }
        # 钉死 Accept-Encoding 为 identity：等量核算以传输字节对照声明总长，透明解压
        # 会改写字节口径致短体或越界误判，调用方同名头一并覆盖。
        base_headers["Accept-Encoding"] = "identity"
        resume = _ResumeState()
        try:
            return await self._download_with_retries(
                url,
                save_path,
                temp_suffix,
                base_headers,
                start_time,
                wall_start_time,
                fsync,
                resume,
            )
        finally:
            # 最终失败或取消后统一清理续传临时文件，成功路径已被原子替换，此处为空操作。
            await resume.dispose()

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
