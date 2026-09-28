"""PNG 头伪造共享助手：以真实 1x1 图改写 IHDR 声明任意宽高，供像素上限类用例复用。"""

from __future__ import annotations

import struct
import zlib
from io import BytesIO

from PIL import Image


def forged_png_bytes(width: int, height: int) -> bytes:
    """改写 1x1 PNG 的 IHDR 宽高为给定值并重算 CRC，头可声明任意像素而文件仅数十字节。"""
    buffer = BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    forged = bytearray(buffer.getvalue())
    forged[16:29] = struct.pack(">II5B", width, height, 8, 2, 0, 0, 0)
    forged[29:33] = struct.pack(">I", zlib.crc32(bytes(forged[12:29])) & 0xFFFFFFFF)
    return bytes(forged)
