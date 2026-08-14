"""AutoSaveManager Base64 解码守卫与批量保存异常处理的单元测试。

覆盖 _prepare_base64_payload 的空数据/估算超限/解码失败/非图片格式/解码后超限守卫，
以及 _run_batch_save 的 CancelledError 重抛与 Exception→fallback 降级分支。
"""

import asyncio
import base64
from pathlib import Path

import pytest

from seedream_mcp.utils.io.io_save import AutoSaveError, AutoSaveManager, AutoSaveResult


@pytest.fixture
def manager(tmp_path: Path) -> AutoSaveManager:
    """构造 cleanup_days=0 的 AutoSaveManager，避免触发目录扫描副作用。

    DownloadManager 的 aiohttp 会话惰性创建，_prepare_base64_payload 不触发会话分配，
    因此 sync fixture 无需 close 即可安全释放。
    """
    return AutoSaveManager(base_dir=tmp_path, cleanup_days=0)


def _minimal_png_bytes() -> bytes:
    """构造合法的最小 PNG 字节，magic 头可被 is_known_image_bytes 识别。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ==================== _prepare_base64_payload 守卫 ====================


def test_prepare_base64_payload_empty_string(manager: AutoSaveManager) -> None:
    """空 payload 抛 AutoSaveError。"""
    with pytest.raises(AutoSaveError, match="空"):
        manager._prepare_base64_payload("", None)


def test_prepare_base64_payload_none(manager: AutoSaveManager) -> None:
    with pytest.raises(AutoSaveError, match="空"):
        manager._prepare_base64_payload(None, None)


def test_prepare_base64_payload_whitespace_only(manager: AutoSaveManager) -> None:
    """仅含空白字符的 payload strip 后为空，抛 AutoSaveError。"""
    with pytest.raises(AutoSaveError, match="空"):
        manager._prepare_base64_payload("   \n\t  ", None)


async def test_prepare_base64_payload_estimate_exceeds_limit(tmp_path: Path) -> None:
    """估算大小超过 max_file_size 时在解码前拒绝，避免内存放大。"""
    mgr = AutoSaveManager(base_dir=tmp_path, max_file_size=100, cleanup_days=0)
    try:
        # 200 chars → estimated 150 bytes > 100
        with pytest.raises(AutoSaveError, match="Base64数据过大"):
            mgr._prepare_base64_payload("A" * 200, None)
    finally:
        await mgr.close()


def test_prepare_base64_payload_decode_failure(manager: AutoSaveManager) -> None:
    """非法 base64 字符触发解码失败（validate=True 拒绝非字母表字符）。"""
    with pytest.raises(AutoSaveError, match="Base64解码失败"):
        manager._prepare_base64_payload("!!!!not_valid_base64!!!!", None)


def test_prepare_base64_payload_not_known_image(manager: AutoSaveManager) -> None:
    """解码成功但字节头非受支持图片 magic，拒绝。"""
    payload = base64.b64encode(b"hello world not an image at all").decode()
    with pytest.raises(AutoSaveError, match="不是受支持的图片格式"):
        manager._prepare_base64_payload(payload, None)


async def test_prepare_base64_payload_decoded_exceeds_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解码后字节超过 max_file_size 时拒绝。

    标准 base64 下 estimated_size >= decoded_size，估算守卫通常先行触发；
    此处 monkeypatch b64decode 返回超限字节，覆盖解码后大小检查的独立分支。
    """
    import seedream_mcp.utils.io.io_save as auto_save_module

    mgr = AutoSaveManager(base_dir=tmp_path, max_file_size=1000, cleanup_days=0)
    try:
        real_png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        payload = base64.b64encode(real_png_header).decode()
        oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000  # 超过 1000
        monkeypatch.setattr(auto_save_module.base64, "b64decode", lambda *a, **k: oversized)
        with pytest.raises(AutoSaveError, match="解码后数据过大"):
            mgr._prepare_base64_payload(payload, None)
    finally:
        await mgr.close()


# ---------- _prepare_base64_payload 正向路径 ----------


def test_prepare_base64_payload_valid_png(manager: AutoSaveManager) -> None:
    """合法 PNG base64 解码后返回字节、扩展名与 sha256 哈希。"""
    png_bytes = _minimal_png_bytes()
    payload = base64.b64encode(png_bytes).decode()
    content_bytes, extension, content_hash = manager._prepare_base64_payload(payload, None)
    assert content_bytes == png_bytes
    assert extension == ".png"
    assert len(content_hash) == 64  # sha256 hex digest


