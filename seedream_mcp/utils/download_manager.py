"""
下载管理模块
"""

import asyncio
import ipaddress
import logging
import socket
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiofiles
import aiohttp

from .errors import SeedreamMCPError

logger = logging.getLogger(__name__)


class DownloadError(SeedreamMCPError):
    """
    下载错误异常
    """

    pass


class DownloadManager:
    """
    异步下载管理器
    """

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        max_file_size: int = 50 * 1024 * 1024,
    ):
        """
        初始化下载管理器

        Args:
            timeout: 下载超时时间（秒）
            max_retries: 最大重试次数
            retry_delay: 重试延迟时间（秒）
            max_file_size: 最大文件大小（字节）
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_file_size = max_file_size
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "DownloadManager":
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """
        获取可用的 aiohttp 会话
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    async def close(self) -> None:
        """
        关闭底层会话资源
        """
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    @staticmethod
    def _temp_path_for(save_path: Path) -> Path:
        suffix = save_path.suffix or ".bin"
        return save_path.with_suffix(f"{suffix}.part")

    @staticmethod
    def _cleanup_temp_file(temp_path: Path) -> None:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError as exc:
            logger.warning(f"清理临时文件失败: {temp_path} -> {exc}")

    def _validate_url_static(self, url: str) -> Tuple[str, bool]:
        """
        执行不依赖网络的 URL 静态安全校验

        Returns:
            (host, needs_dns_check)
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

        # 直接 IP 地址：仅允许公网
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                raise DownloadError(f"不安全的IP地址: {host}")
            return host, False
        except ValueError:
            return host, True

    async def _validate_public_dns(self, host: str) -> None:
        """
        异步解析域名并确保解析结果全部为公网 IP
        """
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise DownloadError(f"域名解析失败: {host} ({exc})")

        if not infos:
            raise DownloadError(f"域名解析结果为空: {host}")

        for info in infos:
            resolved_ip = info[4][0]
            ip_obj = ipaddress.ip_address(resolved_ip)
            if not ip_obj.is_global:
                raise DownloadError(f"域名解析到非公网地址: {host} -> {resolved_ip}")

    async def _validate_url_for_request(self, url: str) -> None:
        """
        执行请求前 URL 安全校验
        """
        host, needs_dns_check = self._validate_url_static(url)
        if needs_dns_check:
            await self._validate_public_dns(host)

    async def download_image(
        self, url: str, save_path: Path, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        异步下载图片

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
            headers = {"User-Agent": "Seedream-MCP/1.0", "Accept": "image/*"}
        # 静态校验不依赖网络，提前失败避免无效重试
        self._validate_url_static(url)

        start_time = time.time()
        last_error = None
        temp_path = self._temp_path_for(save_path)

        for attempt in range(self.max_retries + 1):
            try:
                logger.info(f"开始下载图片 (尝试 {attempt + 1}/{self.max_retries + 1}): {url}")

                self._cleanup_temp_file(temp_path)
                session = await self._ensure_session()
                current_url = url
                redirect_count = 0
                while True:
                    # 依赖网络状态的 DNS 校验放在重试循环内，保障 max_retries 生效
                    await self._validate_url_for_request(current_url)
                    async with session.get(
                        current_url,
                        headers=headers,
                        allow_redirects=False,
                    ) as response:
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

                        # 检查响应状态
                        if response.status != 200:
                            raise DownloadError(f"HTTP错误: {response.status}")

                        # 检查内容类型
                        content_type = response.headers.get("content-type", "")
                        if not content_type.startswith("image/"):
                            logger.warning(f"内容类型可能不是图片: {content_type}")

                        # 检查文件大小
                        content_length = response.headers.get("content-length")
                        if content_length and int(content_length) > self.max_file_size:
                            raise DownloadError(f"文件过大: {content_length} 字节")

                        # 确保目录存在
                        save_path.parent.mkdir(parents=True, exist_ok=True)

                        # 下载写入临时文件，成功后原子替换
                        total_size = 0
                        async with aiofiles.open(temp_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                total_size += len(chunk)
                                if total_size > self.max_file_size:
                                    raise DownloadError(f"文件过大: {total_size} 字节")
                                await f.write(chunk)

                        temp_path.replace(save_path)
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
                            f"图片下载成功: {save_path} ({total_size} 字节, {download_time:.2f}秒)"
                        )
                        return result

            except asyncio.TimeoutError as e:
                last_error = DownloadError(f"下载超时: {e}")
                logger.warning(f"下载超时 (尝试 {attempt + 1}): {url}")

            except aiohttp.ClientError as e:
                last_error = DownloadError(f"网络错误: {e}")
                logger.warning(f"网络错误 (尝试 {attempt + 1}): {e}")

            except OSError as e:
                last_error = DownloadError(f"文件系统错误: {e}")
                logger.warning(f"文件系统错误 (尝试 {attempt + 1}): {e}")

            except Exception as e:
                last_error = DownloadError(f"未知错误: {e}")
                logger.warning(f"下载失败 (尝试 {attempt + 1}): {e}")
            finally:
                self._cleanup_temp_file(temp_path)

            # 如果不是最后一次尝试，等待后重试
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay * (attempt + 1))

        # 所有重试都失败了
        logger.error(f"图片下载失败，已重试 {self.max_retries} 次: {url}")
        raise last_error or DownloadError("下载失败")

    async def download_multiple_images(
        self,
        urls_and_paths: list[tuple[str, Path]],
        headers: Optional[Dict[str, str]] = None,
        max_concurrent: int = 5,
    ) -> list[Dict[str, Any]]:
        """
        并发下载多个图片

        Args:
            urls_and_paths: URL和保存路径的元组列表
            headers: 请求头
            max_concurrent: 最大并发数

        Returns:
            下载结果列表
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def download_with_semaphore(url: str, path: Path) -> Dict[str, Any]:
            async with semaphore:
                try:
                    return await self.download_image(url, path, headers)
                except Exception as e:
                    logger.error(f"下载失败: {url} -> {e}")
                    return {"success": False, "url": url, "file_path": str(path), "error": str(e)}

        tasks = [download_with_semaphore(url, path) for url, path in urls_and_paths]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常结果
        processed_results: list[Dict[str, Any]] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                url, path = urls_and_paths[i]
                processed_results.append(
                    {"success": False, "url": url, "file_path": str(path), "error": str(result)}
                )
            else:
                processed_results.append(result)

        return processed_results

    def validate_url(self, url: str) -> bool:
        """
        验证 URL 静态格式与主机安全性（不含 DNS 解析）

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

    def get_file_extension_from_url(self, url: str) -> str:
        """
        从URL获取文件扩展名

        Args:
            url: 图片URL

        Returns:
            文件扩展名（包含点号）
        """
        try:
            from urllib.parse import urlparse

            path = urlparse(url).path
            if "." in path:
                return Path(path).suffix.lower()
            return ".jpeg"  # 默认扩展名
        except Exception:
            return ".jpeg"
