"""classify_image_reference 单元测试。

守护图像输入来源分类的单一判定，重点覆盖 scheme 大小写不敏感：历史上
inputs/validation/path 三处的 http/https 判定大小写敏感，大写
scheme 的 URL 误入本地文件分支。
"""

import pytest

from seedream_mcp.utils.images.ref import classify_image_reference


@pytest.mark.parametrize(
    "image,expected",
    [
        # URL：scheme 按 RFC 3986 大小写不敏感，历史回归点。
        ("http://example.com/x.png", "url"),
        ("https://example.com/x.png", "url"),
        ("HTTP://example.com/x.png", "url"),
        ("HTTPS://example.com/x.png", "url"),
        ("HtTpS://example.com/x.png", "url"),
        # URL：单斜杠与三斜杠手误同样归入 url，交统一 URL 校验报精确错误。
        ("http:/example.com/x.png", "url"),
        ("https:/example.com/x.png", "url"),
        ("HTTPS:/example.com/x.png", "url"),
        ("http:///example.com/x.png", "url"),
        # Data URI：前缀大小写不敏感。
        ("data:image/png;base64,iVBORw0KGgo=", "data_uri"),
        ("Data:image/png;base64,iVBORw0KGgo=", "data_uri"),
        ("DATA:IMAGE/png;base64,", "data_uri"),
        # Data URI：非图像 MIME 同归 data_uri，交 Data URI 校验报精确错误。
        ("data:text/plain;base64,aGVsbG8=", "data_uri"),
        ("data:application/pdf;base64,aGVsbG8=", "data_uri"),
        # Data URI：带参数与点分子类型的 MIME 形态。
        ("data:image/png;charset=utf-8;base64,iVBORw0KGgo=", "data_uri"),
        ("data:image/vnd.microsoft.icon;base64,iVBORw0KGgo=", "data_uri"),
        # Data URI：无逗号同归 data_uri，交 Data URI 校验报格式错误。
        ("data:image/png;base64", "data_uri"),
        # data: 前缀的非 MIME 形态（本地文件名样）一律判 data_uri，按写坏的
        # Data URI 交校验报格式错误。
        ("data:logo.png", "data_uri"),
        ("data:photo,v2.png", "data_uri"),
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
    """仅拷贝前 16 字符前缀做 scheme 判定，超长输入不做全量扫描。"""
    long_data_uri = "data:image/png;base64," + "A" * 100000
    assert classify_image_reference(long_data_uri) == "data_uri"
    # 超长无逗号串同样仅判前缀。
    assert classify_image_reference("data:" + "A" * 100000) == "data_uri"
