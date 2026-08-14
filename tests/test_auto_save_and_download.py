"""AutoSaveManager 与 DownloadManager 关键路径测试。

覆盖自动保存成功/降级、清理节流的实例独立性、下载内容类型校验与对端 IP fail-closed。
"""

import asyncio
from pathlib import Path

import pytest

from seedream_mcp.utils.io.io_save import AutoSaveManager
from seedream_mcp.utils.io.io_download import (
    DownloadError,
    DownloadManager,
    _is_image_compatible_content_type,
)

from _download_fakes import (
    _PNG_BYTES,
    _FakeSession,
    _patch_download_network,
    _png_success_response,
)

# 1x1 透明 PNG 的 base64 编码
_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def test_is_image_compatible_content_type_accepts_image_and_binary() -> None:
    assert _is_image_compatible_content_type("image/png")
    assert _is_image_compatible_content_type("image/jpeg; charset=utf-8")
    assert _is_image_compatible_content_type("application/octet-stream")
    assert _is_image_compatible_content_type("application/binary")
    # 空内容类型视为兼容，交由字节签名嗅探兜底
    assert _is_image_compatible_content_type("")


def test_is_image_compatible_content_type_rejects_non_image() -> None:
    assert not _is_image_compatible_content_type("text/html")
    assert not _is_image_compatible_content_type("text/html; charset=utf-8")
    assert not _is_image_compatible_content_type("application/json")
    # SVG 可内嵌脚本/实体，存在 XSS 与 XXE 风险，即使属 image/* 也拒绝
    assert not _is_image_compatible_content_type("image/svg+xml")
    assert not _is_image_compatible_content_type("image/svg")


class _FakeResponseNoConnection:
    connection = None


def test_validate_connected_peer_ip_fails_closed_without_peer() -> None:
    """无法提取对端 IP 时 fail-closed 拒绝下载，避免绕过连接后校验。"""
    manager = DownloadManager()

    with pytest.raises(DownloadError, match="无法获取连接对端IP"):
        manager._validate_connected_peer_ip(  # type: ignore[arg-type]
            _FakeResponseNoConnection(), "https://example.com/x.png"
        )


async def test_save_base64_image_writes_file(tmp_path: Path) -> None:
    manager = AutoSaveManager(base_dir=tmp_path)
    try:
        result = await manager.save_base64_image(_PNG_B64, prompt="测试图片")
        assert result.success is True
        assert result.local_path is not None
        saved = Path(result.local_path)
        assert saved.exists()
        assert saved.stat().st_size > 0
        assert saved.suffix.lower() == ".png"
    finally:
        await manager.close()


async def test_save_image_returns_failure_on_download_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """下载失败时返回失败结果而非抛出异常，保留原始 URL 供降级。"""
    manager = AutoSaveManager(base_dir=tmp_path)
    try:

        async def fake_download(url, save_path, headers=None):  # type: ignore[no-untyped-def]
            raise DownloadError("网络错误")

        monkeypatch.setattr(manager.download_manager, "download_image", fake_download)

        result = await manager.save_image("https://example.com/image.png", prompt="测试")

        assert result.success is False
        assert "网络错误" in (result.error or "")
        assert result.original_url == "https://example.com/image.png"
    finally:
        await manager.close()


async def test_save_image_reports_sniffed_final_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_sleep: None
) -> None:
    """URL 无后缀派生 .jpeg 而响应体为 PNG 时，local_path 与 markdown_ref 报告实际落盘文件。

    字节签名嗅探会把落盘路径修正为 .png 后缀；save_image 必须基于下载结果的
    file_path 构造对外路径，报告 URL 派生的原始路径会指向不存在的文件。
    """
    download_manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, download_manager, session)
    manager = AutoSaveManager(base_dir=tmp_path, download_manager=download_manager)

    async with manager:
        result = await manager.save_image("https://cdn.example.com/signed", prompt="测试图片")

    assert result.success is True
    assert result.local_path is not None
    final_path = Path(result.local_path)
    assert final_path.suffix.lower() == ".png"
    assert final_path.exists()
    assert final_path.read_bytes() == _PNG_BYTES
    assert result.markdown_ref is not None
    assert final_path.name in result.markdown_ref
    assert result.markdown_ref.endswith(".png)")


