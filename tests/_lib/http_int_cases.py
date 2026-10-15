"""HTTP 整数头畸形形态目录：strict_http_int 单测与下载域 Content-Range/Content-Length 拒绝口径参数化共用。"""

from __future__ import annotations

import pytest

# 裸 int() 可解析但按 RFC 9110 语法非法、Unicode 数字绕过 isdigit，或空串一类
# 平凡畸形；bytes 入参不含 Unicode 数字形态。
_MALFORMED_STR_FORMS = [
    pytest.param("+5", id="plus-sign"),
    pytest.param("-1", id="minus-sign"),
    pytest.param(" 5", id="leading-space"),
    pytest.param("5 ", id="trailing-space"),
    pytest.param("1_0", id="underscore"),
    pytest.param("", id="empty"),
    pytest.param("abc", id="non-numeric"),
    pytest.param("１２３", id="fullwidth"),
    pytest.param("١٢٣", id="arabic-indic"),
]

_MALFORMED_BYTES_FORMS = [
    pytest.param(b"+5", id="plus-sign"),
    pytest.param(b"-1", id="minus-sign"),
    pytest.param(b" 5", id="leading-space"),
    pytest.param(b"5 ", id="trailing-space"),
    pytest.param(b"1_0", id="underscore"),
    pytest.param(b"", id="empty"),
    pytest.param(b"abc", id="non-numeric"),
]
