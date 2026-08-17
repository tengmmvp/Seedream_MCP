"""参考图预处理缓存子系统：LRU + single-flight 去重。

集中管理图像输入预处理结果的缓存与并发去重。本地文件签名复用 io_path 的越界判定
与 image_validation 的文件资格常量，确保签名与读取锁定同一文件，不因两侧规则漂移
命中陈旧缓存。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from typing import Sequence

from .image_ref import classify_image_reference
from .image_validation import resolve_local_image_candidate
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


class ImagePreparer:
    """参考图预处理缓存管理器，LRU + single-flight 去重。

    预处理结果按 (输入, workspace_roots, 本地文件签名) 缓存，避免并行请求对同一参考图
    重复读取与编码；同一键的并发 miss 复用同一在途 task。本地文件纳入 mtime+size 防内容
    替换返回陈旧编码。预处理并发上限为实例级全局约束，跨批量调用共享。
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
            prepare_concurrency: 预处理并发上限，为实例级全局约束。
        """
        self._prepare_cache: OrderedDict[PrepareCacheKey, str] = OrderedDict()
        self._prepare_cache_max = prepare_cache_max
        self._prepare_cache_max_bytes = prepare_cache_max_bytes
        self._prepare_cache_bytes = 0
        self._prepare_inflight: dict[PrepareCacheKey, asyncio.Task[str]] = {}
        self._prepare_concurrency = prepare_concurrency
        # 信号量为实例级并在全部预处理入口（单图与批量）间共享，使配置的并发上限在
        # 并行生成、并发工具调用与单图直连叠加时仍是全局上限。asyncio.Semaphore 在
        # 首次使用时绑定事件循环，跨循环复用会报错，故持循环身份守卫按需重建。
        self._prepare_semaphore: asyncio.Semaphore | None = None
        self._prepare_semaphore_loop: asyncio.AbstractEventLoop | None = None

    def _get_prepare_semaphore(self) -> asyncio.Semaphore:
        """返回绑定当前事件循环的实例级预处理信号量，循环变化时重建。

        检查与重建之间无 await 点，同一事件循环内不存在竞态；preparer 跨事件循环
        依次复用（如测试进程内多次 asyncio.run）时按新循环重建，语义等价于新实例。
        """
        loop = asyncio.get_running_loop()
        if self._prepare_semaphore is None or self._prepare_semaphore_loop is not loop:
            self._prepare_semaphore = asyncio.Semaphore(max(1, self._prepare_concurrency))
            self._prepare_semaphore_loop = loop
        return self._prepare_semaphore

    @staticmethod
    def _local_file_signature(image: str, workspace_roots: tuple[str, ...]) -> tuple[float, int]:
        """计算图像输入的缓存签名。

        本地文件返回 (mtime, size) 参与缓存键，内容替换后失效避免返回陈旧编码；
        URL 与 data URI 内容由字符串决定、无法定位文件时返回 (0.0, 0)。

        候选定位委托 image_validation.resolve_local_image_candidate，与
        utils.image_input._prepare_local_image 的实际读取路径共用同一规则，签名与
        读取锁定同一文件，不会因两侧规则漂移命中陈旧缓存。

        残余风险：签名基于 mtime+size 而非内容哈希，同信任域内具备本地写权限者可在
        替换文件内容后用 os.utime 还原签名命中陈旧缓存；信任边界依赖 workspace Roots
        声明，Roots 授权目录内的主体视为同域，该投毒不构成跨域越权。

        首尾空白与读取路径统一先 strip：_prepare_local_image 以 strip 后路径定位文件，
        签名路径同样 strip 后定位，两条路径对同一物理文件求签名，带空白前缀的输入
        不会因签名恒为 (0.0, 0) 架空 mtime+size 失效保护。
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

        摘要取 128-bit（32 hex）：64-bit 截断的生日碰撞界约 2^32 次哈希即进入可行域，
        蓄意构造碰撞可令缓存命中返回他人输入；128-bit 将构造成本推出可行域，长度
        增量可忽略。encode 以 replace 容错，未配对代理字符不在此抛
        UnicodeEncodeError；此类非法输入随后在 validate_image_input 的 base64
        解码处按参数级校验报错，与 image_validation 的 Base64 解码失败口径一致，
        批量路径不因编码异常整批中断。
        """
        digest = hashlib.sha256(image.encode("utf-8", errors="replace")).hexdigest()
        return "sha256:" + digest[:32]

    async def prepare_image_input(
        self, image: str, _roots_key: tuple[str, ...] | None = None
    ) -> str:
        """准备图像输入数据。

        将图像 URL 或本地文件路径转换为 API 所需格式。结果按 (输入, workspace_roots,
        本地文件签名) 缓存，避免并行请求对同一参考图重复读取与编码，并以工作区隔离键
        避免跨租户命中；本地文件纳入 mtime+size 防内容替换返回陈旧编码。缓存超限按 LRU
        淘汰；同一键的并发 miss 复用同一在途 task 实现 single-flight 去重。

        并发上限由实例级信号量约束：单图入口（client 直连）与批量入口共用同一
        全局上限，并行生成与并发工具调用叠加时总并发不超过配置的
        prepare_concurrency。槽位经 _PrepareSemaphoreSlot 管理：创建者被取消且共享
        task 仍在运行时，释放责任转移给 task 完成回调，脱缰 task 结束前持续占用
        并发额度，取消风暴不会突破上限。

        Args:
            image: 图像输入字符串，三类来源的归一化语义与模块级函数一致。
            _roots_key: 工作区隔离键；批量路径预计算共享，None 时按当前请求 Roots 现取。

        Returns:
            归一化后的图像输入，本地文件为 Base64 Data URI。

        Raises:
            SeedreamValidationError: 输入格式无效、路径越界或维度超限等参数校验失败。
            SeedreamAPIError: 会话未授权工作区目录或图像处理其他失败。
        """
        semaphore = self._get_prepare_semaphore()
        await semaphore.acquire()
        slot = _PrepareSemaphoreSlot(semaphore)
        try:
            return await self._prepare_image_input_locked(image, _roots_key, slot)
        finally:
            slot.release()

    async def _prepare_image_input_locked(
        self, image: str, _roots_key: tuple[str, ...] | None, slot: _PrepareSemaphoreSlot
    ) -> str:
        """执行图像预处理的缓存检索与执行，调用方已持有实例级信号量槽位。

        签名计算、缓存命中与 single-flight 逻辑均在本实现体内，prepare_image_input
        仅负责信号量守卫；批量路径经公共入口逐图进入，与单图直连共享同一守卫。创建者
        在 shield 等待共享 task 时被取消的，槽位经 slot 转移给 task 本体释放。
        """
        if _roots_key is None:
            from ..io.io_path import get_workspace_roots

            _roots_key = tuple(str(r) for r in get_workspace_roots())
        # URL/data-URI 无本地文件 I/O，直接用空签名短路；本地文件签名含同步 stat/resolve，
        # 移至工作线程避免网络挂载工作区下阻塞事件循环。分类前先 strip，与
        # prepare_image_input 入口及 _local_file_signature 的口径一致，防止前导空白
        # 使 URL/data URI 误判为本地路径。
        image = image.strip()
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
            try:
                return await asyncio.shield(inflight)
            except asyncio.CancelledError:
                # 等待者放弃消费后若再无其他等待者接手，task 失败将无人检索异常，
                # 登记回调兜底记录；task 成功或被取消时回调静默。
                arm_unretrieved_exception_logging(inflight)
                raise

        task = asyncio.ensure_future(self._prepare_and_cache(image, cache_key))
        self._prepare_inflight[cache_key] = task
        try:
            # shield 隔离取消传播：创建者被取消时仅取消其自身 await 的 outer，底层共享
            # task 继续运行至完成，_prepare_inflight 由 task 完成时的 finally 清理。
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # 创建者被取消而共享 task 脱缰继续运行：若随创建者 finally 释放本槽位，
            # 取消次数会无界叠加突破并发上限。把释放责任转移给 task 本体，task 结束前
            # 槽位保持占用，新请求与等待者继续受限。
            slot.transfer_to_task(task)
            # 创建者放弃消费后若再无等待者接手，task 失败将无人检索异常，登记回调兜底
            # 记录；常规失败路径的异常由等待者消费并经既有错误通道记录，不登记。
            arm_unretrieved_exception_logging(task)
            raise

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
            # 判定与读取路径同样以 strip 后输入分类，带空白前缀的 URL 不误入缓存。
            if classify_image_reference(image.strip()) != "url":
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
        """受限并发预处理多张图片。

        每图经公共 prepare_image_input 入口进入，批内并发上限即实例级信号量约束的
        配置值；同一 preparer 上并发的多个批量调用与单图直连调用共享同一全局上限，
        并行生成与 streamable-http 并发工具调用不会叠加突破 prepare_concurrency 上限。

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
