"""自动保存三档错误分层与落盘前置失败的守护测试。

覆盖 _run_save_with_degradation 的 OSError/未知异常两档归约：档位错误前缀、
logger 档位（warning 与 error 不串档）与批量保存不中断；
关池窗口的大载荷解码取消归已知档服务关闭；
以及 FileManager 的 ensure_directory 失败与 create_save_path 越界拒绝。
"""

import asyncio
import base64
from pathlib import Path
from typing import Any

import pytest

from seedream_mcp.utils.core.executors import should_offload_size, shutdown_cpu_offload_executor
from seedream_mcp.utils.io.save import AutoSaveManager
from seedream_mcp.utils.io.storage import FileManager, FileManagerError

from _lib.cpu_offload_spy import saturate_cpu_offload_pool
from _lib.log_fakes import capture_loguru_messages
from _lib.png_fixtures import minimal_png_bytes


async def test_save_image_oserror_degrades_to_filesystem_error_tier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """下载抛 OSError 时归文件系统错误档：warning 日志、错误前缀与 URL 回退。"""
    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0)

    async def failing_download(url: str, save_path: Path, fsync: bool = False) -> dict[str, Any]:
        del url, save_path, fsync
        raise OSError(28, "simulated no space left")

    monkeypatch.setattr(manager.download_manager, "download_image", failing_download)
    monkeypatch.setattr(manager.download_manager, "validate_url", lambda url: True)

    records: list[str] = []
    with capture_loguru_messages(records, level="INFO"):
        result = await manager.save_image("https://x/1.png", prompt="p")

    assert result.success is False
    assert result.original_url == "https://x/1.png"
    assert (result.error or "").startswith("文件系统错误: ")
    assert any("图片保存文件系统错误" in message for message in records)
    assert not any("未知错误" in message for message in records)
    await manager.close()


async def test_save_image_unknown_error_degrades_to_unknown_tier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """下载抛未知异常时归未知错误档：error 日志与未知错误前缀，不落入文件系统档。"""
    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0)

    async def buggy_download(url: str, save_path: Path, fsync: bool = False) -> dict[str, Any]:
        del url, save_path, fsync
        raise RuntimeError("simulated decoder bug")

    monkeypatch.setattr(manager.download_manager, "download_image", buggy_download)
    monkeypatch.setattr(manager.download_manager, "validate_url", lambda url: True)

    records: list[str] = []
    with capture_loguru_messages(records, level="INFO"):
        result = await manager.save_image("https://x/2.png", prompt="p")

    assert result.success is False
    assert (result.error or "").startswith("未知错误: ")
    assert any("图片保存未知错误" in message for message in records)
    assert not any("文件系统错误" in message for message in records)
    await manager.close()


async def test_save_base64_tiered_save_bytes_failures_do_not_interrupt_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """批量 Base64 保存中两档失败并存：前缀与日志档位不串档，成功项仍落盘。

    save_bytes 以 custom_name 派生的文件名分发故障档，
    三任务并发下结果仍与入参顺序一致，单项失败不中断批次。
    """
    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0)
    real_save_bytes = manager.file_manager.save_bytes
    png_payload = base64.b64encode(minimal_png_bytes()).decode()

    def tiered_save_bytes(
        file_path: Path, data: bytes, overwrite: bool = False, fsync: bool = False
    ) -> dict[str, Any]:
        name = file_path.name
        if name.startswith("fs"):
            raise OSError(28, "simulated no space left")
        if name.startswith("unknown"):
            raise RuntimeError("simulated writer bug")
        return real_save_bytes(file_path, data, overwrite=overwrite, fsync=fsync)

    monkeypatch.setattr(manager.file_manager, "save_bytes", tiered_save_bytes)

    records: list[str] = []
    with capture_loguru_messages(records, level="INFO"):
        results = await manager.save_multiple_base64_images(
            [
                {"b64_json": png_payload, "custom_name": "fs", "prompt": "p"},
                {"b64_json": png_payload, "custom_name": "unknown", "prompt": "p"},
                {"b64_json": png_payload, "custom_name": "ok", "prompt": "p"},
            ],
            tool_name="t2i",
        )

    assert [result.success for result in results] == [False, False, True]
    assert (results[0].error or "").startswith("文件系统错误: ")
    assert (results[1].error or "").startswith("未知错误: ")
    assert results[2].local_path is not None
    assert Path(results[2].local_path).read_bytes() == minimal_png_bytes()
    assert any("Base64 图片保存文件系统错误" in message for message in records)
    assert any("Base64 图片保存未知错误" in message for message in records)
    assert any("批量 Base64 保存完成: 1/3 成功" in message for message in records)


async def test_save_base64_pool_closed_during_decode_reports_shutdown_tier(
    tmp_path: Path,
) -> None:
    """关池窗口排队的大载荷解码被取消时归已知档：warning 日志与服务关闭文案，不落未知错误档。"""
    manager = AutoSaveManager(base_dir=tmp_path, cleanup_days=0)
    large_png = minimal_png_bytes() + b"\x00" * 60_000
    payload = base64.b64encode(large_png).decode()
    assert should_offload_size(len(payload))

    records: list[str] = []
    with capture_loguru_messages(records, level="INFO"):
        async with saturate_cpu_offload_pool() as saturated:
            task = asyncio.ensure_future(
                manager.save_base64_image(payload, custom_name="closed", tool_name="t2i")
            )
            await saturated.wait_queued()
            shutdown_cpu_offload_executor()
            result = await task
        await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5)

    assert result.success is False
    assert result.original_url == "base64"
    assert "服务正在关闭" in (result.error or "")
    assert not (result.error or "").startswith("未知错误")
    assert any("Base64 图片保存失败" in message for message in records)
    assert not any("Base64 图片保存未知错误" in message for message in records)
    assert not list(tmp_path.rglob("*.png"))
    await manager.close()


# ==================== FileManager 落盘前置失败 ====================


def test_ensure_directory_failure_raises_file_manager_error(tmp_path: Path) -> None:
    """mkdir 失败归一为 FileManagerError，携带目标路径与底层原因。"""
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("x")
    manager = FileManager(base_dir=tmp_path / "images")

    with pytest.raises(FileManagerError, match="创建目录失败"):
        manager.ensure_directory(blocker / "sub")


def test_create_save_path_rejects_path_outside_base_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成的保存路径越出保存目录时以 FileManagerError 拒绝，不返回越界路径。

    以 validate_path 置 False 建模符号链接逃逸等 resolve 后不在界内的形态。
    """
    manager = FileManager(base_dir=tmp_path / "images")
    monkeypatch.setattr(manager, "validate_path", lambda path: False)

    with pytest.raises(FileManagerError, match="路径不安全"):
        manager.create_save_path(
            prompt="cat", url="https://x/1.png", tool_name="t2i", date_folder=True
        )
