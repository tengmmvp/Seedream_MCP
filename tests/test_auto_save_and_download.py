"""AutoSaveManager 与 DownloadManager 关键路径测试。

覆盖自动保存成功/降级、清理节流按 base_dir 跨实例共享与按目录隔离、下载内容
类型校验与对端 IP fail-closed、fsync 开关透传。
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
    _FakeResponse,
    _FakeSession,
    _patch_download_network,
    _png_success_response,
)

# 1x1 透明 PNG 的 base64 编码
_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def test_is_image_compatible_content_type_accepts_image_and_binary() -> None:
    """图片与二进制内容类型视为兼容。"""
    assert _is_image_compatible_content_type("image/png")
    assert _is_image_compatible_content_type("image/jpeg; charset=utf-8")
    assert _is_image_compatible_content_type("application/octet-stream")
    assert _is_image_compatible_content_type("application/binary")
    # 空内容类型视为兼容，交由字节签名嗅探兜底
    assert _is_image_compatible_content_type("")


def test_is_image_compatible_content_type_rejects_non_image() -> None:
    """非图片内容类型被拒绝，image/svg 同在拒绝之列。"""
    assert not _is_image_compatible_content_type("text/html")
    assert not _is_image_compatible_content_type("text/html; charset=utf-8")
    assert not _is_image_compatible_content_type("application/json")
    # SVG 可内嵌脚本/实体，存在 XSS 与 XXE 风险，即使属 image/* 也拒绝
    assert not _is_image_compatible_content_type("image/svg+xml")
    assert not _is_image_compatible_content_type("image/svg")


class _FakeResponseNoConnection:
    """connection 为 None 的伪响应，驱动对端 IP 提取失败的 fail-closed 路径。"""

    connection = None


def test_validate_connected_peer_ip_fails_closed_without_peer() -> None:
    """无法提取对端 IP 时 fail-closed 拒绝下载，避免绕过连接后校验。"""
    manager = DownloadManager()

    with pytest.raises(DownloadError, match="无法获取连接对端IP"):
        manager._validate_connected_peer_ip(  # type: ignore[arg-type]
            _FakeResponseNoConnection(), "https://example.com/x.png"
        )


async def test_save_base64_image_writes_file(tmp_path: Path) -> None:
    """合法 PNG base64 落盘为实际存在的 .png 文件。"""
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

        async def fake_download(url, save_path, headers=None, fsync=False):  # type: ignore[no-untyped-def]
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
    """URL 派生后缀与响应体实际格式不符时，local_path 报告实际落盘文件。

    URL 无后缀派生 .jpeg 而响应体为 PNG：字节签名嗅探会把落盘路径修正为 .png 后缀；
    save_image 必须基于下载结果的 file_path 构造对外路径，报告 URL 派生的原始路径
    会指向不存在的文件，markdown_ref 同理。
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


async def test_maybe_cleanup_throttle_entry_survives_capacity_eviction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """节流表容量驱逐按最近使用序进行，刚刷新时间戳的键不被移除。

    对已存在键赋值不移动条目位置：按插入位置驱逐会把链首刚刷新节流时间戳的
    键连同时间戳一起移除，同目录的下一次保存视为从未节流而再次触发并发清理，
    正常清理被并发方的删除竞争误判为失败。写入后移到链尾保证驱逐的是真正
    最久未用的键。
    """
    import time as time_module

    from seedream_mcp.utils.io import io_save as auto_save_module

    auto_save_module._cleanup_last_run.clear()
    cleanup_calls: list[int] = []

    def fake_run_cleanup(days: int, max_total_bytes: int | None) -> dict:
        cleanup_calls.append(days)
        return {"deleted_files": 0, "deleted_size": 0, "errors": []}

    # 预置 16 个已过期键占满容量上限，目标目录居链首；修复前写入不移动位置，
    # 驱逐时恰为被逐出的链首键。
    stale = time_module.time() - 7200
    auto_save_module._cleanup_last_run[str(tmp_path)] = stale
    for i in range(15):
        auto_save_module._cleanup_last_run[f"old-{i}"] = stale

    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=30)
    monkeypatch.setattr(manager.file_manager, "run_cleanup_policies", fake_run_cleanup)
    await manager._maybe_cleanup()
    await auto_save_module.drain_background_cleanup_tasks()
    assert cleanup_calls == [30]

    # 第 17 个键触发容量驱逐：被逐出的是最久未用的 old 键，目标目录的节流
    # 时间戳随最近使用序保留。
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    manager_other = AutoSaveManager(base_dir=other_dir, cleanup_days=30)
    monkeypatch.setattr(manager_other.file_manager, "run_cleanup_policies", fake_run_cleanup)
    await manager_other._maybe_cleanup()
    await auto_save_module.drain_background_cleanup_tasks()
    assert cleanup_calls == [30, 30]

    # 紧随其后同目录的请求被节流，不再次触发清理。
    await manager._maybe_cleanup()
    await auto_save_module.drain_background_cleanup_tasks()
    assert cleanup_calls == [30, 30]


async def test_maybe_cleanup_sweeps_orphan_part_with_cleanup_disabled(
    tmp_path: Path,
) -> None:
    """cleanup_days=0 且 max_total_bytes=None 时遗留 .part 孤儿仍被清扫。

    遗留临时文件清扫不受清理开关门控：auto-save 启用但两项清理均显式关闭的部署下，
    进程崩溃遗留的 .part 不无界累积。宽限期内的 .part 不受清扫影响。
    """
    import os
    from datetime import datetime, timedelta

    from seedream_mcp.utils.io import io_save as auto_save_module

    auto_save_module._cleanup_last_run.clear()
    stale_part = tmp_path / "tmpabc123.png.part"
    stale_part.write_bytes(b"x" * 30)
    stale_time = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(stale_part, (stale_time, stale_time))
    fresh_part = tmp_path / "tmpdef456.png.part"
    fresh_part.write_bytes(b"y" * 10)

    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0, max_total_bytes=None)
    try:
        await manager._maybe_cleanup()
        await auto_save_module.drain_background_cleanup_tasks()

        assert not stale_part.exists()
        assert fresh_part.exists()
    finally:
        await manager.close()


