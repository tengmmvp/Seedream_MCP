"""自动保存协调模块。

协调图片的批量并发下载与本地文件写入，内置按目录节流的旧文件清理。
下载或写入失败时采用降级策略，保留原始 URL 而不中断整体生成流程。
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Sequence

from .download_manager import DownloadManager, DownloadError, sanitize_url
from .errors import SeedreamMCPError
from .file_manager import FileManager, FileManagerError
from .formats import (
    DEFAULT_MAX_FILE_SIZE,
    EXTENSION_BY_MIME,
    _format_file_size_mb,
    is_known_image_bytes,
    parse_data_uri,
)
from .logging import get_logger

logger = get_logger(__name__)

# 自动清理的最短间隔，避免每次批量保存都触发全量目录扫描
_CLEANUP_MIN_INTERVAL_SECONDS = 3600
# 按 base_dir 记录最近清理时间，跨请求共享节流；不同 base_dir 独立，互不抑制
_cleanup_last_run: dict[str, float] = {}
# 保护 _cleanup_last_run 的检查与写入，避免并发请求同时通过节流检查重复触发清理
_cleanup_lock = asyncio.Lock()


def _reset_cleanup_state() -> None:
    """重置清理节流的模块级状态，仅供测试与 server._reset_lifespan_state 隔离调用。

    asyncio.Lock 首次 acquire 后绑定当时的事件循环；pytest-asyncio 每个测试用例
    使用全新事件循环，跨循环复用旧锁会在 acquire 时报错。本函数重建 _cleanup_lock
    并清空 _cleanup_last_run，使后续调用从干净状态启动。
    """
    global _cleanup_lock, _cleanup_last_run
    _cleanup_lock = asyncio.Lock()
    _cleanup_last_run = {}


class AutoSaveError(SeedreamMCPError):
    """自动保存错误异常。"""

    pass


def _build_save_metadata(
    prompt: str,
    tool_name: str,
    save_time: str,
    file_size: int,
    content_type: str,
    attempts: int,
    download_time: float | None = None,
) -> dict[str, Any]:
    """构造保存结果元数据，download_time 仅下载路径提供。"""
    metadata: dict[str, Any] = {
        "prompt": prompt,
        "tool_name": tool_name,
        "save_time": save_time,
        "file_size": file_size,
        "content_type": content_type,
        "attempts": attempts,
    }
    if download_time is not None:
        metadata["download_time"] = download_time
    return metadata


class AutoSaveResult:
    """自动保存结果，封装保存状态、本地路径、Markdown 引用与元数据。"""

    def __init__(
        self,
        success: bool,
        original_url: str,
        local_path: str | None = None,
        markdown_ref: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.success = success
        self.original_url = original_url
        self.local_path = local_path
        self.markdown_ref = markdown_ref
        self.error = error
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        """将保存结果序列化为字典，仅包含已设置的字段。"""
        result = {"success": self.success, "original_url": self.original_url}

        if self.local_path:
            result["local_path"] = self.local_path

        if self.markdown_ref:
            result["markdown_ref"] = self.markdown_ref

        if self.error:
            result["error"] = self.error

        if self.metadata:
            result["metadata"] = self.metadata

        return result


class AutoSaveManager:
    """自动保存管理器，协调并发下载、文件写入与节流清理。"""

    def __init__(
        self,
        base_dir: Path | None = None,
        download_timeout: int = 30,
        max_retries: int = 3,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_concurrent: int = 5,
        date_folder: bool = True,
        cleanup_days: int = 30,
        download_manager: DownloadManager | None = None,
    ):
        """
        初始化自动保存管理器

        Args:
            base_dir: 基础保存目录
            download_timeout: 下载超时时间，仅自建下载管理器时生效
            max_retries: 最大重试次数，仅自建下载管理器时生效
            max_file_size: 最大文件大小，仅自建下载管理器时生效
            max_concurrent: 最大并发下载数
            date_folder: 是否按日期创建文件夹
            cleanup_days: 自动清理天数，0表示不清理
            download_manager: 外部共享的下载管理器，提供时复用其 HTTP 会话且不由本实例关闭
        """
        self.file_manager = FileManager(base_dir)
        if download_manager is not None:
            self.download_manager = download_manager
            self._owns_download_manager = False
        else:
            self.download_manager = DownloadManager(
                timeout=download_timeout, max_retries=max_retries, max_file_size=max_file_size
            )
            self._owns_download_manager = True
        self.max_concurrent = max_concurrent
        self.date_folder = date_folder
        self.cleanup_days = cleanup_days

    async def __aenter__(self) -> AutoSaveManager:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """
        释放底层下载资源

        仅关闭本实例自建的下载管理器；外部共享的下载管理器由其所有者管理，如 lifespan。
        """
        if self._owns_download_manager:
            await self.download_manager.close()

    async def _maybe_cleanup(self) -> None:
        """按目录节流触发旧文件清理，每个 base_dir 在最短间隔内仅执行一次。"""
        if self.cleanup_days <= 0:
            return
        base_key = str(self.file_manager.base_dir)
        now = time.time()
        async with _cleanup_lock:
            if now - _cleanup_last_run.get(base_key, 0.0) < _CLEANUP_MIN_INTERVAL_SECONDS:
                return
            _cleanup_last_run[base_key] = now
        try:
            await self.cleanup_old_files(self.cleanup_days)
        except Exception as e:
            logger.warning("自动清理失败: {}", e, exc_info=True)

    def _extension_from_mime(self, mime: str | None) -> str:
        """根据 MIME 类型推断文件扩展名，未知类型回退到 .jpeg。"""
        if not mime:
            return ".jpeg"
        return EXTENSION_BY_MIME.get(mime.lower(), ".jpeg")

    async def save_image(
        self,
        url: str,
        prompt: str = "",
        tool_name: str = "seedream",
        custom_name: str | None = None,
        alt_text: str | None = None,
    ) -> AutoSaveResult:
        """
        保存单个图片

        Args:
            url: 图片URL
            prompt: 生成提示词
            tool_name: 工具名称
            custom_name: 自定义文件名
            alt_text: Markdown替代文本

        Returns:
            保存结果
        """
        try:
            logger.info("开始自动保存图片: {}", sanitize_url(url))

            if not self.download_manager.validate_url(url):
                raise AutoSaveError(f"无效的URL: {sanitize_url(url)}")

            # 创建保存路径；该操作内含 mkdir 与 resolve，移出事件循环线程执行
            save_path = await asyncio.to_thread(
                self.file_manager.create_save_path,
                prompt=prompt,
                url=url,
                tool_name=tool_name,
                custom_name=custom_name,
                date_folder=self.date_folder,
            )

            download_result = await self.download_manager.download_image(url, save_path)

            markdown_alt = alt_text or prompt or "Generated Image"
            markdown_ref = self.file_manager.generate_markdown_reference(save_path, markdown_alt)

            metadata = _build_save_metadata(
                prompt=prompt,
                tool_name=tool_name,
                save_time=datetime.now(timezone.utc).isoformat(),
                file_size=download_result.get("file_size", 0),
                content_type=download_result.get("content_type", ""),
                attempts=download_result.get("attempts", 1),
                download_time=download_result.get("download_time", 0),
            )

            result = AutoSaveResult(
                success=True,
                original_url=url,
                local_path=str(save_path),
                markdown_ref=markdown_ref,
                metadata=metadata,
            )

            logger.info("图片保存成功: {}", save_path)
            return result

        except (DownloadError, FileManagerError, AutoSaveError) as e:
            logger.error("图片保存失败: {} -> {}", sanitize_url(url), e)
            return AutoSaveResult(success=False, original_url=url, error=str(e))
        except Exception as e:
            logger.error("图片保存出现未知错误: {} -> {}", sanitize_url(url), e)
            return AutoSaveResult(success=False, original_url=url, error=f"未知错误: {e}")

    def _prepare_base64_payload(
        self, payload: str | None, mime: str | None
    ) -> tuple[bytes, str, str]:
        """同步解码 base64 并推断扩展名与内容哈希。

        strip/b64decode/sha256 均为 CPU 密集或全量遍历操作，集中于此供 save_base64_image
        经 asyncio.to_thread 调用，避免阻塞事件循环。
        """
        raw_payload = payload or ""
        if not raw_payload or raw_payload.isspace():
            raise AutoSaveError("空的Base64数据")

        estimated_size = (len(raw_payload) * 3) // 4
        if estimated_size > self.download_manager.max_file_size:
            raise AutoSaveError(
                f"Base64数据过大: 约 {_format_file_size_mb(estimated_size)}，"
                f"最大支持 {_format_file_size_mb(self.download_manager.max_file_size)}"
            )

        # 火山引擎 base64 通常不含空白，直传 validate=True 校验避免对大串做全量复制；
        # 仅当含空白致校验失败时才清理后重试解码
        try:
            content_bytes = base64.b64decode(raw_payload, validate=True)
        except Exception:
            try:
                content_bytes = base64.b64decode(re.sub(r"\s+", "", raw_payload), validate=True)
            except Exception as e:
                raise AutoSaveError(f"Base64解码失败: {e}") from e

        if len(content_bytes) > self.download_manager.max_file_size:
            raise AutoSaveError(
                f"解码后数据过大: {_format_file_size_mb(len(content_bytes))}，"
                f"最大支持 {_format_file_size_mb(self.download_manager.max_file_size)}"
            )

        if not is_known_image_bytes(content_bytes):
            raise AutoSaveError("Base64 数据不是受支持的图片格式")

        extension = (
            self._extension_from_mime(mime)
            if mime
            else self.file_manager.infer_extension_from_bytes(content_bytes, default=".jpeg")
        )
        content_hash = self.file_manager.get_content_hash(content_bytes)
        return content_bytes, extension, content_hash

    async def save_base64_image(
        self,
        b64_data: str,
        prompt: str = "",
        tool_name: str = "seedream",
        custom_name: str | None = None,
        alt_text: str | None = None,
    ) -> AutoSaveResult:
        """保存单个 Base64 图片，支持 data URI 或纯 base64 字符串。

        Args:
            b64_data: Base64 编码数据，可为 data URI 或纯 base64 字符串
            prompt: 生成提示词
            tool_name: 工具名称
            custom_name: 自定义文件名
            alt_text: Markdown 替代文本

        Returns:
            保存结果
        """
        try:
            logger.info("开始自动保存 Base64 图片")

            # data URI 解析含对大 base64 串的 partition 全量拷贝，与解码、路径生成、写入一样
            # 属于同步 CPU/IO 操作，合并到单次工作线程执行，避免在事件循环中阻塞
            def _prepare_and_save() -> tuple[bytes, dict[str, Any], str | None]:
                mime, payload = parse_data_uri(b64_data)
                content_bytes, extension, content_hash = self._prepare_base64_payload(payload, mime)
                save_path = self.file_manager.create_save_path_from_extension(
                    prompt=prompt,
                    extension=extension,
                    tool_name=tool_name,
                    custom_name=custom_name,
                    content_hash=content_hash,
                    date_folder=self.date_folder,
                )
                # save_path 由 create_save_path_from_extension 返回，父目录已确保存在，跳过重复 mkdir
                write_result = self.file_manager.save_bytes(
                    save_path, content_bytes, ensure_parent=False
                )
                return content_bytes, write_result, mime

            content_bytes, write_result, mime = await asyncio.to_thread(_prepare_and_save)

            markdown_alt = alt_text or prompt or "Generated Image"
            markdown_ref = self.file_manager.generate_markdown_reference(
                Path(write_result["file_path"]), markdown_alt
            )

            metadata = _build_save_metadata(
                prompt=prompt,
                tool_name=tool_name,
                save_time=write_result.get("save_time") or "",
                file_size=write_result.get("file_size", 0),
                content_type=mime or "",
                attempts=1,
            )

            original_desc = f"base64:{len(content_bytes)}"
            logger.info("Base64 图片保存成功: {}", write_result["file_path"])
            return AutoSaveResult(
                success=True,
                original_url=original_desc,
                local_path=write_result["file_path"],
                markdown_ref=markdown_ref,
                metadata=metadata,
            )

        except (FileManagerError, AutoSaveError) as e:
            logger.error("Base64 图片保存失败: {}", e)
            return AutoSaveResult(success=False, original_url="base64", error=str(e))
        except Exception as e:
            logger.error("Base64 图片保存出现未知错误: {}", e)
            return AutoSaveResult(success=False, original_url="base64", error=f"未知错误: {e}")

    async def _run_batch_save(
        self,
        tasks: Sequence[Awaitable[AutoSaveResult]],
        image_data: list[dict[str, Any]],
        *,
        fallback_url_key: str | None,
        log_label: str,
    ) -> list[AutoSaveResult]:
        """并发执行保存任务并归集结果。

        限制并发、将异常归一化为失败结果、统计成功数并触发节流清理。fallback_url_key
        指定 url 分支从 image_data 取原始标识的键；为 None 时固定为 "base64"。
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def save_with_semaphore(task: Awaitable[AutoSaveResult]) -> AutoSaveResult:
            async with semaphore:
                return await task

        results = await asyncio.gather(
            *[save_with_semaphore(task) for task in tasks], return_exceptions=True
        )

        processed_results: list[AutoSaveResult] = []
        for i, result in enumerate(results):
            # 取消信号必须向上传播，避免被下面的异常兜底吞掉
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                fallback = (
                    image_data[i].get(fallback_url_key, "unknown") if fallback_url_key else "base64"
                )
                processed_results.append(
                    AutoSaveResult(success=False, original_url=fallback, error=str(result))
                )
            elif isinstance(result, AutoSaveResult):
                processed_results.append(result)
            else:
                # 非 Exception 的 BaseException 视为进程级信号继续向上传播，不降级为失败结果
                raise result

        success_count = sum(1 for r in processed_results if r.success)
        logger.info("{}: {}/{} 成功", log_label, success_count, len(image_data))

        await self._maybe_cleanup()
        return processed_results

    async def save_multiple_images(
        self, image_data: list[dict[str, Any]], tool_name: str = "seedream"
    ) -> list[AutoSaveResult]:
        """
        批量保存多个图片

        Args:
            image_data: 图片数据列表，每个元素包含 url、prompt 等信息
            tool_name: 工具名称

        Returns:
            保存结果列表
        """
        logger.info("开始批量保存 {} 个图片", len(image_data))
        tasks = [
            self.save_image(
                url=data.get("url", ""),
                prompt=data.get("prompt", ""),
                tool_name=tool_name,
                custom_name=data.get("custom_name"),
                alt_text=data.get("alt_text"),
            )
            for data in image_data
        ]
        return await self._run_batch_save(
            tasks, image_data, fallback_url_key="url", log_label="批量保存完成"
        )

    async def save_multiple_base64_images(
        self, image_data: list[dict[str, Any]], tool_name: str = "seedream"
    ) -> list[AutoSaveResult]:
        """并发保存多个 Base64 图片。

        Args:
            image_data: 图片数据列表，每个元素包含 b64_json、prompt 等信息
            tool_name: 工具名称

        Returns:
            保存结果列表
        """
        logger.info("开始批量保存 {} 个 Base64 图片", len(image_data))
        tasks = [
            self.save_base64_image(
                b64_data=data.get("b64_json", ""),
                prompt=data.get("prompt", ""),
                tool_name=tool_name,
                custom_name=data.get("custom_name"),
                alt_text=data.get("alt_text"),
            )
            for data in image_data
        ]
        return await self._run_batch_save(
            tasks, image_data, fallback_url_key=None, log_label="批量 Base64 保存完成"
        )

    async def cleanup_old_files(self, days: int = 30) -> dict[str, Any]:
        """清理超过指定天数的旧文件。

        Args:
            days: 文件保留天数，超过此天数的文件将被删除

        Returns:
            清理统计信息
        """
        return await asyncio.to_thread(self.file_manager.cleanup_old_files, days)
