"""AutoSaveManager Base64 解码守卫与批量保存异常处理的单元测试。

覆盖 _prepare_base64_payload 的空数据/估算超限/解码失败/非图片格式/解码后超限守卫、
_run_batch_save 的 CancelledError 重抛与 Exception→fallback 降级分支，以及
AutoSaveResult.to_dict 的数据字段净化与 markdown alt 兜底。
"""

import asyncio
import base64
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from seedream_mcp.utils.io.io_save import (
    AutoSaveError,
    AutoSaveManager,
    AutoSaveResult,
    _build_markdown_alt,
    drain_background_cleanup_tasks,
)


@pytest.fixture
def manager(tmp_path: Path) -> AutoSaveManager:
    """构造 cleanup_days=0 的 AutoSaveManager，本文件用例不依赖按天清理。

    清理入口已不因清理开关短路，批量保存路径会触发后台 .part 清扫；本文件用例
    直连 _prepare_base64_payload 与保存入口，清理相关断言由清理专项文件覆盖。
    DownloadManager 的 aiohttp 会话惰性创建，_prepare_base64_payload 不触发会话分配，
    因此 sync fixture 无需 close 即可安全释放。
    """
    return AutoSaveManager(base_dir=tmp_path, cleanup_days=0)


@pytest.fixture(autouse=True)
async def _drain_cleanup_tasks() -> AsyncIterator[None]:
    """每个用例结束前等待在途后台清理任务完成，避免任务悬垂到用例事件循环之外。

    清理入口不设开关短路后，经 _run_batch_save 的用例会派生真实的后台清扫任务；
    drain 后断言与用例循环生命周期对齐，任务完成状态确定。
    """
    yield
    await drain_background_cleanup_tasks()


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
    """提供与字节签名一致的 mime 时扩展名为该格式的映射值。"""
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


async def test_save_base64_image_sniffed_extension_overrides_conflicting_mime(
    manager: AutoSaveManager,
) -> None:
    """data URI mime 与字节签名冲突时落盘扩展名取嗅探结果。

    data:image/png 前缀承载 JPEG 签名字节，扩展名取嗅探的 .jpeg 而非 mime 推断
    的 .png，与下载路径嗅探修正最终路径的口径对称，扩展名与实际内容不符时不得
    以声明为准落盘。
    """
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    data_uri = "data:image/png;base64," + base64.b64encode(jpeg_bytes).decode()

    result = await manager.save_base64_image(data_uri, custom_name="conflict", tool_name="t2i")

    assert result.success is True
    assert result.local_path is not None
    assert Path(result.local_path).suffix == ".jpeg"
    assert Path(result.local_path).read_bytes() == jpeg_bytes


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
        [failing_task, ok_task],
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
            [cancelled_task],
            [{"url": "http://x/cancel.png"}],
            fallback_url_key="url",
            log_label="test",
        )


