"""生成图片的缩略图预览。

从自动保存落盘的图片生成工具结果携带的 ImageContent 预览：长边不超过
THUMBNAIL_MAX_EDGE 像素的 JPEG，体积远小于原图，多图结果的协议消息不因预览显著
膨胀。解码像素上限经 formats 的幂等注册无条件设置进程级 PIL
MAX_IMAGE_PIXELS，不依赖调用方先经过参考图校验。单张生成失败安全跳过，不影响
其余图片与工具结果本身。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import stat
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from mcp.types import ImageContent

from ...definitions.conventions import MAX_IMAGE_PIXELS
from ..core.executors import CPU_OFFLOAD_DECODE_SLOTS, run_in_cpu_pool
from ..core.inflight import TransientInflightError
from ..core.logs import get_logger
from ..core.loop_bound import LoopKeyedInflight, loop_bound_semaphore
from ..core.throttles import ThrottleRegistry
from ..io.files import atomic_replace_from_fd_sync, open_no_follow_read

if TYPE_CHECKING:
    from PIL import Image

logger = get_logger()

# 预览缩略图规格：典型取值下单张约几十 KB，十张量级的结果总载荷保持 MB 级以内。
THUMBNAIL_MAX_EDGE = 768
THUMBNAIL_JPEG_QUALITY = 80
THUMBNAIL_MIME_TYPE = "image/jpeg"

# 缩略图落盘缓存：与图片目录并列位于 <数据根目录>/.seedream/thumbs，按源图
# (路径, mtime, size) 寻址，命中免除重复解码；缓存文件名不带图片扩展名，不进入
# 目录扫描与图片目录清理配额的视野；累计字节超上界按最旧驱逐，写失败静默降级为
# 每次现生成。
THUMBNAIL_CACHE_DIR_NAME = "thumbs"
THUMBNAIL_CACHE_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_THUMB_SWEEP_INTERVAL_SECONDS = 60.0
# 落盘骨架 .thumb-tmp 的清扫宽限秒数：写入秒级完成，超宽限期仍存在的是进程
# 中断遗留的孤儿，照常纳入驱逐。
_THUMB_TMP_GRACE_SECONDS = 600.0
# 节流门键控的缓存根数上限，旋转 Roots 的长寿命进程持续换根时按 LRU 驱逐防无限增长。
_THUMB_SWEEP_ROOTS_MAX = 16
# 节流门按缓存根键控，多数据根并存时一处清扫不压制另一处的驱逐。
_thumb_sweep_after: ThrottleRegistry[Path] = ThrottleRegistry(_THUMB_SWEEP_ROOTS_MAX)
_thumb_sweep_lock = threading.Lock()

# 预览张数上限：组图与并行的合法组合可达 150 张，全量内嵌会使单条 CallToolResult
# 膨胀至数 MB 以上。
PREVIEW_MAX_IMAGES = 10


def _get_decode_semaphore() -> asyncio.Semaphore:
    """返回绑定当前事件循环的进程级解码限流信号量，事件循环更替时重建。"""
    return loop_bound_semaphore(CPU_OFFLOAD_DECODE_SLOTS, key="preview_decode")


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """把任意模式的图片归一为不带透明通道的 RGB。

    JPEG 不支持透明通道：带透明波段或带 transparency 元信息的模式合成白色背景，
    其余模式直接转换，透明区域在预览中呈白底而非丢失通道后发黑。
    """
    from PIL import Image

    if "A" in image.getbands() or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


class _DecodeOutcome(NamedTuple):
    """单次缩略图解码的结果与失败原因，供调用方跳过告警携带原因。"""

    data: bytes | None
    failure_reason: str | None


def _build_thumbnail_outcome(image_path: Path) -> _DecodeOutcome:
    """解码单张源图生成缩略图，失败在源头留痕并把原因带给调用方。

    Raises:
        FileNotFoundError: 源图缺失，与生成失败分档供调用方映射。
    """
    # PIL 惰性导入，首载含解码器注册，落点在工作线程而非事件循环。
    from PIL import Image, ImageOps

    from ..core.formats import ensure_image_decoders_ready

    ensure_image_decoders_ready()

    try:
        # 文件对象直接交给 PIL 保持惰性流式解码，源图不整读为全量内存拷贝；
        # 符号链接与 FIFO 等非常规文件在打开时拒绝。
        with open_no_follow_read(image_path) as f, Image.open(f) as image:
            # 显式头尺寸校验：PIL 全局阈值仅对 2 倍上限以上的声明抛错，1 至 2 倍
            # 区间只告警，本路径与参考图校验同口径显式拒绝，不依赖落盘侧防线。
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                logger.warning(
                    "缩略图源图像素 {}x{} 超过上限 {}，跳过: {}",
                    width,
                    height,
                    MAX_IMAGE_PIXELS,
                    image_path.name,
                )
                return _DecodeOutcome(None, None)
            # JPEG 先请求 draft 缩尺解码：解码器按请求尺寸缩减采样，只解码必要
            # 分辨率的像素，解码量下降约 4 至 16 倍；请求尺寸取上限的两倍，为后续
            # 缩放保留质量余量。draft 须在像素数据加载前调用才生效，对非 JPEG
            # 格式为无害空操作，格式判定仅为显式表达意图。
            if image.format == "JPEG":
                image.draft("RGB", (THUMBNAIL_MAX_EDGE * 2, THUMBNAIL_MAX_EDGE * 2))
            # EXIF 方向在 flatten 前归一，透明合成与缩放基于物理方向。仅携带非
            # 默认方向标签的图片才做转置，其余图片省去 exif_transpose 的全分辨率
            # 拷贝。draft 只缩减解码分辨率，不改写 EXIF 元数据，方向判定在其后
            # 仍然可靠。274 为 EXIF Orientation 标签编号。
            oriented = ImageOps.exif_transpose(image) if image.getexif().get(274, 1) != 1 else image
            # P/1 模式下 PIL 对 resize 强制 NEAREST（忽略 resample 参数），先转
            # RGB(A) 保 LANCZOS；转换只发生在调色板与双值图，不影响大图的
            # 先缩放后合成路径。
            if oriented.mode in ("P", "1"):
                oriented = oriented.convert("RGBA" if "transparency" in oriented.info else "RGB")
            # 先缩放、后合成白底：带 alpha 的大图在全分辨率合成单张峰值达数百 MB。
            oriented.thumbnail(
                (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )
            flattened = _flatten_to_rgb(oriented)
            buffer = BytesIO()
            flattened.save(buffer, format="JPEG", quality=THUMBNAIL_JPEG_QUALITY)
            return _DecodeOutcome(buffer.getvalue(), None)
    except FileNotFoundError:
        # 源图缺失上抛与生成失败分档，供调用方区分静默跳过与失败告警。
        raise
    except Exception as exc:
        logger.warning("缩略图解码失败: {} -> {}", image_path.name, exc)
        return _DecodeOutcome(None, str(exc))


async def _build_thumbnail_outcome_limited(image_path: Path) -> _DecodeOutcome:
    """在进程级解码限流信号量与 CPU 卸载池内生成单张解码结果。"""
    async with _get_decode_semaphore():
        return await run_in_cpu_pool(_build_thumbnail_outcome, image_path)


def thumbnail_cache_root(images_root: Path) -> Path:
    """返回与图片目录并列的缩略图缓存目录（<数据根目录>/.seedream/thumbs）。"""
    return images_root.parent / THUMBNAIL_CACHE_DIR_NAME


def _thumb_key(image_path: Path, mtime_ns: int, size: int) -> str:
    digest = hashlib.blake2b(
        f"{image_path}|{mtime_ns}|{size}".encode("utf-8", "backslashreplace"),
        digest_size=16,
    ).hexdigest()
    return f"{digest}.thumb"


def _read_bytes_safely(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _store_thumbnail(thumb: Path, thumbs_root: Path, data: bytes) -> None:
    try:
        thumbs_root.mkdir(parents=True, exist_ok=True)

        def _write(fd: int) -> None:
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(data)

        atomic_replace_from_fd_sync(thumb, _write, suffix=".thumb-tmp")
    except OSError:
        # 缓存写失败降级为每次现生成
        return
    _maybe_sweep_thumbnails(thumbs_root)


def _maybe_sweep_thumbnails(thumbs_root: Path) -> None:
    if not _thumb_sweep_after.due(thumbs_root):
        return
    if not _thumb_sweep_lock.acquire(blocking=False):
        return
    try:
        if not _thumb_sweep_after.due(thumbs_root):
            return
        entries: list[tuple[float, int, Path]] = []
        # mtime 宽限判定须与文件系统墙钟同基准，不随节流门换单调钟。
        now = time.time()
        try:
            for entry in thumbs_root.iterdir():
                try:
                    info = entry.stat()
                except OSError:
                    # 并发替换中的临时条目此刻消失属正常，跳过继续收集
                    continue
                if entry.name.endswith(".thumb-tmp"):
                    # 在途写入的落盘骨架被 unlink 会使写入方原子替换失败，
                    # 宽限期内跳过。
                    if now - info.st_mtime < _THUMB_TMP_GRACE_SECONDS:
                        continue
                entries.append((info.st_mtime, info.st_size, entry))
        except OSError:
            return
        # 门在完整收集成功后才推进，中途异常下个窗口可重试
        _thumb_sweep_after.mark(thumbs_root, _THUMB_SWEEP_INTERVAL_SECONDS)
    finally:
        _thumb_sweep_lock.release()
    total = sum(size for _, size, _ in entries)
    if total <= THUMBNAIL_CACHE_MAX_TOTAL_BYTES:
        return
    target = THUMBNAIL_CACHE_MAX_TOTAL_BYTES // 2
    evicted = 0
    for _, size, entry in sorted(entries):
        if total <= target:
            break
        try:
            entry.unlink()
            total -= size
            evicted += 1
        except OSError:
            continue
    if evicted:
        logger.info("缩略图缓存超限，按最旧驱逐 {} 个文件", evicted)


def reset_thumb_sweep_gate() -> None:
    """复位全部缓存根的清理节流门。"""
    _thumb_sweep_after.clear()


# 缩略图在途去重的循环键控单飞注册表。
_thumb_inflight: LoopKeyedInflight[Path, _DecodeOutcome] = LoopKeyedInflight()


def _log_thumbnail_skip(image_name: str, reason: str | None) -> None:
    """记录单张预览跳过告警：缓存等待侧与无缓存直解共用，归因调用方上下文。"""
    if reason is None:
        logger.warning("缩略图生成失败，跳过该张预览: {}", image_name)
        return
    logger.warning("缩略图生成失败，跳过该张预览: {} -> {}", image_name, reason)


async def cached_thumbnail_bytes(image_path: Path, images_root: Path) -> bytes | None:
    """带落盘缓存与在途去重的缩略图获取：命中直接读文件，未命中共享单次解码。

    缓存键取进入解码前的一次源图 no-follow stat，解码期间源图被替换时旧键写入
    的缩略图随旧图失效，新图的键未命中重新生成；同循环内并发同键共享同一解码
    task（跨循环各自解码），取消语义与孤儿异常兜底由 InflightEntry 承担，
    跳过告警由各等待者在自身调用方上下文携带失败原因记录。

    Args:
        image_path: 已保存图片的文件路径。
        images_root: 已 resolve 的图片目录，缓存目录与其并列。

    Returns:
        JPEG 缩略图字节；损坏、像素超限或共享在途瞬态失败（标记混入）等生成
            失败时为 None。

    Raises:
        FileNotFoundError: 源图缺失或非常规文件（目录、断链、符号链接，及 POSIX
            中间分量为普通文件的 NotADirectoryError），与生成失败分档供调用方映射。
        OSError: 源图 stat 瞬时失败（权限、共享冲突），不与缺失混淆。
    """
    thumbs_root = thumbnail_cache_root(images_root)

    def _stat_and_read() -> tuple[bytes | None, Path]:
        # no-follow 口径与原图端点的打开序列一致，不跟随渲染符号链接目标；POSIX
        # 中间分量为普通文件时 stat 抛 NotADirectoryError（FileNotFoundError 的
        # 兄弟异常），显式并列归缺失，不逃逸为瞬时 stat 失败。
        try:
            st = image_path.stat(follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise FileNotFoundError(image_path) from exc
        if not stat.S_ISREG(st.st_mode):
            # 目录、断链与符号链接等非常规文件按缺失口径上抛，免去调用方另做存在性预检。
            raise FileNotFoundError(image_path)
        thumb = thumbs_root / _thumb_key(image_path, st.st_mtime_ns, st.st_size)
        # 空字节条目视为未命中，重新生成覆盖写损坏缓存
        return _read_bytes_safely(thumb), thumb

    cached, thumb = await asyncio.to_thread(_stat_and_read)
    if cached:
        return cached

    # 失败详情在 task 体内源头记录（渲染占位符），无人等待时不产生跳过告警。
    try:
        result = await _thumb_inflight.consume_shared(
            thumb, lambda: _decode_and_cache_thumbnail(image_path, thumb, thumbs_root)
        )
    except TransientInflightError:
        # 共享在途瞬态失败（标记混入）归生成失败降级为 None，其余异常原样上抛。
        _log_thumbnail_skip(image_path.name, None)
        return None
    if result.data is None:
        _log_thumbnail_skip(image_path.name, result.failure_reason)
        return None
    return result.data


async def _decode_and_cache_thumbnail(
    image_path: Path, thumb: Path, thumbs_root: Path
) -> _DecodeOutcome:
    """执行单次限流解码，成功时写入缓存，供 cached_thumbnail_bytes 在途去重。

    在途登记由创建方经完成回调按条目身份清理；创建者被取消时 task 继续运行，
    保护共享同一 task 的其他等待者不被连带取消。
    """
    outcome = await _build_thumbnail_outcome_limited(image_path)
    if outcome.data is not None:
        await asyncio.to_thread(_store_thumbnail, thumb, thumbs_root, outcome.data)
    return outcome


async def build_preview_contents(
    image_paths: list[Path], images_root: Path | None = None
) -> list[ImageContent]:
    """限流并发为已保存图片生成 ImageContent 预览列表。

    PIL 解码与缩放为同步 CPU 操作，逐张经缓存路径下放工作线程并由预览解码并发
    信号量限流；传入图片目录时经落盘缓存免除重复解码，生成失败或源图缺失的
    路径跳过，返回列表仅含成功项且与输入顺序一致。空输入返回空列表。

    Args:
        image_paths: 自动保存成功的图片文件路径列表。
        images_root: 已 resolve 的图片目录，None 时每次现生成不走缓存。

    Returns:
        与成功路径一一对应的 ImageContent 列表。
    """
    if not image_paths:
        return []

    async def _or_none(path: Path) -> bytes | None:
        try:
            if images_root is not None:
                return await cached_thumbnail_bytes(path, images_root)
            outcome = await _build_thumbnail_outcome_limited(path)
        except OSError:
            # 源图缺失与 stat 瞬时失败跳过该张。
            return None
        if outcome.data is None:
            _log_thumbnail_skip(path.name, outcome.failure_reason)
        return outcome.data

    thumbnails = await asyncio.gather(*(_or_none(path) for path in image_paths))
    contents: list[ImageContent] = []
    for thumbnail in thumbnails:
        if thumbnail is None:
            continue
        contents.append(
            ImageContent(
                type="image",
                data=base64.b64encode(thumbnail).decode("ascii"),
                mime_type=THUMBNAIL_MIME_TYPE,
            )
        )
    return contents
