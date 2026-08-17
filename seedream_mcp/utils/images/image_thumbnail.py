"""生成图片的缩略图预览。

从自动保存落盘的图片文件生成 MCP 工具结果 content 携带的 ImageContent 预览：
长边不超过 THUMBNAIL_MAX_EDGE 像素的 JPEG，体积远小于原图，多图结果的协议消息
不因预览显著膨胀。解码像素上限沿用 image_validation 设置的进程级
PIL MAX_IMAGE_PIXELS，解码开销与拒绝边界与其一致。单张生成失败安全跳过，不影响
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

logger = get_logger(__name__)

# 预览缩略图规格：长边上限与 JPEG 质量共同决定单张预览体积，典型取值下每张
# 约几十 KB，十张量级的并行与组图结果总载荷保持在 MB 级以内。
THUMBNAIL_MAX_EDGE = 768
THUMBNAIL_JPEG_QUALITY = 80
THUMBNAIL_MIME_TYPE = "image/jpeg"


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """把任意模式的图片归一为不带透明通道的 RGB。

    JPEG 不支持透明通道：RGBA、LA 与带 transparency 的调色板模式合成白色背景，
    其余模式直接转换，透明区域在预览中呈白底而非丢失通道后发黑。

    Args:
        image: PIL 已打开的图片对象。

    Returns:
        RGB 模式的图片对象；输入已为 RGB 时原样返回。
    """
    from PIL import Image

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
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
    # PIL 与图像模块的其余惰性导入惯例一致：首载含解码器注册，落点在工作线程
    # 而非事件循环线程，server 导入链不因此急载 PIL。
    from PIL import Image

    try:
        with Image.open(image_path) as image:
            flattened = _flatten_to_rgb(image)
            flattened.thumbnail(
                (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )
            buffer = BytesIO()
            flattened.save(buffer, format="JPEG", quality=THUMBNAIL_JPEG_QUALITY)
            return buffer.getvalue()
    except Exception:
        logger.warning("缩略图生成失败，跳过该张预览: {}", image_path.name)
        return None


async def build_preview_contents(image_paths: list[Path]) -> list[ImageContent]:
    """并发为已保存图片生成 ImageContent 预览列表。

    PIL 解码与缩放为同步 CPU 操作，逐张经 asyncio.to_thread 下放工作线程并发
    执行；生成失败的路径跳过，返回列表仅含成功项且与输入顺序一致。空输入返回
    空列表。

    Args:
        image_paths: 自动保存成功的图片文件路径列表。

    Returns:
        与成功路径一一对应的 ImageContent 列表。
    """
    if not image_paths:
        return []
    thumbnails = await asyncio.gather(
        *(asyncio.to_thread(build_thumbnail_bytes, path) for path in image_paths)
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
