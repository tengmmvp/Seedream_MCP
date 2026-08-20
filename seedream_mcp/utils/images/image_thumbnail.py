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
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.types import ImageContent

from ..core.logs import get_logger

if TYPE_CHECKING:
    from PIL import Image

logger = get_logger()

# 预览缩略图规格：典型取值下单张约几十 KB，十张量级的结果总载荷保持 MB 级以内。
THUMBNAIL_MAX_EDGE = 768
THUMBNAIL_JPEG_QUALITY = 80
THUMBNAIL_MIME_TYPE = "image/jpeg"

# 预览张数上限：组图与并行的合法组合可达 150 张，全量内嵌会使单条 CallToolResult
# 膨胀至数 MB 以上。
PREVIEW_MAX_IMAGES = 10

# 预览解码并发上限：4K 单张解码为 RGB 约占 50MB 内存，全量并发时瞬态可达 GB 级。
PREVIEW_DECODE_CONCURRENCY = 3

# 进程级解码限流信号量：并发上限约束覆盖全部调用而非单次调用；信号量绑定首次
# 使用时的事件循环，跨循环按需重建。
_decode_semaphore: asyncio.Semaphore | None = None
_decode_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_decode_semaphore() -> asyncio.Semaphore:
    """返回绑定当前事件循环的进程级解码限流信号量，事件循环更替时重建。"""
    global _decode_semaphore, _decode_semaphore_loop
    loop = asyncio.get_running_loop()
    if _decode_semaphore is None or _decode_semaphore_loop is not loop:
        _decode_semaphore = asyncio.Semaphore(PREVIEW_DECODE_CONCURRENCY)
        _decode_semaphore_loop = loop
    return _decode_semaphore


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

    from .image_validation import ensure_image_decoders_ready

    # 幂等注册 HEIF 解码器并无条件设置 36M 解码像素上限。
    ensure_image_decoders_ready()

    try:
        with Image.open(image_path) as image:
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
            flattened = _flatten_to_rgb(oriented)
            flattened.thumbnail(
                (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )
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


async def build_preview_contents(image_paths: list[Path]) -> list[ImageContent]:
    """限流并发为已保存图片生成 ImageContent 预览列表。

    PIL 解码与缩放为同步 CPU 操作，逐张经 build_thumbnail_bytes_limited 下放
    工作线程并由 PREVIEW_DECODE_CONCURRENCY 信号量限流；生成失败的路径跳过，
    返回列表仅含成功项且与输入顺序一致。空输入返回空列表。

    Args:
        image_paths: 自动保存成功的图片文件路径列表。

    Returns:
        与成功路径一一对应的 ImageContent 列表。
    """
    if not image_paths:
        return []

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
