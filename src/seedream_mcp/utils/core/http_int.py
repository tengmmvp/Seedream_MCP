"""HTTP 整数头的严格解析：transport、download 与 client 的 _http 共用单源。"""

from __future__ import annotations


def strict_http_int(value: bytes | str) -> int | None:
    """仅受理纯 ASCII 十进制数字，其余形态归 None。

    裸 int() 会接受 '+5'、' 5'、'1_0' 与 Unicode 数字等非法形态；str 入参 isascii
    前置防全角与阿拉伯-印度数字经 isdigit 绕过，bytes 的 isdigit 本就 ASCII-only。
    """
    if isinstance(value, str):
        if not (value.isascii() and value.isdigit()):
            return None
    elif not value.isdigit():
        return None
    try:
        return int(value)
    except ValueError:
        # 超长数字串超出 int 字串转换上限，按畸形归 None。
        return None
