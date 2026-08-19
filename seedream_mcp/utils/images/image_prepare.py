"""参考图预处理缓存子系统：LRU + single-flight 去重。

集中管理图像输入预处理结果的缓存与并发去重。本地文件签名复用 image_validation
的候选定位，签名与实际读取锁定同一文件，不因规则漂移命中陈旧缓存。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from typing import Callable, Sequence

from .image_ref import classify_image_reference
from .image_validation import resolve_local_image_candidate
from ..core import logs as core_logs
from ..core.logs import arm_unretrieved_exception_logging, get_logger
from ..io.io_path import resolve_workspace_roots

logger = get_logger(__name__)

# 预处理缓存键：(image 字符串, workspace_roots 字符串元组, 本地文件 mtime+size 签名)
PrepareCacheKey = tuple[str, tuple[str, ...], tuple[float, int]]

# 超过此长度的非本地输入改用摘要键，避免大 data URI 的 O(n) 哈希与比较阻塞事件循环。
_LARGE_IMAGE_THRESHOLD = 1024 * 1024


class _PrepareSemaphoreSlot:
    """预处理并发槽位的独占释放句柄，保证槽位恰好释放一次。

    正常与异常路径由持有者在 finally 中释放；创建者被取消而共享 task 仍在运行时，
    释放责任经 transfer_to_task 转移给该 task 的完成回调。转移后脱缰 task 在结束前
    持续占用并发额度，取消次数不再无界叠加突破 prepare_concurrency 上限。
    """

    __slots__ = ("_semaphore", "_released")

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    def release(self) -> None:
        """释放槽位，重复调用仅首次生效。"""
        if self._released:
            return
        self._released = True
        self._semaphore.release()

    def transfer_to_task(self, task: "asyncio.Task[str]") -> None:
        """把槽位释放责任转移给 task，由 task 完成回调释放。

        task 已完成时完成回调经事件循环尽快调度，仍保证恰好一次释放。
        """
        if self._released:
            return
        self._released = True
        task.add_done_callback(self._release_when_task_done)

    def _release_when_task_done(self, task: "asyncio.Task[str]") -> None:
        """task 完成回调，释放经转移的槽位，恰好执行一次由转移语义保证。"""
        del task
        self._semaphore.release()


class _PrepareInflight:
    """single-flight 在途条目：共享 task 与活动消费者计数。

    消费者 await 期间登记计数、finally 释放；任一消费者经 shield 收到结果或异常时
    置位 observed。兜底日志仅在计数归零且结果未被消费时登记，避免同一异常重复
    入日志。
    """

    __slots__ = ("task", "consumers", "observed")

    def __init__(self, task: "asyncio.Task[str]") -> None:
        self.task = task
        self.consumers = 0
        self.observed = False


class ImagePreparer:
    """参考图预处理缓存管理器，LRU + single-flight 去重。

    预处理结果按 (输入, workspace_roots, 本地文件签名) 缓存，本地文件纳入
    mtime+size 防内容替换返回陈旧编码；同一键的并发 miss 复用同一在途 task。
    并发上限为实例级全局约束，跨批量调用共享；仅实际执行预处理的调用占用并发
    槽位，缓存命中与在途等待在槽外完成。
    """

    def __init__(
        self,
        prepare_cache_max: int,
        prepare_cache_max_bytes: int,
        prepare_concurrency: int,
    ) -> None:
        """初始化预处理缓存与并发约束。

        Args:
            prepare_cache_max: LRU 缓存条目数上限。
            prepare_cache_max_bytes: 缓存累计字节上限。
            prepare_concurrency: 预处理并发上限。
        """
        self._prepare_cache: OrderedDict[PrepareCacheKey, str] = OrderedDict()
        self._prepare_cache_max = prepare_cache_max
        self._prepare_cache_max_bytes = prepare_cache_max_bytes
        self._prepare_cache_bytes = 0
        self._prepare_inflight: dict[PrepareCacheKey, _PrepareInflight] = {}
        self._prepare_concurrency = prepare_concurrency
        # asyncio.Semaphore 首次使用时绑定事件循环，跨循环复用会报错，持循环身份
        # 守卫按需重建。
        self._prepare_semaphore: asyncio.Semaphore | None = None
        self._prepare_semaphore_loop: asyncio.AbstractEventLoop | None = None

    def _get_prepare_semaphore(self) -> asyncio.Semaphore:
        """返回绑定当前事件循环的实例级预处理信号量，循环变化时重建。

        检查与重建之间无 await 点，同一事件循环内不存在竞态；preparer 跨事件循环
        依次复用时按新循环重建，语义等价于新实例。
        """
        loop = asyncio.get_running_loop()
        if self._prepare_semaphore is None or self._prepare_semaphore_loop is not loop:
            self._prepare_semaphore = asyncio.Semaphore(max(1, self._prepare_concurrency))
            self._prepare_semaphore_loop = loop
        return self._prepare_semaphore

    @staticmethod
    def _local_file_signature(image: str, workspace_roots: tuple[str, ...]) -> tuple[float, int]:
        """计算图像输入的缓存签名。

        本地文件返回 (mtime, size)，内容替换后失效避免返回陈旧编码；URL、data URI
        与无法定位文件的输入返回 (0.0, 0)。候选定位与 image_input 的实际读取路径
        共用 resolve_local_image_candidate，签名与读取锁定同一文件。

        残余风险：签名基于 mtime+size 而非内容哈希，同信任域内具备本地写权限者可在
        替换内容后用 os.utime 还原签名命中陈旧缓存；Roots 授权目录内的主体视为同域，
        不构成跨域越权。先 strip 再定位，与读取路径口径一致。
        """
        image = image.strip()
        if classify_image_reference(image) != "local":
            return (0.0, 0)

        found = resolve_local_image_candidate(image, resolve_workspace_roots(workspace_roots))
        if found is None:
            return (0.0, 0)
        _, st = found
        return (st.st_mtime, st.st_size)

    @staticmethod
    def _data_uri_digest(image: str) -> str:
        """计算非本地图像输入的摘要键，供超大输入替代全串作缓存键。

        摘要取 sha256 前 32 hex（128-bit）：64-bit 截断的生日碰撞界约 2^32 次哈希即
        进入可行域，蓄意碰撞可令缓存命中返回他人输入，128-bit 将构造成本推出可行
        域。encode 以 replace 容错，未配对代理字符的非法输入随后在 base64 解码处按
        参数校验报错，批量路径不因编码异常整批中断。
        """
        digest = hashlib.sha256(image.encode("utf-8", errors="replace")).hexdigest()
        return "sha256:" + digest[:32]

    async def prepare_image_input(
        self, image: str, _roots_key: tuple[str, ...] | None = None
    ) -> str:
        """准备图像输入数据，将图像 URL、Data URI 或本地文件路径归一化为 API 所需格式。

        结果按 (输入, workspace_roots, 本地文件签名) 缓存，以工作区隔离键避免跨租户
        命中；同一键的并发 miss 复用同一在途 task（single-flight），缓存超限按 LRU
        淘汰。并发上限由实例级信号量约束，仅实际执行预处理的调用占用槽位，缓存命中
        与在途等待在槽外完成；创建者被取消而共享 task 仍在运行时，槽位释放责任转移
        给 task 完成回调。

        Args:
            image: 图像输入字符串，三类来源的归一化语义与模块级函数一致。
            _roots_key: 工作区隔离键；批量路径预计算共享，None 时按当前请求 Roots 现取。

        Returns:
            归一化后的图像输入，本地文件为 Base64 Data URI。

        Raises:
            SeedreamValidationError: 输入格式无效、路径越界、维度超限或图像内容
                处理失败等调用方输入问题。
            SeedreamConfigError: 会话未授权工作区目录且无环境回退根。
        """
        cache_key, normalized_image = await self._resolve_cache_key(image, _roots_key)

        cached = self._prepare_cache.get(cache_key)
        if cached is not None:
            self._prepare_cache.move_to_end(cache_key)
            return cached

        # 在途检查在获取信号量前完成，等待者不占并发槽位。
        inflight = self._prepare_inflight.get(cache_key)
        if inflight is not None:
            return await self._await_inflight(inflight)

        semaphore = self._get_prepare_semaphore()
        await semaphore.acquire()
        slot = _PrepareSemaphoreSlot(semaphore)
        try:
            return await self._prepare_image_input_locked(normalized_image, cache_key, slot)
        finally:
            slot.release()

    async def _resolve_cache_key(
        self, image: str, _roots_key: tuple[str, ...] | None
    ) -> tuple[PrepareCacheKey, str]:
        """计算图像输入的缓存键并返回 strip 后的输入，不持有并发槽位。

        缓存命中与在途等待路径不进入信号量，键计算须在槽外完成；本地文件签名含
        同步 stat/resolve，大输入的摘要计算含 O(n) 哈希，均移至工作线程避免阻塞
        事件循环。先 strip 再分类，与 _local_file_signature 口径一致，防止前导
        空白使 URL 或 data URI 误判为本地路径。

        Returns:
            (缓存键, strip 后输入) 二元组。
        """
        if _roots_key is None:
            from ..io.io_path import get_workspace_roots

            _roots_key = tuple(str(r) for r in get_workspace_roots())
        image = image.strip()
        ref_kind = classify_image_reference(image)
        if ref_kind == "local":
            signature = await asyncio.to_thread(self._local_file_signature, image, _roots_key)
            key_image = image
        elif len(image) > _LARGE_IMAGE_THRESHOLD:
            signature = (0.0, 0)
            key_image = await asyncio.to_thread(self._data_uri_digest, image)
        else:
            signature = (0.0, 0)
            key_image = image
        cache_key: PrepareCacheKey = (key_image, _roots_key, signature)
        return cache_key, image

    async def _await_inflight(
        self, entry: _PrepareInflight, on_cancel: Callable[[], None] | None = None
    ) -> str:
        """以消费者身份在并发槽位外等待在途 task。

        shield 隔离取消传播：本调用被取消时仅取消自身 await，底层共享 task 继续
        运行；on_cancel 在取消异常向外传播前执行，供创建者转移槽位释放责任。消费
        者计数在 await 期间登记，结果或异常送达时置位 observed；放弃等待且计数归零
        时经 _arm_if_abandoned 登记兜底日志回调。
        """
        entry.consumers += 1
        try:
            try:
                result = await asyncio.shield(entry.task)
            except asyncio.CancelledError:
                if on_cancel is not None:
                    on_cancel()
                raise
            except Exception:
                # 异常经 shield 送达本消费者，置位已消费标记，不再登记兜底日志。
                entry.observed = True
                raise
            entry.observed = True
            return result
        finally:
            entry.consumers -= 1
            self._arm_if_abandoned(entry)

    def _arm_if_abandoned(self, entry: _PrepareInflight) -> None:
        """最后一个潜在消费者放弃且结果未被消费时，登记兜底日志回调。

        计数归零后 task 若失败将无人检索异常，经 logs 的登记入口挂兜底回调；回调
        触发时复查计数与已消费标记，登记后前提可能已不成立，复查失败即静默跳过，
        避免同一异常重复入日志。
        """

        def _log_if_orphaned(task: "asyncio.Task[str]") -> None:
            # 触发时经模块属性解析通用记录函数，保持调用方可替换该实现做测试观测。
            if entry.consumers == 0 and not entry.observed:
                core_logs.log_unretrieved_task_exception(task)

        if entry.consumers > 0 or entry.observed:
            return
        arm_unretrieved_exception_logging(entry.task, _log_if_orphaned)

    async def _prepare_image_input_locked(
        self, image: str, cache_key: PrepareCacheKey, slot: _PrepareSemaphoreSlot
    ) -> str:
        """创建并等待预处理 task，调用方已持有实例级信号量槽位。

        获取信号量的等待窗口内，同键先完成者可能已写缓存，或另一创建者已登记在途
        task，故先复查缓存与在途注册表：命中缓存直接返回，命中在途则归还槽位改以
        纯等待者身份在槽外等待，并发满载时后到者不重复执行全量读盘与编码。miss 时
        创建 _prepare_and_cache task 登记在途注册表并经 shield 等待，创建者被取消
        时槽位经 on_cancel 回调转移给 task 本体释放。
        """
        cached = self._prepare_cache.get(cache_key)
        if cached is not None:
            # 等待信号量期间先完成者已写缓存。
            self._prepare_cache.move_to_end(cache_key)
            return cached

        inflight = self._prepare_inflight.get(cache_key)
        if inflight is not None:
            # 等待信号量期间他人已登记同键，归还槽位转为纯等待者。
            slot.release()
            return await self._await_inflight(inflight)

        task = asyncio.ensure_future(self._prepare_and_cache(image, cache_key))
        # 共享 task 供后到等待者复用，注册表在 task 完成时的 finally 清理。
        entry = _PrepareInflight(task)
        self._prepare_inflight[cache_key] = entry

        def _transfer_slot() -> None:
            # 创建者被取消而 task 脱缰继续运行时，释放责任转移给 task 本体，防止
            # 取消叠加突破并发上限。
            slot.transfer_to_task(task)

        return await self._await_inflight(entry, on_cancel=_transfer_slot)

    async def _prepare_and_cache(self, image: str, cache_key: PrepareCacheKey) -> str:
        """执行图像预处理并写入 LRU 缓存，供 single-flight 去重复用。

        inflight 在本 task 完成时清理；创建者被取消时 task 继续运行直至完成，
        保护共享同一 task 的其他等待者，避免连带取消。
        """
        from .image_input import prepare_image_input

        try:
            prepared = await prepare_image_input(image)
            # URL 校验后原样返回，缓存无收益反而占用 LRU 条目，故不缓存；data URI
            # 与本地文件经解码或编码产生新值，照常缓存。
            if classify_image_reference(image.strip()) != "url":
                self._cache_prepared_result(cache_key, prepared)
            return prepared
        finally:
            self._prepare_inflight.pop(cache_key, None)

    def _cache_prepared_result(self, cache_key: PrepareCacheKey, prepared: str) -> None:
        """将预处理结果写入 LRU 缓存，按条目数与累计字节双重上限淘汰。"""
        size = len(prepared)
        # 单条结果自身超出字节上限时永不可缓存，直接跳过避免无意义清空整个缓存。
        if size > self._prepare_cache_max_bytes:
            return
        # 字节超限先按 LRU 淘汰至可容纳而非直接拒绝，提升少量大参考图的复用命中。
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
        """受限并发预处理多张图片。

        每图经公共 prepare_image_input 入口进入，与其他批量及单图调用共享实例级
        并发上限。

        Args:
            images: 图像输入字符串序列。

        Returns:
            与入参顺序一致的归一化结果列表。

        Raises:
            SeedreamMCPError: 任一图像预处理失败时抛出，与单图入口的异常语义一致。
        """
        from ..io.io_path import get_workspace_roots

        # 批内预计算一次工作区键，避免每图重复读取 ContextVar 与构造元组。
        roots_key = tuple(str(r) for r in get_workspace_roots())

        tasks = [self.prepare_image_input(image, roots_key) for image in images]
        return await asyncio.gather(*tasks)