async def test_maybe_cleanup_throttle_shared_per_base_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """节流按 base_dir 跨请求共享：同目录仅触发一次，不同目录各自触发。"""
    from seedream_mcp.utils.io import io_save as auto_save_module

    auto_save_module._cleanup_last_run.clear()
    cleanup_calls: list[int] = []

    def fake_run_cleanup(days: int, max_total_bytes: int | None) -> dict:
        cleanup_calls.append(days)
        return {"deleted_files": 0, "deleted_size": 0, "errors": []}

    # 同一 base_dir 的两个实例共享节流
    manager_a = AutoSaveManager(base_dir=tmp_path, cleanup_days=30)
    manager_b = AutoSaveManager(base_dir=tmp_path, cleanup_days=30)
    monkeypatch.setattr(manager_a.file_manager, "run_cleanup_policies", fake_run_cleanup)
    monkeypatch.setattr(manager_b.file_manager, "run_cleanup_policies", fake_run_cleanup)

    await manager_a._maybe_cleanup()
    await manager_b._maybe_cleanup()  # 同 base_dir，被节流
    # 后台清理异步执行，等待完成后再断言调用次数
    await auto_save_module.drain_background_cleanup_tasks()
    assert cleanup_calls == [30]

    # 不同 base_dir 独立节流
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    manager_c = AutoSaveManager(base_dir=other_dir, cleanup_days=30)
    monkeypatch.setattr(manager_c.file_manager, "run_cleanup_policies", fake_run_cleanup)
    await manager_c._maybe_cleanup()
    await auto_save_module.drain_background_cleanup_tasks()
    assert cleanup_calls == [30, 30]


async def test_maybe_cleanup_retries_after_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """清理失败时节流时间戳回滚，下次批量保存可立即重试而非等待完整间隔。"""
    from seedream_mcp.utils.io import io_save as auto_save_module

    auto_save_module._cleanup_last_run.clear()
    calls: list[int] = []

    def failing_then_succeeding_cleanup(days: int, max_total_bytes: int | None) -> dict:
        calls.append(days)
        if len(calls) == 1:
            raise RuntimeError("transient cleanup failure")
        return {"deleted_files": 0, "deleted_size": 0, "errors": []}

    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=30)
    try:
        monkeypatch.setattr(
            manager.file_manager, "run_cleanup_policies", failing_then_succeeding_cleanup
        )

        # 首次清理失败：异常被吞，时间戳回滚使下次可重试
        await manager._maybe_cleanup()
        await auto_save_module.drain_background_cleanup_tasks()
        assert calls == [30]

        # 紧接着的第二次因上次失败未占用节流窗口，可立即重试
        await manager._maybe_cleanup()
        await auto_save_module.drain_background_cleanup_tasks()
        assert calls == [30, 30]
    finally:
        await manager.close()


async def test_close_does_not_wait_for_background_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """close 不等待在途后台清理，请求返回路径不被全量目录遍历阻塞。

    清理收尾由 drain_background_cleanup_tasks 在进程级退出清理时等待；此前 close 上的
    gather 使节流触发的请求同步挂起整个清理时长，且模块级任务集合造成跨请求延迟耦合。
    """
    from seedream_mcp.utils.io import io_save as auto_save_module

    auto_save_module.reset_cleanup_state()
    release = asyncio.Event()
    started = asyncio.Event()
    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=30)

    async def held_cleanup(base_key: str, previous: float) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(manager, "_run_cleanup_in_background", held_cleanup)

    async with manager:
        await manager._maybe_cleanup()
        # 让出控制权使后台任务开始执行，确认清理确已启动且被 release 挂起
        await asyncio.sleep(0)
        assert started.is_set()

    # async with 退出即 close 完成，而清理任务仍在途，证明 close 未等待清理
    assert not release.is_set()

    release.set()
    await auto_save_module.drain_background_cleanup_tasks()
    assert not auto_save_module._cleanup_tasks


def test_is_known_image_bytes_detects_image_magic() -> None:
    """下载字节签名校验：识别真实图片 magic，拒绝 HTML/可执行等伪造内容。"""
    from seedream_mcp.utils.core.formats import is_known_image_bytes

    assert is_known_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert is_known_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    assert is_known_image_bytes(b"GIF89a")
    assert not is_known_image_bytes(b"<html><script>x</script></html>")
    assert not is_known_image_bytes(b"MZ\x90\x00\x03\x00\x00\x00")
    assert not is_known_image_bytes(b"\x00" * 32)


async def test_save_base64_image_rejects_non_image_bytes(tmp_path: Path) -> None:
    """base64 解码后非图片字节须拒绝，对称下载路径的字节签名校验。"""
    from base64 import b64encode

    manager = AutoSaveManager(base_dir=tmp_path)
    try:
        bad_b64 = b64encode(b"<html><script>x</script></html>").decode()
        result = await manager.save_base64_image(bad_b64)
        assert result.success is False
        assert "不是受支持的图片格式" in (result.error or "")
    finally:
        await manager.close()
