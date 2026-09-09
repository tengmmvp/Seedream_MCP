"""参考图预处理缓存子系统：LRU + single-flight 去重。

集中管理图像输入预处理结果的缓存与并发去重。本地输入的缓存签名与读取链复用
image_validation 的候选定位，签名与实际读取锁定同一文件，不因规则漂移命中陈旧
缓存。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path

from .image_input import local_candidate_scope, prepare_image_input
from .image_ref import classify_image_reference, require_image_str
from .image_validation import LocalImageCandidate, resolve_local_image_candidate
from ..core.inflight import InflightEntry
from ..io.io_path import get_read_scope, resolve_images_root

# 预处理缓存键：(image 字符串, 读权限字符串元组, 本地文件 mtime+size 签名)；URL
# 与 Data URI 的归一化结果是输入的纯函数，读权限隔离元组恒为空。
PrepareCacheKey = tuple[str, tuple[str, ...], tuple[float, int]]

# 批内共享的读取上下文：(图片目录, 读权限列表)，供缓存键隔离与签名定位一次求值。
ReadContext = tuple[Path, list[Path]]


def _current_read_context() -> tuple[tuple[str, ...], Path, list[Path]]:
    """一次求值 (读权限字符串元组, 图片目录, 读权限列表)，供本地输入共享。

    含首次 resolve 的文件系统调用，调用方在工作线程执行。
    """
    scope = get_read_scope()
    return tuple(str(r) for r in scope), resolve_images_root(), scope


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
        del task  # 回调签名要求接收 task，释放逻辑不使用。
        self._semaphore.release()


class ImagePreparer:
    """参考图预处理缓存管理器，LRU + single-flight 去重。

    预处理结果按 (输入, 读权限, 本地文件签名) 缓存，本地文件纳入
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
        self._prepare_inflight: dict[PrepareCacheKey, InflightEntry[str]] = {}
        self._prepare_concurrency = prepare_concurrency
        # asyncio.Semaphore 首次使用时绑定事件循环，跨循环复用会报错，持循环身份
        # 守卫按需重建并同步清空在途登记。
        self._prepare_semaphore: asyncio.Semaphore | None = None
        self._prepare_semaphore_loop: asyncio.AbstractEventLoop | None = None

    def _get_prepare_semaphore(self) -> asyncio.Semaphore:
        """返回绑定当前事件循环的预处理信号量，循环更替时重建并清空在途登记。

        旧循环登记的在途 task 绑定旧循环且永不完成，死条目会使新循环的同键等待
        永久挂起，随重建一并清空，同键调用按 miss 重新执行。检查与重建之间无
        await 点，同一事件循环内不存在竞态；preparer 跨事件循环依次复用时按新
        循环重建，语义等价于新实例。
        """
        loop = asyncio.get_running_loop()
        if self._prepare_semaphore is None or self._prepare_semaphore_loop is not loop:
            self._prepare_semaphore = asyncio.Semaphore(max(1, self._prepare_concurrency))
            self._prepare_semaphore_loop = loop
            self._prepare_inflight.clear()
        return self._prepare_semaphore

    @staticmethod
    def _local_candidate(
        image: str, images_root: Path | None = None, read_scope: list[Path] | None = None
    ) -> LocalImageCandidate | None:
        """定位本地输入的候选文件，返回 (resolve 后物理路径, stat)。

        URL、data URI 与无法定位文件的输入返回 None，缓存签名由调用方从 stat
        派生。候选定位与 image_input 的读取路径共用 resolve_local_image_candidate，
        签名与读取锁定同一文件；images_root 与 read_scope 未提供时现取，批内调用传入
        共享上下文消除逐图重复求值。

        残余风险：签名基于 mtime+size 而非内容哈希，同信任域内具备本地写权限者可在
        替换内容后用 os.utime 还原签名命中陈旧缓存；读权限目录内的主体视为同域，
        不构成跨域越权。先 strip 再定位，与读取路径口径一致。
        """
        image = image.strip()
        if classify_image_reference(image) != "local":
            return None

        return resolve_local_image_candidate(image, images_root=images_root, read_scope=read_scope)

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
        self,
        image: str,
        *,
        scope_key: tuple[str, ...] | None = None,
        read_context: ReadContext | None = None,
    ) -> str:
        """准备图像输入数据，将图像 URL、Data URI 或本地文件路径归一化为 API 所需格式。

        结果按 (输入, 读权限, 本地文件签名) 缓存，以读权限隔离键避免跨租户
        命中；同一键的并发 miss 复用同一在途 task（single-flight），缓存超限按 LRU
        淘汰。并发上限由实例级信号量约束，仅实际执行预处理的调用占用槽位，缓存命中
        与在途等待在槽外完成；创建者被取消而共享 task 仍在运行时，槽位释放责任转移
        给 task 完成回调。本地输入的首次定位候选经上下文传入读取链复用，签名与读取
        锁定同一文件。

        Args:
            image: 图像输入字符串，三类来源的归一化语义与模块级函数一致。
            scope_key: 本地输入的读权限隔离键；仅内部批量路径预计算共享传入，
                None 时按当前请求现取，非本地输入不消费该键。
            read_context: 本地输入的 (图片目录, 读权限) 共享上下文；仅内部批量路径
                预计算共享传入，None 时与隔离键一并现取。

        Returns:
            归一化后的图像输入，本地文件为 Base64 Data URI。

        Raises:
            SeedreamValidationError: 输入格式无效、路径越界、维度超限或图像内容
                处理失败等调用方输入问题。
        """
        # 循环更替检测先于在途检查执行，旧循环遗留的死条目不再拦截同键调用。
        self._get_prepare_semaphore()
        cache_key, normalized_image, candidate = await self._resolve_cache_key(
            image, scope_key, read_context
        )

        cached = self._prepare_cache.get(cache_key)
        if cached is not None:
            self._prepare_cache.move_to_end(cache_key)
            return cached

        # 在途检查在获取信号量前完成，等待者不占并发槽位。
        inflight = self._prepare_inflight.get(cache_key)
        if inflight is not None:
            return await inflight.consume()

        semaphore = self._get_prepare_semaphore()
        await semaphore.acquire()
        slot = _PrepareSemaphoreSlot(semaphore)
        try:
            # 候选经作用域注入在途 task 的读取链，task 创建时复制当前上下文取到本
            # 调用的候选；作用域退出即复位，值不泄漏进调用方的后续调用。
            async with local_candidate_scope(candidate):
                return await self._prepare_image_input_locked(normalized_image, cache_key, slot)
        finally:
            slot.release()

    async def _resolve_cache_key(
        self,
        image: str,
        scope_key: tuple[str, ...] | None,
        read_context: ReadContext | None,
    ) -> tuple[PrepareCacheKey, str, LocalImageCandidate | None]:
        """计算图像输入的缓存键并返回 strip 后输入与本地候选，不持有并发槽位。

        缓存命中与在途等待路径不进入信号量，键计算须在槽外完成；本地文件定位含
        同步 stat/resolve，读取上下文现取与大输入的摘要计算含 O(n) 哈希，均移至
        工作线程避免阻塞事件循环。先 strip 再分类，与 _local_candidate 口径
        一致，防止前导空白使 URL 或 data URI 误判为本地路径。读权限隔离键
        仅本地输入求值：URL 与 Data URI 的归一化结果是输入的纯函数，隔离元组恒为
        空，纯远端输入不因数据根目录声明不可解析而在预处理阶段失败。

        Returns:
            (缓存键, strip 后输入, 本地候选) 三元组，非本地与未定位到文件的输入
            候选为 None。
        """
        image = require_image_str(image).strip()
        ref_kind = classify_image_reference(image)
        candidate: LocalImageCandidate | None = None
        signature: tuple[float, int]
        if ref_kind == "local":
            if scope_key is None or read_context is None:
                computed_key, images_root, scope = await asyncio.to_thread(_current_read_context)
                if scope_key is None:
                    scope_key = computed_key
                if read_context is None:
                    read_context = (images_root, scope)
            candidate = await asyncio.to_thread(
                self._local_candidate, image, read_context[0], read_context[1]
            )
            if candidate is None:
                signature = (0.0, 0)
            else:
                _, st = candidate
                signature = (st.st_mtime, st.st_size)
            key_image = image
        else:
            scope_key = ()
            signature = (0.0, 0)
            if len(image) > _LARGE_IMAGE_THRESHOLD:
                key_image = await asyncio.to_thread(self._data_uri_digest, image)
            else:
                key_image = image
        cache_key: PrepareCacheKey = (key_image, scope_key, signature)
        return cache_key, image, candidate

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
            return await inflight.consume()

        task = asyncio.ensure_future(self._prepare_and_cache(image, cache_key))
        # 共享 task 供后到等待者复用，注册表在 task 完成时的 finally 清理。
        entry = InflightEntry(task)
        self._prepare_inflight[cache_key] = entry

        def _transfer_slot() -> None:
            # 创建者被取消而 task 脱缰继续运行时，释放责任转移给 task 本体，防止
            # 取消叠加突破并发上限。
            slot.transfer_to_task(task)

        return await entry.consume(on_cancel=_transfer_slot)

    async def _prepare_and_cache(self, image: str, cache_key: PrepareCacheKey) -> str:
        """执行图像预处理并写入 LRU 缓存，供 single-flight 去重复用。

        inflight 在本 task 完成时清理；创建者被取消时 task 继续运行直至完成，
        保护共享同一 task 的其他等待者，避免连带取消。
        """
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
        # 批内预计算一次读权限键与读取上下文，避免每图重复读取 ContextVar、构造
        # 元组与逐图求值；仅存在本地输入时求值，纯远端批次不依赖数据根目录声明的可解
        # 析性。求值含首次 resolve 的文件系统调用，下沉工作线程。非 str 元素与
        # 单图入口同口径归校验错误。
        for image in images:
            require_image_str(image)
        stripped_images = [image.strip() for image in images]
        if any(classify_image_reference(item) == "local" for item in stripped_images):
            scope_key, images_root, scope = await asyncio.to_thread(_current_read_context)
            read_context: ReadContext | None = (images_root, scope)
        else:
            scope_key = ()
            read_context = None

        tasks = [
            asyncio.ensure_future(
                self.prepare_image_input(image, scope_key=scope_key, read_context=read_context)
            )
            for image in stripped_images
        ]
        try:
            return await asyncio.gather(*tasks)
        except Exception:
            # 首个失败即取消未完成的兄弟任务，快速让出并发额度；已完成任务的缓存
            # 仍有效。调用方取消由 gather 自带传播，不经本分支。
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
