"""AutoSaveManager 批量并发的部分失败聚合测试，覆盖核心降级路径。"""

from pathlib import Path

import pytest

from seedream_mcp.utils.auto_save import AutoSaveManager, AutoSaveResult


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
    await manager.close()
