"""classify_image_reference 单元测试。

守护图像输入来源分类的单一判定，重点覆盖 scheme 大小写不敏感：历史上
image_input/image_validation/io_path 三处的 http/https 判定大小写敏感，大写
scheme 的 URL 误入本地文件分支。
"""

import pytest

from seedream_mcp.utils.images.image_ref import classify_image_reference


@pytest.mark.parametrize(
    "image,expected",
    [
        # URL：scheme 按 RFC 3986 大小写不敏感，历史回归点
        ("http://example.com/x.png", "url"),
        ("https://example.com/x.png", "url"),
        ("HTTP://example.com/x.png", "url"),
        ("HTTPS://example.com/x.png", "url"),
        ("HtTpS://example.com/x.png", "url"),
        # Data URI：前缀大小写不敏感
        ("data:image/png;base64,iVBORw0KGgo=", "data_uri"),
        ("Data:image/png;base64,iVBORw0KGgo=", "data_uri"),
        ("DATA:IMAGE/png;base64,", "data_uri"),
        # 本地路径
        ("./images/x.png", "local"),
        ("/abs/path/x.png", "local"),
        ("x.png", "local"),
        ("relative/path/x.jpg", "local"),
    ],
)
def test_classify_image_reference(image: str, expected: str) -> None:
    """各类来源与大小写混合 scheme 的分类正确。"""
    assert classify_image_reference(image) == expected


def test_classify_empty_and_whitespace_treated_as_local() -> None:
    """空串与仅空白视为本地路径，交由下游校验拒绝，不在分类层抛错。"""
    assert classify_image_reference("") == "local"
    assert classify_image_reference("   ") == "local"


def test_classify_only_checks_prefix_window() -> None:
    """仅取前 16 字符判定，超长 base64 data URI 不做全量拷贝。"""
    long_data_uri = "data:image/png;base64," + "A" * 100000
    assert classify_image_reference(long_data_uri) == "data_uri"
