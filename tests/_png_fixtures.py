"""PNG 测试字节共享助手：最小 PNG 字节供 base64 解码类用例，IHDR 宽高伪造供像素上限类用例。"""

from __future__ import annotations

import struct
import zlib
from io import BytesIO

from PIL import Image


def minimal_png_bytes() -> bytes:
    """构造最小 PNG 字节，魔法字节可被 is_known_image_bytes 识别。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def forged_png_bytes(width: int, height: int) -> bytes:
    """改写 1x1 PNG 的 IHDR 宽高为给定值并重算 CRC，头可声明任意像素而文件仅数十字节。"""
    buffer = BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    forged = bytearray(buffer.getvalue())
    forged[16:29] = struct.pack(">II5B", width, height, 8, 2, 0, 0, 0)
    forged[29:33] = struct.pack(">I", zlib.crc32(bytes(forged[12:29])) & 0xFFFFFFFF)
    return bytes(forged)
