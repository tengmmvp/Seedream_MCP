"""parse_retry_after 解析测试：覆盖 delta-seconds、HTTP-date、上限保护与非法输入。"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from seedream_mcp.utils.core.errors import parse_retry_after


def test_parse_retry_after_delta_seconds() -> None:
    """delta-seconds 形态解析为等待秒数。"""
    assert parse_retry_after({"retry-after": "120"}) == 120.0


def test_parse_retry_after_missing_returns_none() -> None:
    """缺键或空值返回 None。"""
    assert parse_retry_after({}) is None
    assert parse_retry_after({"retry-after": ""}) is None


def test_parse_retry_after_capped_at_max() -> None:
    """超大秒数被上限钳制。"""
    assert parse_retry_after({"retry-after": "99999"}) == 300.0


def test_parse_retry_after_invalid_returns_none() -> None:
    """非法与负数值返回 None。"""
    assert parse_retry_after({"retry-after": "not-a-number"}) is None
    assert parse_retry_after({"retry-after": "-5"}) is None


def test_parse_retry_after_http_date_future() -> None:
    """未来 HTTP-date 解析为剩余等待秒数。"""
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    remaining_before = (future - datetime.now(timezone.utc)).total_seconds()
    result = parse_retry_after({"retry-after": format_datetime(future, usegmt=True)})
    assert result is not None
    # 双侧动态界：解析发生在取 remaining_before 之后、断言时刻之前，剩余差值
    # 单调递减，结果必落在两时刻之间；秒级取整丢亚秒精度，下界放宽 1 秒容差。
    # 固定阈值下界在重负载 CI 上会假红。
    remaining_after = (future - datetime.now(timezone.utc)).total_seconds()
    assert remaining_after - 1 <= result <= remaining_before


def test_parse_retry_after_http_date_past_returns_none() -> None:
    """过去 HTTP-date 无需等待，返回 None。"""
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert parse_retry_after({"retry-after": format_datetime(past, usegmt=True)}) is None


def test_parse_retry_after_zero_clamped_to_min() -> None:
    """值为 0 时按下限兜底为最小等待秒数，避免密集重试风暴。"""
    assert parse_retry_after({"retry-after": "0"}) == 1.0


def test_parse_retry_after_uppercase_header_key() -> None:
    """大写驼峰键名 Retry-After 须同样可解析。"""
    assert parse_retry_after({"Retry-After": "120"}) == 120.0


def test_parse_retry_after_http_date_far_future_clamped_to_max() -> None:
    """HTTP-date 指向远期时被上限钳制为最大等待秒数，避免被诱导长时间睡眠。"""
    far_future = datetime.now(timezone.utc) + timedelta(seconds=99999)
    result = parse_retry_after({"retry-after": format_datetime(far_future, usegmt=True)})
    assert result == 300.0
