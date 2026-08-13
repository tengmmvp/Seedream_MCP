"""_cache_prepared_result 字节上限淘汰守护。

验证按累计字节双重上限的 LRU 淘汰：新条目超字节预算时先淘汰最旧条目腾位后才缓存；
单条结果大于 max_bytes 时跳过缓存；条目数超限时字节计数同步扣减。_prepare_cache_bytes
计数始终与缓存内条目长度之和一致。
"""

from __future__ import annotations

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig


def _key(tag: str) -> tuple[str, tuple[str, ...], tuple[float, int]]:
    return (tag, (), (0.0, 0))


def _make_client(max_bytes: int, max_entries: int = 1000) -> SeedreamClient:
    client = SeedreamClient(SeedreamConfig(api_key="k"))
    client._prepare_cache_max_bytes = max_bytes
    client._prepare_cache_max = max_entries
    return client


def test_cache_evicts_lru_until_byte_budget_fits() -> None:
    """新条目使累计字节超预算时，按 LRU 淘汰最旧条目直至可容纳再写入。"""
    client = _make_client(max_bytes=100)
    # 填入 3 条各 30 字节，累计 90
    client._cache_prepared_result(_key("a"), "a" * 30)
    client._cache_prepared_result(_key("b"), "b" * 30)
    client._cache_prepared_result(_key("c"), "c" * 30)
    assert client._prepare_cache_bytes == 90

    # 写入 40 字节：90 + 40 = 130 > 100，淘汰最旧 a(30) 后 60 + 40 = 100 不超
    client._cache_prepared_result(_key("d"), "d" * 40)
    assert _key("a") not in client._prepare_cache
    assert list(client._prepare_cache.keys()) == [_key("b"), _key("c"), _key("d")]
    assert client._prepare_cache_bytes == 100
    # 计数与缓存内容一致
    assert client._prepare_cache_bytes == sum(len(v) for v in client._prepare_cache.values())


def test_cache_evicts_multiple_lru_entries_for_large_insert() -> None:
    """单次写入需淘汰多条 LRU 才能容纳时，连续淘汰直至预算足够。"""
    client = _make_client(max_bytes=100)
    for tag, ch in [("a", "a"), ("b", "b"), ("c", "c")]:
        client._cache_prepared_result(_key(tag), ch * 30)
    # 90 + 80 = 170 > 100：依次淘汰 a、b、c 后 0 + 80 = 80 <= 100
    client._cache_prepared_result(_key("d"), "d" * 80)
    assert list(client._prepare_cache.keys()) == [_key("d")]
    assert client._prepare_cache_bytes == 80


def test_cache_skips_oversized_single_result() -> None:
    """单条结果自身大于 max_bytes 时跳过缓存，缓存保持空且计数为 0。"""
    client = _make_client(max_bytes=50)
    client._cache_prepared_result(_key("big"), "z" * 60)
    assert len(client._prepare_cache) == 0
    assert client._prepare_cache_bytes == 0


def test_cache_count_eviction_decrements_byte_account() -> None:
    """条目数超 max 时淘汰最旧条目并同步扣减字节计数，保持计数一致。"""
    client = _make_client(max_bytes=10000, max_entries=2)
    client._cache_prepared_result(_key("a"), "a" * 10)
    client._cache_prepared_result(_key("b"), "b" * 20)
    client._cache_prepared_result(_key("c"), "c" * 30)
    # 条目上限 2：淘汰 a，剩 b、c，bytes = 20 + 30 = 50
    assert list(client._prepare_cache.keys()) == [_key("b"), _key("c")]
    assert client._prepare_cache_bytes == 50
    assert client._prepare_cache_bytes == sum(len(v) for v in client._prepare_cache.values())


def test_oversized_insert_preserves_existing_entries() -> None:
    """单条结果大于 max_bytes 时直接跳过，不清空已有缓存条目。"""
    client = _make_client(max_bytes=100)
    client._cache_prepared_result(_key("a"), "a" * 30)
    client._cache_prepared_result(_key("b"), "b" * 30)
    assert client._prepare_cache_bytes == 60

    # 插入超大单项：120 > 100 永不可缓存，直接跳过，a、b 保留且计数不变
    client._cache_prepared_result(_key("big"), "z" * 120)
    assert list(client._prepare_cache.keys()) == [_key("a"), _key("b")]
    assert client._prepare_cache_bytes == 60
    assert _key("big") not in client._prepare_cache
