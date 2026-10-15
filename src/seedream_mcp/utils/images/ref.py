"""图像输入来源分类的单一判定实现。

统一 URL、Data URI 与本地文件路径三类判定，scheme 大小写不敏感符合 RFC 3986，
避免各调用方大小写策略漂移导致大写 scheme 的 URL 误入本地文件分支。
"""

from __future__ import annotations

from typing import Literal

from ..core.errors import SeedreamValidationError


def require_image_str(image: object) -> str:
    """非字符串图像输入归一为校验错误，不落入兜底异常暴露 Python 内部信息。

    单图与批量预处理的全部入口共用，保证守卫深度与错误文案一致。
    """
    if not isinstance(image, str):
        raise SeedreamValidationError("图像输入必须是字符串", field="image", value=None)
    return image


def classify_image_reference(image: str) -> Literal["url", "data_uri", "local"]:
    """判定图像输入来源类型，scheme 大小写不敏感。

    http(s) 前缀不论斜杠数归入 url，单斜杠手误由统一 URL 校验给出精确报错；data:
    前缀在 5 至 128 短窗内找逗号，逗号前头部（无逗号取同窗截断）含 ``/`` 的 MIME
    形态判 data_uri，非图像 MIME 与无逗号截断 URI 均交 Data URI 校验给出精确
    报错，形如 data:logo.png、data:photo,v2.png 的本地文件名不误入。逗号与头部
    扫描仅在 data: 前缀命中时执行且封顶 128 字符，超长无逗号串不做全量扫描；
    scheme 判定取前 16 字符小写窗口，最长前缀 ``https://`` 为 8 字符足够覆盖。

    Args:
        image: 图像输入字符串，调用方应先 strip 首尾空白。

    Returns:
        输入来源类型："url"、"data_uri"、"local" 三者之一。
    """
    prefix = image[:16].lower()
    if prefix.startswith(("http:", "https:")):
        return "url"
    if prefix.startswith("data:"):
        comma = image.find(",", 5, 128)
        header = image[5:comma] if comma >= 0 else image[5:128]
        if "/" in header:
            return "data_uri"
    return "local"
