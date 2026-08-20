"""AutoSaveManager 批量并发的部分失败聚合与清理范围测试。"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from seedream_mcp.utils.io.io_save import (
    AutoSaveManager,
    AutoSaveResult,
    drain_background_cleanup_tasks,
)


async def test_save_multiple_images_aggregates_partial_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """批量保存中部分失败时，成功与失败结果都应聚合返回，不中断整体。"""
    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0)
    call_index = 0

    async def fake_save(  # type: ignore[no-untyped-def]
        url,
        prompt="",
        tool_name="seedream",
        custom_name=None,
        alt_text=None,
    ):
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            return AutoSaveResult(success=False, original_url=url, error="下载失败")
        return AutoSaveResult(success=True, original_url=url, local_path=str(tmp_path / "ok.png"))

    monkeypatch.setattr(manager, "save_image", fake_save)

    results = await manager.save_multiple_images(
        [{"url": "http://x/1.png", "prompt": "p"}, {"url": "http://x/2.png", "prompt": "p"}],
        tool_name="t",
    )

    assert len(results) == 2
    assert results[0].success is False
    assert "下载失败" in (results[0].error or "")
    assert results[1].success is True
    # 清理入口不因清理开关短路，批量保存会派生后台清扫任务；drain 后再关闭，
    # 任务完成状态确定，不悬垂到用例事件循环之外
    await drain_background_cleanup_tasks()
    await manager.close()


async def test_maybe_cleanup_age_covers_default_root_beyond_request_base_dir(
    tmp_path: Path,
) -> None:
    """save_path 使保存目录指向子目录时，按天清理仍覆盖默认根下的历史目录。"""
    from seedream_mcp.utils.io import io_save as auto_save_module

    default_root = tmp_path / "images"
    request_dir = default_root / "custom"
    request_dir.mkdir(parents=True)

    expired = default_root / "2025-01-01" / "old.png"
    expired.parent.mkdir(parents=True)
    expired.write_bytes(b"x" * 100)
    expired_time = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(expired, (expired_time, expired_time))

    manager = AutoSaveManager(base_dir=request_dir, cleanup_base_dir=default_root, cleanup_days=30)
    try:
        await manager._maybe_cleanup()
        await auto_save_module.drain_background_cleanup_tasks()

        assert not expired.exists()
    finally:
        await manager.close()


async def test_maybe_cleanup_quota_enforced_across_default_root(tmp_path: Path) -> None:
    """总量配额按默认根整体计算，跨请求子目录驱逐默认根下的最旧文件。"""
    from seedream_mcp.utils.io import io_save as auto_save_module

    default_root = tmp_path / "images"
    request_dir = default_root / "custom"
    request_dir.mkdir(parents=True)

    now = datetime.now()
    oldest = default_root / "2025-01-01" / "old.png"
    oldest.parent.mkdir(parents=True)
    oldest.write_bytes(b"x" * 100)
    oldest_time = (now - timedelta(days=40)).timestamp()
    os.utime(oldest, (oldest_time, oldest_time))
    newest = request_dir / "new.png"
    newest.write_bytes(b"y" * 100)
    newest_time = (now - timedelta(days=1)).timestamp()
    os.utime(newest, (newest_time, newest_time))

    manager = AutoSaveManager(
        base_dir=request_dir,
        cleanup_base_dir=default_root,
        cleanup_days=0,
        max_total_bytes=150,
    )
    try:
        await manager._maybe_cleanup()
        await auto_save_module.drain_background_cleanup_tasks()

        assert not oldest.exists()
        assert newest.exists()
    finally:
        await manager.close()


async def test_save_image_rejects_oversized_pixel_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """下载内容像素头超过 36M 上限时删除落盘文件并按保存失败降级保留 URL。"""
    import io
    import struct
    import zlib

    # 真实 1x1 PNG 改写 IHDR 宽高为 10000x10000 并重算 CRC：结构合法可被 PIL 识别，
    # 头尺寸超限而文件本身只有几十字节，正是解压炸弹的字节形态。
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    bomb = bytearray(buffer.getvalue())
    bomb[16:29] = struct.pack(">II5B", 10_000, 10_000, 8, 2, 0, 0, 0)
    bomb[29:33] = struct.pack(">I", zlib.crc32(bytes(bomb[12:29])) & 0xFFFFFFFF)
    target = tmp_path / "bomb.png"
    target.write_bytes(bytes(bomb))

    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0)

    async def fake_download(url: str, save_path: Path, fsync: bool = False) -> dict:
        return {
            "success": True,
            "file_path": str(target),
            "file_size": len(bomb),
            "download_time": 0.0,
            "content_type": "image/png",
            "attempts": 1,
        }

    monkeypatch.setattr(manager.download_manager, "download_image", fake_download)
    monkeypatch.setattr(manager.download_manager, "validate_url", lambda url: True)

    result = await manager.save_image("http://x/bomb.png", prompt="p")

    assert result.success is False
    assert not target.exists()
    assert "像素" in (result.error or "")
    await drain_background_cleanup_tasks()
    await manager.close()


async def test_maybe_cleanup_throttle_shared_across_request_subdirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同一默认根下不同请求子目录共享节流，清理仅触发一次。"""
    from seedream_mcp.utils.io import io_save as auto_save_module

    default_root = tmp_path / "images"
    request_a = default_root / "a"
    request_b = default_root / "b"
    request_a.mkdir(parents=True)
    request_b.mkdir()

    cleanup_calls: list[int] = []

    def fake_run_cleanup(days: int, max_total_bytes: int | None) -> dict:
        cleanup_calls.append(days)
        return {"deleted_files": 0, "deleted_size": 0, "errors": []}

    manager_a = AutoSaveManager(base_dir=request_a, cleanup_base_dir=default_root, cleanup_days=30)
    manager_b = AutoSaveManager(base_dir=request_b, cleanup_base_dir=default_root, cleanup_days=30)
    monkeypatch.setattr(manager_a._cleanup_file_manager, "run_cleanup_policies", fake_run_cleanup)
    monkeypatch.setattr(manager_b._cleanup_file_manager, "run_cleanup_policies", fake_run_cleanup)

    await manager_a._maybe_cleanup()
    await manager_b._maybe_cleanup()  # 同默认根，被节流
    await auto_save_module.drain_background_cleanup_tasks()

    assert cleanup_calls == [30]
