"""_cache_prepared_result 字节上限淘汰守护。

字节与条目数双重上限：超预算按 LRU 淘汰最旧条目腾位，单条超限跳过缓存，
字节记账含键载荷，计数始终与缓存内键值长度之和一致。
"""

from __future__ import annotations

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig


def _key(tag: str) -> tuple[str, tuple[str, ...], tuple[float, int]]:
    return (tag, (), (0.0, 0))


def _make_client(max_bytes: int, max_entries: int = 1000) -> SeedreamClient:
    return SeedreamClient(
        SeedreamConfig(
            api_key="k", prepare_cache_max=max_entries, prepare_cache_max_bytes=max_bytes
        )
    )


def _accounted_total(client: SeedreamClient) -> int:
    """按键值载荷合计口径汇总缓存内条目，与 _prepare_cache_bytes 对账。"""
    preparer_cache = client._image_preparer._prepare_cache
    return sum(len(value) + len(key[0]) for key, value in preparer_cache.items())


def test_cache_evicts_lru_until_byte_budget_fits() -> None:
    """新条目使累计字节超预算时，按 LRU 淘汰最旧条目直至可容纳再写入。"""
    client = _make_client(max_bytes=100)
    # 填入 3 条各值 30 字节、键 1 字符，累计 93。
    client._image_preparer._cache_prepared_result(_key("a"), "a" * 30)
    client._image_preparer._cache_prepared_result(_key("b"), "b" * 30)
    client._image_preparer._cache_prepared_result(_key("c"), "c" * 30)
    assert client._image_preparer._prepare_cache_bytes == 93

    # 写入值 40 字节合计 41：93 + 41 = 134 > 100，淘汰 a、b 后 31 + 41 = 72 不超。
    client._image_preparer._cache_prepared_result(_key("d"), "d" * 40)
    assert _key("a") not in client._image_preparer._prepare_cache
    assert list(client._image_preparer._prepare_cache.keys()) == [_key("c"), _key("d")]
    assert client._image_preparer._prepare_cache_bytes == 72
    assert client._image_preparer._prepare_cache_bytes == _accounted_total(client)


def test_cache_evicts_multiple_lru_entries_for_large_insert() -> None:
    """单次写入需淘汰多条 LRU 才能容纳时，连续淘汰直至预算足够。"""
    client = _make_client(max_bytes=100)
    for tag, ch in [("a", "a"), ("b", "b"), ("c", "c")]:
        client._image_preparer._cache_prepared_result(_key(tag), ch * 30)
    # 93 + 81 = 174 > 100：依次淘汰 a、b、c 后 0 + 81 = 81 <= 100。
    client._image_preparer._cache_prepared_result(_key("d"), "d" * 80)
    assert list(client._image_preparer._prepare_cache.keys()) == [_key("d")]
    assert client._image_preparer._prepare_cache_bytes == 81


def test_cache_skips_oversized_single_result() -> None:
    """单条键值合计大于 max_bytes 时跳过缓存，缓存保持空且计数为 0。"""
    client = _make_client(max_bytes=50)
    client._image_preparer._cache_prepared_result(_key("big"), "z" * 60)
    assert len(client._image_preparer._prepare_cache) == 0
    assert client._image_preparer._prepare_cache_bytes == 0


def test_cache_count_eviction_decrements_byte_account() -> None:
    """条目数超 max 时淘汰最旧条目并同步扣减字节计数，保持计数一致。"""
    client = _make_client(max_bytes=10000, max_entries=2)
    client._image_preparer._cache_prepared_result(_key("a"), "a" * 10)
    client._image_preparer._cache_prepared_result(_key("b"), "b" * 20)
    client._image_preparer._cache_prepared_result(_key("c"), "c" * 30)
    # 条目上限 2：淘汰 a，剩 b、c，bytes = 21 + 31 = 52。
    assert list(client._image_preparer._prepare_cache.keys()) == [_key("b"), _key("c")]
    assert client._image_preparer._prepare_cache_bytes == 52
    assert client._image_preparer._prepare_cache_bytes == _accounted_total(client)


def test_oversized_insert_preserves_existing_entries() -> None:
    """单条键值合计大于 max_bytes 时直接跳过，不清空已有缓存条目。"""
    client = _make_client(max_bytes=100)
    client._image_preparer._cache_prepared_result(_key("a"), "a" * 30)
    client._image_preparer._cache_prepared_result(_key("b"), "b" * 30)
    assert client._image_preparer._prepare_cache_bytes == 62

    # 插入键值合计 121 > 100 永不可缓存，直接跳过，a、b 保留且计数不变。
    client._image_preparer._cache_prepared_result(_key("big"), "z" * 118)
    assert list(client._image_preparer._prepare_cache.keys()) == [_key("a"), _key("b")]
    assert client._image_preparer._prepare_cache_bytes == 62
    assert _key("big") not in client._image_preparer._prepare_cache


def test_cache_byte_accounting_includes_large_data_uri_key_payload() -> None:
    """data URI 大键的载荷与值一并记账：预算约束键值总驻留，淘汰扣减同口径。"""
    client = _make_client(max_bytes=200)
    large_uri = "data:image/png;base64," + "A" * 128
    assert len(large_uri) + 50 == 200
    client._image_preparer._cache_prepared_result(_key(large_uri), "v" * 50)
    assert client._image_preparer._prepare_cache_bytes == 200
    assert client._image_preparer._prepare_cache_bytes == _accounted_total(client)

    # 键值合计 200 + 新条目 11 超预算，唯一的大键条目按总驻留口径淘汰腾位。
    client._image_preparer._cache_prepared_result(_key("z"), "x" * 10)
    assert list(client._image_preparer._prepare_cache.keys()) == [_key("z")]
    assert client._image_preparer._prepare_cache_bytes == 11