async def test_run_batch_save_base64_fallback_key(manager: AutoSaveManager) -> None:
    """fallback_url_key=None 时异常结果的 original_url 回退为 base64。"""

    async def failing_task() -> AutoSaveResult:
        raise RuntimeError("decode fail")

    results = await manager._run_batch_save(
        [failing_task],
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
        [ok_task],
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
    # 成功与失败路径的 original_url 统一为同一 base64 标识串；字节数经 metadata.file_size 提供
    assert results[0].original_url == "base64"
    assert results[0].metadata["file_size"] == len(png_bytes)
    assert results[1].success is False
    assert results[1].original_url == "base64"


# ==================== to_dict 净化与 markdown alt 兜底 ====================


def test_auto_save_result_to_dict_sanitizes_local_path_and_markdown_ref() -> None:
    """to_dict 对 local_path/markdown_ref 施加 sanitize_data_text。

    与 results.py 的 data 通道同字段防护对称：同名字段在 data 项通道已净化，
    auto_save.results 通道不得少做——CRLF 压平防注入，userinfo 凭据剥离。
    """
    result = AutoSaveResult(
        success=True,
        original_url="https://user:pass@example.com/img.png?token=abc",
        local_path="C:\\save\\img.png\r\nFAKE: injected",
        markdown_ref="![alt](./img.png)\r\nFAKE-REF: injected",
        error=None,
    )

    dumped = result.to_dict()

    # 控制字符压平，换行注入不可行；内容主体保留，数据字段不做常规截断以保持可用性
    assert dumped["local_path"] == "C:\\save\\img.png  FAKE: injected"
    assert dumped["markdown_ref"] == "![alt](./img.png)  FAKE-REF: injected"
    # 纯 URL 数据字段仅剥 userinfo 凭据，签名查询参数保持完整
    assert dumped["original_url"] == "https://example.com/img.png?token=abc"


def test_auto_save_result_to_dict_sanitizes_error_text() -> None:
    """error 通道过 sanitize_error_text 截断，凭据样式片段被剥离。"""
    result = AutoSaveResult(
        success=False,
        original_url="base64",
        error="api_key: sk-secret123\r\n下载失败",
    )

    dumped = result.to_dict()

    assert "sk-secret123" not in dumped["error"]
    assert "\r" not in dumped["error"]
    assert "\n" not in dumped["error"]


async def test_save_base64_image_prompt_not_embedded_in_markdown_ref(
    manager: AutoSaveManager,
) -> None:
    """prompt 不再拼入 markdown alt：CRLF、凭据样式与超长内容不经 markdown_ref 泄露。

    提示词已在 structuredContent 顶层 prompt 字段存在，alt 兜底使用固定文案，输出
    有界且不引入注入面。custom_name 固定文件名派生，隔离断言到 alt 通道。
    """
    png_bytes = _minimal_png_bytes()
    payload = base64.b64encode(png_bytes).decode()
    malicious_prompt = "api_key: sk-secret\r\nFAKE: injected " + "x" * 5000

    result = await manager.save_base64_image(
        payload, prompt=malicious_prompt, custom_name="cat", tool_name="t2i"
    )

    assert result.success is True
    ref = result.markdown_ref
    assert ref is not None
    assert ref.startswith("![Generated Image](")
    assert "sk-secret" not in ref
    assert "FAKE" not in ref
    assert "\r" not in ref
    assert "\n" not in ref
    # 固定文案兜底使 alt 有界，超长 prompt 不放大 markdown_ref
    assert len(ref) < 300


async def test_save_base64_image_escapes_markdown_breaking_alt(
    manager: AutoSaveManager,
) -> None:
    """调用方提供的 alt 含控制字符与 markdown 定界符时压平并转义，引用结构完整。

    右方括号与反斜杠经转义防 alt 内容截断 ``![...](...)`` 结构；空 alt 回退固定文案。
    """
    png_bytes = _minimal_png_bytes()
    payload = base64.b64encode(png_bytes).decode()

    result = await manager.save_base64_image(payload, alt_text="a]b\\c\r\nd", tool_name="t2i")

    assert result.success is True
    ref = result.markdown_ref
    assert ref is not None
    assert "\\]" in ref
    assert "\\\\" in ref
    assert "\r" not in ref
    assert "\n" not in ref

    empty_alt = await manager.save_base64_image(payload, alt_text="", tool_name="t2i")
    assert empty_alt.markdown_ref is not None
    assert empty_alt.markdown_ref.startswith("![Generated Image](")


def test_build_markdown_alt_truncation_keeps_escape_pairs_intact() -> None:
    """超长 alt 截断后不产生尾随孤立反斜杠，转义对不被截断点劈开。

    截断发生在转义前的压平文本上并按最坏两倍放大预留上限。本用例的输入在转义后
    长度为 201，若在转义后截断到 200，会留下未配对的单个尾随反斜杠转义掉闭合
    方括号，使 Markdown 图片引用不再解析。
    """
    # 1 个普通字符加 100 个反斜杠：压平长度 101 触发截断，转义后 1 + 200 = 201；
    # 若在转义后截断到 200，末位恰为转义对的前半，留下孤立尾随反斜杠
    alt = "a" + "\\" * 100
    result = _build_markdown_alt(alt)

    assert len(result) <= 200
    # 尾随反斜杠连续段长度必须为偶数，反斜杠转义对完整
    trailing = len(result) - len(result.rstrip("\\"))
    assert trailing % 2 == 0
    # 未触发截断的边界输入：100 个反斜杠转义后恰为 200，成对完整不截断
    exact = _build_markdown_alt("\\" * 100)
    assert exact == "\\\\" * 100
    assert len(exact) == 200


def test_build_save_metadata_sanitizes_content_type() -> None:
    """content_type 为下载响应头原文，净化后控制字符与凭据片段不随 metadata 外泄。"""
    from seedream_mcp.utils.io.io_save import _build_save_metadata

    metadata = _build_save_metadata(
        "t2i", "2026-08-16T00:00:00", 1024, "image/png\r\nX-Injected: Bearer leak", 1
    )

    content_type = metadata["content_type"]
    assert "\r" not in content_type and "\n" not in content_type
    assert "leak" not in content_type
