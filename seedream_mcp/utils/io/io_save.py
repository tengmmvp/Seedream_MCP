"""自动保存协调模块。

协调图片的批量并发下载与本地文件写入，内置按目录节流的旧文件清理。
下载或写入失败时采用降级策略，保留原始 URL 而不中断整体生成流程。
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from ..core.errors import SeedreamMCPError, sanitize_data_text, sanitize_error_text
from ..core.formats import (
    DEFAULT_IMAGE_EXTENSION,
    DEFAULT_MAX_FILE_SIZE,
    EXTENSION_BY_MIME,
    format_file_size_mb,
    infer_extension_from_bytes,
    is_known_image_bytes,
    parse_data_uri,
)
from ..core.logs import get_logger
from .io_download import DownloadManager, DownloadError, sanitize_url
from .io_storage import FileManager, FileManagerError

logger = get_logger(__name__)

# 自动清理的最短间隔，避免每次批量保存都触发全量目录扫描。
_CLEANUP_MIN_INTERVAL_SECONDS = 3600
# 按 base_dir 记录最近清理时间，跨请求共享节流；不同 base_dir 独立，互不抑制。
# 用 OrderedDict 并设上限，避免异常多变的 base_dir 使键无界增长耗尽内存。
_CLEANUP_LAST_RUN_MAX_ENTRIES = 16
_cleanup_last_run: OrderedDict[str, float] = OrderedDict()
# 保护 _cleanup_last_run 的检查与写入，避免并发请求同时通过节流检查重复触发清理。
_cleanup_lock = asyncio.Lock()
# 在途的后台清理任务，供进程级退出清理 drain_background_cleanup_tasks 等待完成。
_cleanup_tasks: set[asyncio.Task[None]] = set()


def reset_cleanup_state() -> None:
    """重置清理节流的模块级状态，供 resources 复位协议与测试隔离调用。

    asyncio.Lock 首次 acquire 后绑定当时的事件循环；pytest-asyncio 每个测试用例
    使用全新事件循环，跨循环复用旧锁会在 acquire 时报错。本函数重建 _cleanup_lock
    并清空 _cleanup_last_run，使后续调用从干净状态启动。
    """
    global _cleanup_lock, _cleanup_last_run, _cleanup_tasks
    _cleanup_lock = asyncio.Lock()
    _cleanup_last_run = OrderedDict()
    _cleanup_tasks = set()


async def drain_background_cleanup_tasks() -> None:
    """等待在途的后台清理任务全部完成，供进程级退出清理调用。

    请求路径的 AutoSaveManager.close 不等待清理，以免阻塞返回路径并引入跨请求耦合；
    stdio 经 lifespan teardown、streamable-http 经服务循环的退出清理间接调用本函数，
    保证进程退出时清理已完成、节流状态定局。任务自身失败已在
    _run_cleanup_in_background 内回滚节流时间戳并记录日志，此处仅等待不重试；
    等待期间新 spawn 的任务不在本轮快照内，交由下次调用或进程退出兜底。
    """
    if _cleanup_tasks:
        await asyncio.gather(*list(_cleanup_tasks), return_exceptions=True)


class AutoSaveError(SeedreamMCPError):
    """自动保存操作失败。"""

    pass


# Markdown 替代文本的长度上限：alt 只承担图片引用内的可访问性描述，超长文本放大
# 输出体积且破坏可读性；完整提示词已在 structuredContent 顶层 prompt 字段存在。
_MARKDOWN_ALT_MAX_LENGTH = 200


def _build_markdown_alt(alt_text: str | None) -> str:
    """净化 Markdown 替代文本，返回可直接嵌入 ``![...](...)`` 的文本。

    alt 为调用方可控自由文本：控制字符压平防注入，``\\``、``[``、``]`` 反斜杠转义
    保持图片引用的结构完整，超长在转义前截断到上限的一半。单字符转义后至多放大为
    两个字符，按上限的一半截断使转义结果必然不超上限，截断点也不会把反斜杠转义对
    劈成尾随孤立反斜杠。空文本回退固定文案；不使用 prompt 兜底，提示词在结构化输出
    顶层已有专门字段，拼入 alt 只会放大输出且引入注入面。
    """
    if not alt_text:
        return "Generated Image"
    flattened = re.sub(r"[\x00-\x1f\x7f]", " ", alt_text)
    max_flat_length = _MARKDOWN_ALT_MAX_LENGTH // 2
    if len(flattened) > max_flat_length:
        flattened = flattened[:max_flat_length]
    escaped = flattened.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return escaped or "Generated Image"


def _build_save_metadata(
    tool_name: str,
    save_time: str,
    file_size: int,
    content_type: str,
    attempts: int,
    download_time: float | None = None,
) -> dict[str, Any]:
    """构造保存结果元数据，download_time 仅下载路径提供。

    不记录 prompt：structuredContent 顶层已携带完整提示词，metadata 内重复一份
    只会放大输出体积。attempts 与 download_time 为下载诊断信息，保留供排查。
    """
    metadata: dict[str, Any] = {
        "tool_name": tool_name,
        "save_time": save_time,
        "file_size": file_size,
        # content_type 为下载响应头原文，属上游自由文本；与 AutoSaveResult.to_dict
        # 对 original_url/error 的净化同口径，控制字符与凭据片段不随 metadata 外泄。
        "content_type": sanitize_data_text(content_type),
        "attempts": attempts,
    }
    if download_time is not None:
        metadata["download_time"] = download_time
    return metadata


class AutoSaveResult:
    """自动保存结果，封装保存状态、本地路径、Markdown 引用与元数据。

    Attributes:
        success: 是否保存成功
        original_url: 原始图片 URL，Base64 保存路径为 base64 标识串
        local_path: 保存成功时的本地文件路径，未保存时为 None
        markdown_ref: Markdown 图片引用，未生成时为 None
        error: 失败时的错误描述，成功时为 None
        metadata: 保存结果元数据，缺省为空字典
    """

    def __init__(
        self,
        success: bool,
        original_url: str,
        local_path: str | None = None,
        markdown_ref: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """初始化自动保存结果。

        Args:
            success: 是否保存成功。
            original_url: 原始图片 URL，Base64 保存路径为 base64 标识串。
            local_path: 保存成功时的本地文件路径，未保存时为 None。
            markdown_ref: Markdown 图片引用，未生成时为 None。
            error: 失败时的错误描述，成功时为 None。
            metadata: 保存结果元数据；None 时置为空字典。
        """
        self.success = success
        self.original_url = original_url
        self.local_path = local_path
        self.markdown_ref = markdown_ref
        self.error = error
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        """将保存结果序列化为字典，仅包含已设置的字段。

        original_url、local_path 与 markdown_ref 为数据字段，过 sanitize_data_text
        剥离 userinfo 凭据与控制字符但不做常规截断，签名 URL 与本地路径保持完整可用；
        error 为自由文本，过 sanitize_error_text 截断。各字段的净化与 results.py 中
        data 项的对应字段对齐，防止同名字段在两条输出通道防护不对称。
        """
        result = {"success": self.success, "original_url": sanitize_data_text(self.original_url)}

        if self.local_path:
            result["local_path"] = sanitize_data_text(self.local_path)

        if self.markdown_ref:
            result["markdown_ref"] = sanitize_data_text(self.markdown_ref)

        if self.error:
            result["error"] = sanitize_error_text(self.error)

        if self.metadata:
            result["metadata"] = self.metadata

        return result


class AutoSaveManager:
    """自动保存管理器，协调并发下载、文件写入与节流清理。

    Attributes:
        file_manager: 文件管理器，负责保存路径生成与字节写入。
        download_manager: 下载管理器，自建或由外部共享。
        max_file_size: 最大文件大小（字节）。
        max_concurrent: 最大并发下载数。
        date_folder: 是否按日期创建文件夹。
        cleanup_days: 自动清理天数，0 表示不按天清理。
        max_total_bytes: 保存目录总字节上限，None 表示不限制。
    """

    def __init__(
        self,
        base_dir: Path | None = None,
        download_timeout: int = 30,
        max_retries: int = 3,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_concurrent: int = 5,
        date_folder: bool = True,
        cleanup_days: int = 30,
        max_total_bytes: int | None = None,
        download_manager: DownloadManager | None = None,
    ):
        """初始化自动保存管理器。

        Args:
            base_dir: 基础保存目录。
            download_timeout: 下载超时时间，仅自建下载管理器时生效。
            max_retries: 最大重试次数，仅自建下载管理器时生效。
            max_file_size: 最大文件大小，本实例自持；自建下载管理器时同时作为其上限。
            max_concurrent: 最大并发下载数。
            date_folder: 是否按日期创建文件夹。
            cleanup_days: 自动清理天数，0 表示不按天清理。
            max_total_bytes: 保存目录总字节上限，超出按最旧文件驱逐；None 表示不限制。
            download_manager: 外部共享的下载管理器，提供时复用其 HTTP 会话且不由本实例关闭。
        """
        self.file_manager = FileManager(base_dir)
        self.max_file_size = max_file_size
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
        self.max_total_bytes = max_total_bytes

    async def __aenter__(self) -> AutoSaveManager:
        """进入上下文，直接返回自身，不预热下载会话。"""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """退出上下文时释放本实例自有的下载资源。"""
        await self.close()

    async def close(self) -> None:
        """释放底层下载资源。

        仅关闭本实例自建的下载管理器；外部共享的下载管理器由其所有者管理，如 lifespan。
        不等待在途后台清理任务：清理仅访问文件系统、不依赖下载会话，失败已在任务内
        回滚节流时间戳并记录日志，无需 close 同步；模块级任务集合上的等待反而使请求
        返回路径被全量目录遍历阻塞，并造成跨请求延迟耦合。退出前的等待收敛在
        drain_background_cleanup_tasks，由进程级清理入口调用。
        """
        if self._owns_download_manager:
            await self.download_manager.close()

    async def _maybe_cleanup(self) -> None:
        """按目录节流触发清理，每个 base_dir 在最短间隔内仅执行一次。

        清理入口不设开关短路：遗留 .part 孤儿清扫须在 auto-save 启用时无条件可达，
        两项清理策略均显式关闭的部署下进程崩溃遗留的临时文件同样被回收，不无界
        累积。按天清理与配额驱逐仍由 run_cleanup_policies 按各自开关分别门控。
        节流时间戳仅在清理成功后保留，失败时回滚到清理前的值，使下次批量保存可尽快重试，
        避免瞬时清理失败被节流一整小时。重试频率受限于批量保存调用频率，不会形成即时重试
        风暴；锁内完成检查与占位保证并发请求不会同时进入清理。
        """
        base_key = str(self.file_manager.base_dir)
        now = time.time()
        async with _cleanup_lock:
            previous = _cleanup_last_run.get(base_key, 0.0)
            if now - previous < _CLEANUP_MIN_INTERVAL_SECONDS:
                return
            _cleanup_last_run[base_key] = now
            while len(_cleanup_last_run) > _CLEANUP_LAST_RUN_MAX_ENTRIES:
                _cleanup_last_run.popitem(last=False)
        # 后台执行清理，不阻塞当前请求返回；失败回滚节流时间戳供下次重试。
        task = asyncio.create_task(self._run_cleanup_in_background(base_key, previous))
        _cleanup_tasks.add(task)
        task.add_done_callback(_cleanup_tasks.discard)

    async def _run_cleanup_in_background(self, base_key: str, previous: float) -> None:
        """在后台线程执行清理，失败时回滚节流时间戳。

        run_cleanup_policies 对扫描级与逐项错误宽捕获并收入返回值 errors 列表而非
        上抛，逐项失败同样回滚节流时间戳：部分失败意味着目录可能仍超限，下次批量
        保存应尽快重试而非等待完整节流间隔。
        """
        try:
            # 单次目录扫描依次执行按天清理与总量配额驱逐，避免两策略各自全目录遍历。
            outcome = await asyncio.to_thread(
                self.file_manager.run_cleanup_policies,
                self.cleanup_days,
                self.max_total_bytes,
            )
        except Exception as e:
            await self._rollback_cleanup_throttle(base_key, previous)
            logger.warning("自动清理失败: {}", e, exc_info=True)
            return
        errors = outcome.get("errors") if isinstance(outcome, dict) else None
        if errors:
            await self._rollback_cleanup_throttle(base_key, previous)
            logger.warning("自动清理部分失败: {}", errors)

    async def _rollback_cleanup_throttle(self, base_key: str, previous: float) -> None:
        """回滚到清理前的时间戳，使下次批量保存能立即重试而非等待完整节流间隔。"""
        async with _cleanup_lock:
            _cleanup_last_run[base_key] = previous

    def _extension_from_mime(self, mime: str | None) -> str:
        """根据 MIME 类型推断文件扩展名，未知类型回退默认图片扩展名。"""
        if not mime:
            return DEFAULT_IMAGE_EXTENSION
        return EXTENSION_BY_MIME.get(mime.lower(), DEFAULT_IMAGE_EXTENSION)

    async def save_image(
        self,
        url: str,
        prompt: str | None = None,
        tool_name: str = "seedream",
        custom_name: str | None = None,
        alt_text: str | None = None,
    ) -> AutoSaveResult:
        """保存单个图片。

        Args:
            url: 图片 URL。
            prompt: 生成提示词；图层拆分等场景缺省时为 None，文件名回退内置基础名。
            tool_name: 工具名称。
            custom_name: 自定义文件名。
            alt_text: Markdown 替代文本。

        Returns:
            保存结果；下载或写入失败时 success 为 False 并携带错误描述，不向调用方
            抛出下载与文件系统异常。
        """
        try:
            logger.info("开始自动保存图片: {}", sanitize_url(url))

            if not self.download_manager.validate_url(url):
                raise AutoSaveError(f"无效的URL: {sanitize_url(url)}")

            # 创建保存路径；该操作内含 mkdir 与 resolve，移出事件循环线程执行。
            save_path = await asyncio.to_thread(
                self.file_manager.create_save_path,
                prompt=prompt or "",
                url=url,
                tool_name=tool_name,
                custom_name=custom_name,
                date_folder=self.date_folder,
            )

            download_result = await self.download_manager.download_image(url, save_path)

            # 字节签名嗅探可能修正扩展名，实际落盘路径以下载结果的 file_path 为准；
            # URL 派生的 save_path 此时可能指向不存在的文件，不得用于对外报告。
            final_path = Path(download_result["file_path"])

            markdown_alt = _build_markdown_alt(alt_text)
            markdown_ref = self.file_manager.generate_markdown_reference(final_path, markdown_alt)

            metadata = _build_save_metadata(
                tool_name=tool_name,
                save_time=datetime.now(timezone.utc).isoformat(),
                file_size=download_result.get("file_size", 0),
                content_type=download_result.get("content_type", ""),
                attempts=download_result.get("attempts", 1),
                # 缺失时传 None 省略该键，与 base64 保存路径的 metadata 形状一致。
                download_time=download_result.get("download_time"),
            )

            result = AutoSaveResult(
                success=True,
                original_url=url,
                local_path=str(final_path),
                markdown_ref=markdown_ref,
                metadata=metadata,
            )

            logger.info("图片保存成功: {}", final_path)
            return result

        except (DownloadError, FileManagerError, AutoSaveError) as e:
            logger.error("图片保存失败: {} -> {}", sanitize_url(url), e)
            return AutoSaveResult(success=False, original_url=url, error=str(e))
        except OSError as e:
            # 磁盘满、只读文件系统、权限等文件系统故障归为一类，避免可诊断错误落入未知兜底。
            logger.error("图片保存文件系统错误: {} -> {}", sanitize_url(url), e)
            return AutoSaveResult(success=False, original_url=url, error=f"文件系统错误: {e}")
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
        if estimated_size > self.max_file_size:
            raise AutoSaveError(
                f"Base64数据过大: 约 {format_file_size_mb(estimated_size)}，"
                f"最大支持 {format_file_size_mb(self.max_file_size)}"
            )

        # 火山引擎 base64 通常不含空白，直传 validate=True 校验避免对大串做全量复制；
        # 仅当含空白致校验失败时才清理后重试解码。
        try:
            content_bytes = base64.b64decode(raw_payload, validate=True)
        except Exception:
            try:
                content_bytes = base64.b64decode(re.sub(r"\s+", "", raw_payload), validate=True)
            except Exception as e:
                raise AutoSaveError(f"Base64解码失败: {e}") from e

        if len(content_bytes) > self.max_file_size:
            raise AutoSaveError(
                f"解码后数据过大: {format_file_size_mb(len(content_bytes))}，"
                f"最大支持 {format_file_size_mb(self.max_file_size)}"
            )

        if not is_known_image_bytes(content_bytes):
            raise AutoSaveError("Base64 数据不是受支持的图片格式")

        # 扩展名以字节签名嗅探为准，与下载路径嗅探修正最终路径的口径对称；mime 仅作
        # 嗅探失败时的回退，data URI 声明与字节不符时落盘扩展名仍与实际内容一致。
        extension = infer_extension_from_bytes(
            content_bytes, default=self._extension_from_mime(mime)
        )
        content_hash = self.file_manager.get_content_hash(content_bytes)
        return content_bytes, extension, content_hash

    async def save_base64_image(
        self,
        b64_data: str,
        prompt: str | None = None,
        tool_name: str = "seedream",
        custom_name: str | None = None,
        alt_text: str | None = None,
    ) -> AutoSaveResult:
        """保存单个 Base64 图片，支持 data URI 或纯 base64 字符串。

        Args:
            b64_data: Base64 编码数据，可为 data URI 或纯 base64 字符串。
            prompt: 生成提示词；图层拆分等场景缺省时为 None，文件名回退内置基础名。
            tool_name: 工具名称。
            custom_name: 自定义文件名。
            alt_text: Markdown 替代文本。

        Returns:
            保存结果；解码或写入失败时 success 为 False 并携带错误描述，不向调用方
            抛出解码与文件系统异常。
        """
        try:
            logger.info("开始自动保存 Base64 图片")

            # data URI 解析含对大 base64 串的 partition 全量拷贝，与解码、路径生成、写入一样
            # 属于同步 CPU/IO 操作，合并到单次工作线程执行，避免在事件循环中阻塞。
            def _prepare_and_save() -> tuple[dict[str, Any], str | None]:
                mime, payload = parse_data_uri(b64_data)
                content_bytes, extension, content_hash = self._prepare_base64_payload(payload, mime)
                save_path = self.file_manager.create_save_path_from_extension(
                    prompt=prompt or "",
                    extension=extension,
                    tool_name=tool_name,
                    custom_name=custom_name,
                    content_hash=content_hash,
                    date_folder=self.date_folder,
                )
                # save_path 由 create_save_path_from_extension 返回，父目录已确保存在，跳过重复 mkdir。
                write_result = self.file_manager.save_bytes(
                    save_path, content_bytes, ensure_parent=False
                )
                return write_result, mime

            write_result, mime = await asyncio.to_thread(_prepare_and_save)

            markdown_alt = _build_markdown_alt(alt_text)
            markdown_ref = self.file_manager.generate_markdown_reference(
                Path(write_result["file_path"]), markdown_alt
            )

            metadata = _build_save_metadata(
                tool_name=tool_name,
                save_time=write_result.get("save_time") or "",
                file_size=write_result.get("file_size", 0),
                content_type=mime or "",
                attempts=1,
            )

            logger.info("Base64 图片保存成功: {}", write_result["file_path"])
            return AutoSaveResult(
                success=True,
                original_url="base64",
                local_path=write_result["file_path"],
                markdown_ref=markdown_ref,
                metadata=metadata,
            )

        except (FileManagerError, AutoSaveError) as e:
            logger.error("Base64 图片保存失败: {}", e)
            return AutoSaveResult(success=False, original_url="base64", error=str(e))
        except OSError as e:
            # 磁盘满、只读文件系统、权限等文件系统故障归为一类，避免可诊断错误落入未知兜底。
            logger.error("Base64 图片保存文件系统错误: {}", e)
            return AutoSaveResult(success=False, original_url="base64", error=f"文件系统错误: {e}")
        except Exception as e:
            logger.error("Base64 图片保存出现未知错误: {}", e)
            return AutoSaveResult(success=False, original_url="base64", error=f"未知错误: {e}")

    async def _run_batch_save(
        self,
        factories: Sequence[Callable[[], Awaitable[AutoSaveResult]]],
        image_data: list[dict[str, Any]],
        *,
        fallback_url_key: str | None,
        log_label: str,
    ) -> list[AutoSaveResult]:
        """并发执行保存任务并归集结果。

        限制并发、将异常归一化为失败结果、统计成功数并触发节流清理。fallback_url_key
        指定 url 分支从 image_data 取原始标识的键；为 None 时固定为 "base64"。
        入参为协程工厂而非协程对象：协程在获得信号量后才创建，批量被整体取消时仍在
        排队的任务不遗留未 await 的协程对象，避免 GC 阶段的 RuntimeWarning 噪音。
        """
        # semaphore 保持局部构造：AutoSaveManager 按调用新建且每个实例至多执行一次
        # 批量保存，提升为实例属性不会带来复用收益。
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def save_with_semaphore(
            factory: Callable[[], Awaitable[AutoSaveResult]],
        ) -> AutoSaveResult:
            async with semaphore:
                return await factory()

        results = await asyncio.gather(
            *[save_with_semaphore(factory) for factory in factories], return_exceptions=True
        )

        processed_results: list[AutoSaveResult] = []
        for i, result in enumerate(results):
            # 取消信号必须向上传播，避免被下面的异常兜底吞掉。
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
                # 非 Exception 的 BaseException 视为进程级信号继续向上传播，不降级为失败结果。
                raise result

        success_count = sum(1 for r in processed_results if r.success)
        logger.info("{}: {}/{} 成功", log_label, success_count, len(image_data))

        await self._maybe_cleanup()
        return processed_results

    async def save_multiple_images(
        self, image_data: list[dict[str, Any]], tool_name: str = "seedream"
    ) -> list[AutoSaveResult]:
        """批量保存多个图片。

        Args:
            image_data: 图片数据列表，每个元素包含 url、prompt 等信息。
            tool_name: 工具名称。

        Returns:
            与入参顺序一致的保存结果列表；单项失败降级为失败结果，不中断批次。
        """
        logger.info("开始批量保存 {} 个图片", len(image_data))
        # 默认参数绑定当前项：闭包晚绑定会使全部工厂引用同一循环变量。
        factories = [
            lambda data=data: self.save_image(
                url=data.get("url", ""),
                prompt=data.get("prompt", ""),
                tool_name=tool_name,
                custom_name=data.get("custom_name"),
                alt_text=data.get("alt_text"),
            )
            for data in image_data
        ]
        return await self._run_batch_save(
            factories, image_data, fallback_url_key="url", log_label="批量保存完成"
        )

    async def save_multiple_base64_images(
        self, image_data: list[dict[str, Any]], tool_name: str = "seedream"
    ) -> list[AutoSaveResult]:
        """并发保存多个 Base64 图片。

        Args:
            image_data: 图片数据列表，每个元素包含 b64_json、prompt 等信息。
            tool_name: 工具名称。

        Returns:
            与入参顺序一致的保存结果列表；单项失败降级为失败结果，不中断批次。
        """
        logger.info("开始批量保存 {} 个 Base64 图片", len(image_data))
        factories = [
            lambda data=data: self.save_base64_image(
                b64_data=data.get("b64_json", ""),
                prompt=data.get("prompt", ""),
                tool_name=tool_name,
                custom_name=data.get("custom_name"),
                alt_text=data.get("alt_text"),
            )
            for data in image_data
        ]
        return await self._run_batch_save(
            factories, image_data, fallback_url_key=None, log_label="批量 Base64 保存完成"
        )
