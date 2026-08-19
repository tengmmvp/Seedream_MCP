"""守护测试：_prepare_image_input 本地文件缓存键含 (mtime, size) 签名。

本地文件替换后签名变化使缓存失效；以 size 维度变化触发，规避 Windows mtime 精度问题。
"""

from pathlib import Path

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.images import image_input


@pytest.mark.asyncio
async def test_prepare_image_input_invalidates_cache_when_local_file_size_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地文件 size 变化后 (mtime, size) 签名失效，第二次调用重新走底层 prepare。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    roots_key = (str(tmp_path),)

    image_file = tmp_path / "ref.png"
    image_file.write_bytes(b"original-content")

    call_count = 0

    async def fake_prepare(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}#{call_count}"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare)

    # 第一次调用：cache miss，底层被调用，结果写入缓存
    first = await client._image_preparer.prepare_image_input(str(image_file), roots_key)
    assert call_count == 1
    assert len(client._image_preparer._prepare_cache) == 1

    # 覆写为更长的内容，确保 size 维度变化使签名失效
    image_file.write_bytes(b"replaced-content-with-more-bytes")

    # 第二次调用：签名变化 → cache miss → 重新调用底层
    second = await client._image_preparer.prepare_image_input(str(image_file), roots_key)
    assert call_count == 2
    assert first != second
    # size 维度变化使两次 cache_key 不同，缓存各保留一条
    assert len(client._image_preparer._prepare_cache) == 2
