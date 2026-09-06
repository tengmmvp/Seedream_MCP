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


def _oversized_header_png(width: int, height: int) -> bytes:
    """真实 1x1 PNG 改写 IHDR 宽高为给定值并重算 CRC，结构合法可被 PIL 识别。

    头尺寸可任意放大而文件本身只有几十字节，正是解压炸弹的字节形态。
    """
    import io
    import struct
    import zlib

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    forged = bytearray(buffer.getvalue())
    forged[16:29] = struct.pack(">II5B", width, height, 8, 2, 0, 0, 0)
    forged[29:33] = struct.pack(">I", zlib.crc32(bytes(forged[12:29])) & 0xFFFFFFFF)
    return bytes(forged)


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
    bomb = _oversized_header_png(10_000, 10_000)
    target = tmp_path / "bomb.png"
    target.write_bytes(bomb)

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


async def test_save_image_rejects_decompression_bomb_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """头像素超过 2 倍上限触发 PIL DecompressionBombError 时按像素超限口径拒绝。

    回归背景为该异常是 Exception 直接子类而非 OSError 子类，此前 except 元组接不
    住，异常击穿保存降级链路成为未知错误且落盘文件不被清理。9000x9000=81M 超过
    2x36M，打开阶段即抛错，具体尺寸不可得，错误文案不含宽高形态。
    """
    from seedream_mcp.utils.io import io_save as auto_save_module

    bomb = _oversized_header_png(9_000, 9_000)
    target = tmp_path / "bomb_error.png"
    target.write_bytes(bomb)

    # 复位就绪标志强制走冷路径，本次调用内 MAX_IMAGE_PIXELS 被置为 36M，81M 头
    # 像素在打开阶段触发 DecompressionBombError 而非显式头尺寸校验分支。
    monkeypatch.setattr(auto_save_module, "_decoders_ready", False)

    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0)

    async def fake_download(url: str, save_path: Path, fsync: bool = False) -> dict:
        del url, save_path, fsync
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

    result = await manager.save_image("http://x/bomb_error.png", prompt="p")

    error = result.error or ""
    assert result.success is False
    assert not target.exists()
    assert "超过保存上限" in error
    assert "9000x9000" not in error
    await drain_background_cleanup_tasks()
    await manager.close()


async def test_save_image_rejects_pixels_between_limit_and_double(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """头像素在 36M 至 2 倍上限的告警区间由显式头尺寸校验拒绝并携带具体尺寸。

    PIL 在该区间仅告警不抛错，锁定显式校验分支独立可用，不依赖解压炸弹异常路径。
    """
    bomb = _oversized_header_png(6_100, 6_100)  # 37.21M，介于 36M 与 72M 之间
    target = tmp_path / "band.png"
    target.write_bytes(bomb)

    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0)

    async def fake_download(url: str, save_path: Path, fsync: bool = False) -> dict:
        del url, save_path, fsync
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

    result = await manager.save_image("http://x/band.png", prompt="p")

    assert result.success is False
    assert not target.exists()
    assert "6100x6100" in (result.error or "")
    await drain_background_cleanup_tasks()
    await manager.close()


def test_pixel_limit_rejection_cold_path_initializes_decoders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """冷路径调用时设置解压炸弹阈值并注册 HEIF 解码器，再次调用不重复初始化。

    io 组不反向依赖 images 组的注册入口，初始化在 io_save 内同口径惰性执行；以
    注册计数 spy 断言调用与幂等，不构造真实 HEIF 载荷。
    """
    import io

    import pillow_heif
    from PIL import Image as PilImage

    from seedream_mcp.utils.core.formats import MAX_IMAGE_PIXELS
    from seedream_mcp.utils.io import io_save as auto_save_module

    tiny = tmp_path / "tiny.png"
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    tiny.write_bytes(buffer.getvalue())

    register_calls: list[int] = []

    def counting_register(**kwargs: object) -> None:
        del kwargs
        register_calls.append(1)

    monkeypatch.setattr(auto_save_module, "_decoders_ready", False)
    monkeypatch.setattr(pillow_heif, "register_heif_opener", counting_register)
    # 预置 PIL 默认阈值，与 36M 区分：冷路径断言才能证明阈值确被写入，不被
    # 先前用例残留的 36M 假绿。
    monkeypatch.setattr(PilImage, "MAX_IMAGE_PIXELS", 89_478_485)

    assert auto_save_module._pixel_limit_rejection(tiny) is None
    assert register_calls == [1]
    assert PilImage.MAX_IMAGE_PIXELS == MAX_IMAGE_PIXELS

    auto_save_module._pixel_limit_rejection(tiny)
    assert register_calls == [1]


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