def test_prepare_base64_payload_with_mime(manager: AutoSaveManager) -> None:
    """提供 mime 时扩展名由 mime 映射决定。"""
    png_bytes = _minimal_png_bytes()
    payload = base64.b64encode(png_bytes).decode()
    _, extension, _ = manager._prepare_base64_payload(payload, "image/png")
    assert extension == ".png"


def test_prepare_base64_payload_strips_whitespace(manager: AutoSaveManager) -> None:
    """payload 含空白字符（换行/空格/制表）时被 strip 后正常解码。"""
    png_bytes = _minimal_png_bytes()
    clean_payload = base64.b64encode(png_bytes).decode()
    dirty_payload = clean_payload[:5] + " \n\t " + clean_payload[5:]
    content_bytes, _, _ = manager._prepare_base64_payload(dirty_payload, None)
    assert content_bytes == png_bytes


# ==================== _run_batch_save 异常处理 ====================


async def test_run_batch_save_converts_exception_to_failed_result(
    manager: AutoSaveManager,
) -> None:
    """任务抛 Exception 时降级为 success=False 结果，使用 fallback_url_key 取原始标识。"""

    async def failing_task() -> AutoSaveResult:
        raise RuntimeError("download boom")

    async def ok_task() -> AutoSaveResult:
        return AutoSaveResult(success=True, original_url="http://x/ok.png", local_path="/tmp/ok")

    image_data = [{"url": "http://x/fail.png"}, {"url": "http://x/ok.png"}]
    results = await manager._run_batch_save(
        [failing_task(), ok_task()],
        image_data,
        fallback_url_key="url",
        log_label="test",
    )
    assert len(results) == 2
    assert results[0].success is False
    assert "download boom" in (results[0].error or "")
    assert results[0].original_url == "http://x/fail.png"
    assert results[1].success is True


async def test_run_batch_save_reraises_cancelled_error(manager: AutoSaveManager) -> None:
    """CancelledError 必须向上传播，不被异常兜底吞掉。"""

    async def cancelled_task() -> AutoSaveResult:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await manager._run_batch_save(
            [cancelled_task()],
            [{"url": "http://x/cancel.png"}],
            fallback_url_key="url",
            log_label="test",
        )


async def test_run_batch_save_base64_fallback_key(manager: AutoSaveManager) -> None:
    """fallback_url_key=None 时异常结果的 original_url 回退为 base64。"""

    async def failing_task() -> AutoSaveResult:
        raise RuntimeError("decode fail")

    results = await manager._run_batch_save(
        [failing_task()],
        [{"b64_json": "abc"}],
        fallback_url_key=None,
        log_label="b64 test",
    )
    assert len(results) == 1
    assert results[0].success is False
    assert results[0].original_url == "base64"
    assert "decode fail" in (results[0].error or "")


async def test_run_batch_save_passes_through_success_results(
    manager: AutoSaveManager,
) -> None:
    """正常 AutoSaveResult 原样通过。"""

    async def ok_task() -> AutoSaveResult:
        return AutoSaveResult(success=True, original_url="http://x/1.png", local_path="/tmp/1")

    results = await manager._run_batch_save(
        [ok_task()],
        [{"url": "http://x/1.png"}],
        fallback_url_key="url",
        log_label="test",
    )
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].local_path == "/tmp/1"


# ==================== save_multiple_base64_images 端到端 ====================


async def test_save_multiple_base64_images_end_to_end(
    manager: AutoSaveManager, tmp_path: Path
) -> None:
    """合法与非法 payload 混合时，合法项落盘成功、非法项降级为失败结果。"""
    png_bytes = _minimal_png_bytes()
    payload = base64.b64encode(png_bytes).decode()
    image_data = [
        {"b64_json": payload, "prompt": "cat"},
        {"b64_json": "", "prompt": "bad"},  # 空 payload → 保存失败
    ]

    results = await manager.save_multiple_base64_images(image_data, tool_name="t2i")

    assert len(results) == 2
    assert results[0].success is True
    local_path = results[0].local_path
    assert local_path is not None
    assert Path(local_path).exists()
    assert Path(local_path).read_bytes() == png_bytes
    assert results[1].success is False
    assert results[1].original_url == "base64"
