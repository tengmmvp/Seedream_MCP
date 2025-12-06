"""
下载管理模块
实现异步图片下载、超时控制、重试机制
"""

import asyncio
import aiohttp
import logging
from typing import Optional, Dict, Any
from pathlib import Path
import time

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """下载错误异常"""

    pass


class DownloadManager:
    """异步下载管理器"""

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        max_file_size: int = 50 * 1024 * 1024,  # 50MB
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

        start_time = time.time()
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                logger.info(f"开始下载图片 (尝试 {attempt + 1}/{self.max_retries + 1}): {url}")

                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as session:
                    async with session.get(url, headers=headers) as response:
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

                        # 下载并保存文件
                        total_size = 0
                        with open(save_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                total_size += len(chunk)
                                if total_size > self.max_file_size:
                                    raise DownloadError(f"文件过大: {total_size} 字节")
                                f.write(chunk)

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
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                url, path = urls_and_paths[i]
                processed_results.append(
                    {"success": False, "url": url, "file_path": str(path), "error": str(result)}
                )
            else:
                processed_results.append(result)

        return processed_results

    def validate_url(self, url: str) -> bool:
        """
        验证URL格式

        Args:
            url: 要验证的URL

        Returns:
            是否为有效URL
        """
        try:
            from urllib.parse import urlparse

            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
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
