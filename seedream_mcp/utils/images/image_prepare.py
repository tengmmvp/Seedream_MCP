"""参考图预处理缓存子系统：LRU + single-flight 去重。

集中管理图像输入预处理结果的缓存与并发去重。本地文件签名复用 io_path 的越界判定
与 image_validation 的文件资格常量，确保签名与读取锁定同一文件，不因两侧规则漂移
命中陈旧缓存。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from pathlib import Path
from typing import Sequence

from .image_ref import classify_image_reference
from .image_validation import resolve_local_image_candidate
from ..core.logs import get_logger, log_unretrieved_task_exception

logger = get_logger(__name__)

# 预处理缓存键：(image 字符串, workspace_roots 字符串元组, 本地文件 mtime+size 签名)
PrepareCacheKey = tuple[str, tuple[str, ...], tuple[float, int]]

# 超过此长度的非本地输入改用摘要键，避免大 data URI 的 O(n) 哈希与比较阻塞事件循环。
_LARGE_IMAGE_THRESHOLD = 1024 * 1024

# 工作区 roots 元组 → 已 resolve 的 base 列表，跨图复用避免每图重复 resolve 同一根目录。
# 上限 _RESOLVED_BASES_CACHE_MAX_ENTRIES，超限淘汰最旧条目。dict 自 Python 3.7 起保持插入序。
_RESOLVED_BASES_CACHE_MAX_ENTRIES = 32
_resolved_bases_cache: dict[tuple[str, ...], list[Path]] = {}


class ImagePreparer:
    """参考图预处理缓存管理器，LRU + single-flight 去重。

    预处理结果按 (输入, workspace_roots, 本地文件签名) 缓存，避免并行请求对同一参考图
    重复读取与编码；同一键的并发 miss 复用同一在途 task。本地文件纳入 mtime+size 防内容
    替换返回陈旧编码。
    """

    def __init__(
        self,
        prepare_cache_max: int,
        prepare_cache_max_bytes: int,
        prepare_concurrency: int,
    ) -> None:
        self._prepare_cache: OrderedDict[PrepareCacheKey, str] = OrderedDict()
        self._prepare_cache_max = prepare_cache_max
        self._prepare_cache_max_bytes = prepare_cache_max_bytes
        self._prepare_cache_bytes = 0
        self._prepare_inflight: dict[PrepareCacheKey, asyncio.Task[str]] = {}
        self._prepare_concurrency = prepare_concurrency

    @staticmethod
    def _local_file_signature(image: str, workspace_roots: tuple[str, ...]) -> tuple[float, int]:
        """计算图像输入的缓存签名。

        本地文件返回 (mtime, size) 参与缓存键，内容替换后失效避免返回陈旧编码；
        URL 与 data URI 内容由字符串决定、无法定位文件时返回 (0.0, 0)。

        候选定位委托 image_validation.resolve_local_image_candidate，与
        utils.image_input._prepare_local_image 的实际读取路径共用同一规则，签名与
        读取锁定同一文件，不会因两侧规则漂移命中陈旧缓存。

        已 resolve 的 base 列表按 workspace_roots 缓存并在跨图间复用，避免批量多图时每图
        重复 resolve 同一根目录，降低网络挂载工作区下的 resolve 开销。
        """
        if classify_image_reference(image) != "local":
            return (0.0, 0)

        resolved_bases = _resolved_bases_cache.get(workspace_roots)
        if resolved_bases is None:
            resolved_bases = []
            for root in workspace_roots:
                try:
                    resolved_bases.append(Path(root).resolve())
                except (OSError, ValueError):
                    continue
            _resolved_bases_cache[workspace_roots] = resolved_bases
            if len(_resolved_bases_cache) > _RESOLVED_BASES_CACHE_MAX_ENTRIES:
                try:
                    _resolved_bases_cache.pop(next(iter(_resolved_bases_cache)))
                except KeyError:
                    pass

        found = resolve_local_image_candidate(image, resolved_bases)
        if found is None:
            return (0.0, 0)
        _, st = found
        return (st.st_mtime, st.st_size)

    @staticmethod
    def _data_uri_digest(image: str) -> str:
        """计算非本地图像输入的摘要键，供超大输入替代全串作缓存键。

        摘要取 128-bit（32 hex）：64-bit 截断的生日碰撞界约 2^32 次哈希即进入可行域，
        蓄意构造碰撞可令缓存命中返回他人输入；128-bit 将构造成本推出可行域，长度
        增量可忽略。
        """
        return "sha256:" + hashlib.sha256(image.encode("utf-8")).hexdigest()[:32]

    async def prepare_image_input(
        self, image: str, _roots_key: tuple[str, ...] | None = None
    ) -> str:
        """准备图像输入数据。

        将图像 URL 或本地文件路径转换为 API 所需格式。结果按 (输入, workspace_roots,
        本地文件签名) 缓存，避免并行请求对同一参考图重复读取与编码，并以工作区隔离键
        避免跨租户命中；本地文件纳入 mtime+size 防内容替换返回陈旧编码。缓存超限按 LRU
        淘汰；同一键的并发 miss 复用同一在途 task 实现 single-flight 去重。
        """
        if _roots_key is None:
            from ..io.io_path import get_workspace_roots

            _roots_key = tuple(str(r) for r in get_workspace_roots())
        # URL/data-URI 无本地文件 I/O，直接用空签名短路；本地文件签名含同步 stat/resolve，
        # 移至工作线程避免网络挂载工作区下阻塞事件循环。
        ref_kind = classify_image_reference(image)
        if ref_kind == "local":
            signature = await asyncio.to_thread(self._local_file_signature, image, _roots_key)
            key_image = image
        elif len(image) > _LARGE_IMAGE_THRESHOLD:
            # 超大 data URI 用摘要键，避免全串 O(n) 哈希与比较阻塞事件循环。
            signature = (0.0, 0)
            key_image = await asyncio.to_thread(self._data_uri_digest, image)
        else:
            signature = (0.0, 0)
            key_image = image
        cache_key: PrepareCacheKey = (key_image, _roots_key, signature)

        cached = self._prepare_cache.get(cache_key)
        if cached is not None:
            self._prepare_cache.move_to_end(cache_key)
            return cached

        inflight = self._prepare_inflight.get(cache_key)
        if inflight is not None:
            # shield 隔离取消传播：等待者被取消时仅取消其自身 await 的 outer，
            # 底层共享 task 继续运行，保护其他等待者与缓存写入。
            return await asyncio.shield(inflight)

        task = asyncio.ensure_future(self._prepare_and_cache(image, cache_key))
        self._prepare_inflight[cache_key] = task
        # 检索共享 task 的异常结果：创建者被取消后 shield 的 outer 不再消费 task 结果，
        # 若 task 随后失败且无其他等待者，事件循环会告警 "Task exception was never
        # retrieved"；回调内显式检索并记录，消除噪音日志。
        task.add_done_callback(log_unretrieved_task_exception)
        # shield 隔离取消传播：创建者被取消时仅取消其自身 await 的 outer，底层共享
        # task 继续运行至完成，_prepare_inflight 由 task 完成时的 finally 清理。
        return await asyncio.shield(task)

    async def _prepare_and_cache(self, image: str, cache_key: PrepareCacheKey) -> str:
        """执行图像预处理并写入 LRU 缓存，供 single-flight 去重复用。

        inflight 在本 task 完成时清理；创建者被取消时 task 继续运行直至完成，
        保护共享同一 task 的其他等待者，避免连带取消。
        """
        from .image_input import prepare_image_input

        try:
            prepared = await prepare_image_input(image)
            # HTTP/HTTPS URL 经 prepare_image_input 统一校验后原样返回，缓存无收益
            # 反而占用 LRU 条目；data URI 与本地文件经解码或编码产生新值，仍照常缓存。
            if classify_image_reference(image) != "url":
                self._cache_prepared_result(cache_key, prepared)
            return prepared
        finally:
            self._prepare_inflight.pop(cache_key, None)

    def _cache_prepared_result(self, cache_key: PrepareCacheKey, prepared: str) -> None:
        """将预处理结果写入 LRU 缓存，按条目数与累计字节双重上限淘汰。

        单条结果加总后超过字节上限时跳过缓存，避免大图累积撑爆内存；条目超限时淘汰最久未用
        条目并同步扣减字节计数，保持计数与缓存内容一致。
        """
        size = len(prepared)
        # 单条结果自身超出字节上限时永不可缓存，直接跳过避免无意义清空整个缓存。
        if size > self._prepare_cache_max_bytes:
            return
        # 字节超限时先按 LRU 淘汰至可容纳，而非直接拒绝，避免大图场景缓存被早期
        # 条目占满后新条目无法入场，提升高频复用少量大参考图的命中率。
        while (
            self._prepare_cache_bytes + size > self._prepare_cache_max_bytes and self._prepare_cache
        ):
            _, evicted = self._prepare_cache.popitem(last=False)
            self._prepare_cache_bytes -= len(evicted)
        self._prepare_cache[cache_key] = prepared
        self._prepare_cache_bytes += size
        while len(self._prepare_cache) > self._prepare_cache_max:
            _, evicted = self._prepare_cache.popitem(last=False)
            self._prepare_cache_bytes -= len(evicted)

    async def prepare_images_in_parallel(self, images: Sequence[str]) -> list[str]:
        """受限并发预处理多张图片。"""
        from ..io.io_path import get_workspace_roots

        concurrency = max(1, self._prepare_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        # 批内预计算一次工作区键，避免每图重复读取 ContextVar 与构造元组。
        roots_key = tuple(str(r) for r in get_workspace_roots())

        async def _prepare_with_limit(image: str) -> str:
            async with semaphore:
                return await self.prepare_image_input(image, roots_key)

        tasks = [_prepare_with_limit(image) for image in images]
        return await asyncio.gather(*tasks)
