"""图像输入来源分类的单一判定实现。

统一 URL、Data URI 与本地文件路径三类判定，scheme 大小写不敏感符合 RFC 3986，
避免各调用方大小写策略漂移导致大写 scheme 的 URL 误入本地文件分支。
"""

from __future__ import annotations

from typing import Literal


def classify_image_reference(image: str) -> Literal["url", "data_uri", "local"]:
    """判定图像输入来源类型，scheme 大小写不敏感。

    仅取前 16 字符小写判定，避免对大 base64 data URI 做全量拷贝。最长 scheme
    前缀 ``https://`` 与 ``data:image/`` 均不超过 12 字符，16 字符窗口足够覆盖。

    Args:
        image: 图像输入字符串，调用方应先 strip 首尾空白。

    Returns:
        输入来源类型："url"、"data_uri"、"local" 三者之一。
    """
    prefix = image[:16].lower()
    if prefix.startswith(("http://", "https://")):
        return "url"
    if prefix.startswith("data:image/"):
        return "data_uri"
    return "local"
