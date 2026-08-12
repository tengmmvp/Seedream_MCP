"""parse_retry_after 解析测试：覆盖 delta-seconds、HTTP-date、上限保护与非法输入。"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from seedream_mcp.utils.errors import parse_retry_after


def test_parse_retry_after_delta_seconds() -> None:
    assert parse_retry_after({"retry-after": "120"}) == 120.0


def test_parse_retry_after_missing_returns_none() -> None:
    assert parse_retry_after({}) is None
    assert parse_retry_after({"retry-after": ""}) is None


def test_parse_retry_after_capped_at_max() -> None:
    assert parse_retry_after({"retry-after": "99999"}) == 300.0


def test_parse_retry_after_invalid_returns_none() -> None:
    assert parse_retry_after({"retry-after": "not-a-number"}) is None
    assert parse_retry_after({"retry-after": "-5"}) is None


def test_parse_retry_after_http_date_future() -> None:
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    result = parse_retry_after({"retry-after": format_datetime(future, usegmt=True)})
    assert result is not None
    assert 50 <= result <= 60


def test_parse_retry_after_http_date_past_returns_none() -> None:
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert parse_retry_after({"retry-after": format_datetime(past, usegmt=True)}) is None