async def test_maybe_cleanup_failure_backoff_throttles_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """清理失败时写入短退避时间戳，失败重试有独立于完整节流间隔的下限。

    旧行为回滚到清理前旧值，失败后的首次调用立即再次触发全目录扫描，高频保存下
    持续失败的清理退化为每次保存都扫描；退避时间戳使下次重试至少等待 60 秒。
    """
    import time as time_module

    from seedream_mcp.utils.io import io_save as auto_save_module

    auto_save_module._cleanup_last_run.clear()
    calls: list[int] = []

    def failing_cleanup(days: int, max_total_bytes: int | None) -> dict:
        calls.append(days)
        raise RuntimeError("persistent cleanup failure")

    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=30)
    try:
        monkeypatch.setattr(manager.file_manager, "run_cleanup_policies", failing_cleanup)

        # 首次清理失败：异常被吞，写入短退避时间戳
        await manager._maybe_cleanup()
        await auto_save_module.drain_background_cleanup_tasks()
        assert calls == [30]

        # 紧接着的第二次调用被退避时间戳节流，不再立即重试
        await manager._maybe_cleanup()
        await auto_save_module.drain_background_cleanup_tasks()
        assert calls == [30]

        # 退避时间戳形态为 now - interval + backoff：距下次可重试还需约退避秒数，
        # 用例执行耗时可忽略，余量按退避值减 1 秒容差断言
        base_key = str(manager.file_manager.base_dir)
        stored = auto_save_module._cleanup_last_run[base_key]
        remaining_wait = auto_save_module._CLEANUP_MIN_INTERVAL_SECONDS - (
            time_module.time() - stored
        )
        assert remaining_wait >= auto_save_module._CLEANUP_FAILURE_RETRY_BACKOFF_SECONDS - 1
        assert remaining_wait < auto_save_module._CLEANUP_MIN_INTERVAL_SECONDS
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

    async def held_cleanup(base_key: str) -> None:
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


async def test_download_image_rejects_html_content_type_single_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_sleep: None
) -> None:
    """200 + text/html 驱动 download_image 主循环：终态 DownloadError、单次尝试、不落盘。

    HTML 错误页属语义明确的非图片响应，内容类型校验在写盘前拒绝；误入可重试分类
    会徒增退避等待且最终仍不可能成功。
    """
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(status=200, headers={"content-type": "text/html"})])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="响应内容类型非图片"):
        await manager.download_image("https://example.com/img.png", save_path)

    assert session._idx == 1
    assert not save_path.exists()
    assert not list(tmp_path.glob("*.part"))


def test_validate_url_wrapper_returns_false_for_ftp_scheme() -> None:
    """wrapper 契约：ftp 协议的静态拒绝经 validate_url 归约为返回 False，不向调用方抛 DownloadError。

    抛错形态的静态校验分支由 test_download_manager_ssrf.py 的
    test_validate_url_static_rejects_non_http_scheme 专属套件覆盖，此处仅锁定
    wrapper 特有的 bool 返回契约。
    """
    assert DownloadManager().validate_url("ftp://x/1.png") is False


# ==================== fsync 开关透传 ====================


async def test_save_base64_image_fsync_true_calls_os_fsync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fsync=True 经 AutoSaveManager 透传到 save_bytes 落盘，os.fsync 被调用。"""
    import os as os_module

    fsync_calls: list[int] = []
    real_fsync = os_module.fsync

    def _tracking_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os_module, "fsync", _tracking_fsync)

    manager = AutoSaveManager(base_dir=tmp_path, fsync=True)
    try:
        result = await manager.save_base64_image(_PNG_B64, prompt="测试图片")
        assert result.success is True
        assert result.local_path is not None
        assert Path(result.local_path).read_bytes()
        assert len(fsync_calls) == 1
    finally:
        await manager.close()


async def test_save_image_fsync_true_passes_through_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_sleep: None
) -> None:
    """fsync=True 经 AutoSaveManager 透传到下载落盘路径，下载完成后 os.fsync 被调用。"""
    import os as os_module

    fsync_calls: list[int] = []
    real_fsync = os_module.fsync

    def _tracking_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os_module, "fsync", _tracking_fsync)

    download_manager = DownloadManager()
    session = _FakeSession([_png_success_response()])
    _patch_download_network(monkeypatch, download_manager, session)
    manager = AutoSaveManager(base_dir=tmp_path, download_manager=download_manager, fsync=True)

    async with manager:
        result = await manager.save_image("https://example.com/img.png", prompt="测试图片")

    assert result.success is True
    assert result.local_path is not None
    assert Path(result.local_path).read_bytes() == _PNG_BYTES
    assert len(fsync_calls) == 1


async def test_save_base64_image_fsync_default_off_skips_os_fsync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """默认 fsync 关闭：落盘成功但不调用 os.fsync，不产生同步刷盘开销。"""
    import os as os_module

    fsync_calls: list[int] = []

    def _tracking_fsync(fd: int) -> None:
        fsync_calls.append(fd)

    monkeypatch.setattr(os_module, "fsync", _tracking_fsync)

    manager = AutoSaveManager(base_dir=tmp_path)
    try:
        result = await manager.save_base64_image(_PNG_B64, prompt="测试图片")
        assert result.success is True
        assert fsync_calls == []
    finally:
        await manager.close()
