"""生成图片的缩略图预览。

从自动保存落盘的图片生成工具结果携带的 ImageContent 预览：长边不超过
THUMBNAIL_MAX_EDGE 像素的 JPEG，体积远小于原图，多图结果的协议消息不因预览显著
膨胀。解码像素上限经 image_validation 的幂等注册无条件设置进程级 PIL
MAX_IMAGE_PIXELS，不依赖调用方先经过参考图校验。单张生成失败安全跳过，不影响
其余图片与工具结果本身。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.types import ImageContent

from ..core.formats import MAX_IMAGE_PIXELS
from ..core.logs import get_logger
from ..core.loop_bound import loop_bound_semaphore
from ..io.io_file import atomic_replace_from_fd_sync

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
_thumb_sweep_after = 0.0
_thumb_sweep_lock = threading.Lock()

# 预览张数上限：组图与并行的合法组合可达 150 张，全量内嵌会使单条 CallToolResult
# 膨胀至数 MB 以上。
PREVIEW_MAX_IMAGES = 10

# 预览解码并发上限：4K 单张解码为 RGB 约占 50MB 内存，全量并发时瞬态可达 GB 级。
PREVIEW_DECODE_CONCURRENCY = 3


def _get_decode_semaphore() -> asyncio.Semaphore:
    """返回绑定当前事件循环的进程级解码限流信号量，事件循环更替时重建。"""
    return loop_bound_semaphore(PREVIEW_DECODE_CONCURRENCY, key="preview_decode")


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


def build_thumbnail_bytes(image_path: Path) -> bytes | None:
    """读取图片文件并生成 JPEG 缩略图字节。

    经 PIL thumbnail 缩放，保持纵横比且只缩小不放大，小于上限的原图按原尺寸
    编码。文件不存在、数据损坏、解码超限等任何异常统一归一为 None，跳过策略
    由调用方决定。

    Args:
        image_path: 已保存图片的文件路径。

    Returns:
        JPEG 缩略图字节；无法生成时为 None。
    """
    # PIL 惰性导入，首载含解码器注册，落点在工作线程而非事件循环。
    from PIL import Image, ImageOps

    from ..core.formats import ensure_image_decoders_ready

    ensure_image_decoders_ready()

    try:
        with Image.open(image_path) as image:
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
                return None
            # JPEG 先请求 draft 缩尺解码：解码器按请求尺寸缩减采样，只解码必要分辨率
            # 的像素，解码量下降约 4 至 16 倍；请求尺寸取上限的两倍，为后续缩放保留
            # 质量余量。draft 须在像素数据加载前调用才生效，对非 JPEG 格式为无害空
            # 操作，格式判定仅为显式表达意图。
            if image.format == "JPEG":
                image.draft("RGB", (THUMBNAIL_MAX_EDGE * 2, THUMBNAIL_MAX_EDGE * 2))
            # EXIF 方向在 flatten 前归一，透明合成与缩放基于物理方向。仅携带非默认
            # 方向标签的图片才做转置，其余图片省去 exif_transpose 的全分辨率拷贝。
            # draft 只缩减解码分辨率，不改写 EXIF 元数据，方向判定在其后仍然可靠。
            # 274 为 EXIF Orientation 标签编号。
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
            return buffer.getvalue()
    except Exception as e:
        logger.warning("缩略图生成失败，跳过该张预览: {} -> {}", image_path.name, e)
        return None


async def build_thumbnail_bytes_limited(image_path: Path) -> bytes | None:
    """在进程级解码限流信号量内生成单张缩略图字节。

    与批量预览共用同一并发上限，Web 操作台缩略图端点等独立调用方不绕开限流；
    信号量绑定当前事件循环且随循环更替自动重建，跨循环调用安全。

    Args:
        image_path: 已保存图片的文件路径。

    Returns:
        JPEG 缩略图字节；无法生成时为 None。
    """
    async with _get_decode_semaphore():
        return await asyncio.to_thread(build_thumbnail_bytes, image_path)


def thumbnail_cache_root(images_root: Path) -> Path:
    """返回与图片目录并列的缩略图缓存目录（<数据根目录>/.seedream/thumbs）。"""
    return images_root.parent / THUMBNAIL_CACHE_DIR_NAME


def _thumb_key(image_path: Path, mtime_ns: int, size: int) -> str:
    digest = hashlib.blake2b(
        f"{image_path}|{mtime_ns}|{size}".encode("utf-8", "backslashreplace"),
        digest_size=16,
    ).hexdigest()
    return f"{digest}.thumb"


def _source_stat(image_path: Path) -> os.stat_result | None:
    try:
        return image_path.stat()
    except OSError:
        return None


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
    global _thumb_sweep_after
    if time.time() < _thumb_sweep_after:
        return
    if not _thumb_sweep_lock.acquire(blocking=False):
        return
    try:
        if time.time() < _thumb_sweep_after:
            return
        entries: list[tuple[float, int, Path]] = []
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
        _thumb_sweep_after = time.time() + _THUMB_SWEEP_INTERVAL_SECONDS
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
    """复位缩略图清理节流门，仅供测试隔离调用。"""
    global _thumb_sweep_after
    _thumb_sweep_after = 0.0


async def cached_thumbnail_bytes(image_path: Path, images_root: Path) -> bytes | None:
    """带落盘缓存的缩略图获取：命中直接读文件，未命中限流解码后写缓存。

    缓存键取进入解码前的一次源图 stat，解码期间源图被替换时旧键写入的缩略图
    随旧图失效，新图的键未命中重新生成。

    Args:
        image_path: 已保存图片的文件路径。
        images_root: 已 resolve 的图片目录，缓存目录与其并列。

    Returns:
        JPEG 缩略图字节；无法生成时为 None。
    """
    thumbs_root = thumbnail_cache_root(images_root)

    def _stat_and_read() -> tuple[bytes | None, Path | None]:
        stat = _source_stat(image_path)
        if stat is None:
            return None, None
        thumb = thumbs_root / _thumb_key(image_path, stat.st_mtime_ns, stat.st_size)
        # 空字节条目视为未命中，重新生成覆盖写损坏缓存
        return _read_bytes_safely(thumb), thumb

    cached, thumb = await asyncio.to_thread(_stat_and_read)
    if cached:
        return cached
    # stat 失败即源图缺失，短路返回，不占解码信号量。
    if thumb is None:
        return None
    generated = await build_thumbnail_bytes_limited(image_path)
    if generated is not None:
        await asyncio.to_thread(_store_thumbnail, thumb, thumbs_root, generated)
    return generated


async def build_preview_contents(
    image_paths: list[Path], images_root: Path | None = None
) -> list[ImageContent]:
    """限流并发为已保存图片生成 ImageContent 预览列表。

    PIL 解码与缩放为同步 CPU 操作，逐张经缓存路径下放工作线程并由
    PREVIEW_DECODE_CONCURRENCY 信号量限流；传入图片目录时经落盘缓存免除重复解码，
    生成失败的路径跳过，返回列表仅含成功项且与输入顺序一致。空输入返回空列表。

    Args:
        image_paths: 自动保存成功的图片文件路径列表。
        images_root: 已 resolve 的图片目录，None 时每次现生成不走缓存。

    Returns:
        与成功路径一一对应的 ImageContent 列表。
    """
    if not image_paths:
        return []

    if images_root is not None:
        thumbnails = await asyncio.gather(
            *(cached_thumbnail_bytes(path, images_root) for path in image_paths)
        )
    else:
        thumbnails = await asyncio.gather(
            *(build_thumbnail_bytes_limited(path) for path in image_paths)
        )
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
