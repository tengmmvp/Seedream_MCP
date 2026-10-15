"""strict_http_int 直接单测：bytes 与 str 两入参形态的接受与拒绝口径。"""

from __future__ import annotations

import pytest

from seedream_mcp.utils.core.http_int import strict_http_int

from _http_int_cases import _MALFORMED_BYTES_FORMS, _MALFORMED_STR_FORMS


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1024", 1024), ("0", 0), ("007", 7)],
    ids=["digits", "zero", "leading-zeros"],
)
def test_strict_http_int_accepts_pure_ascii_digit_str(value: str, expected: int) -> None:
    """str 形态纯 ASCII 十进制数字正常解析，前导零同样受理。"""
    assert strict_http_int(value) == expected


def test_strict_http_int_accepts_pure_ascii_digit_bytes() -> None:
    """bytes 形态纯 ASCII 十进制数字正常解析，前导零同样受理。"""
    assert strict_http_int(b"1024") == 1024
    assert strict_http_int(b"007") == 7


@pytest.mark.parametrize("value", _MALFORMED_STR_FORMS)
def test_strict_http_int_rejects_malformed_str(value: str) -> None:
    """str 形态非纯 ASCII 十进制数字一律归 None，不经裸 int() 宽松解析。"""
    assert strict_http_int(value) is None


@pytest.mark.parametrize("value", _MALFORMED_BYTES_FORMS)
def test_strict_http_int_rejects_malformed_bytes(value: bytes) -> None:
    """bytes 形态非纯 ASCII 十进制数字一律归 None。"""
    assert strict_http_int(value) is None


@pytest.mark.parametrize("value", ["9" * 5000, b"9" * 5000], ids=["str", "bytes"])
def test_strict_http_int_normalizes_overlong_digit_run(value: bytes | str) -> None:
    """超长数字串超出 int 字串转换上限，按畸形归 None 不抛 ValueError。"""
    assert strict_http_int(value) is None
