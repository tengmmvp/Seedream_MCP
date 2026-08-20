"""Web 操作台图库浏览 API 测试：保存根边界、相对路径输出与分页。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from _web_fixtures import build_web_app, make_png_bytes, write_workspace_config


async def _post_browse(app: Any, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return await client.post("/web/api/browse", json=body)


async def test_browse_lists_images_with_relative_paths(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """保存根内图片以相对路径返回，不出现盘符绝对路径或占位符。"""
    save_root = write_workspace_config(tmp_path)
    day_dir = save_root / "2026-08-20" / "text_to_image"
    day_dir.mkdir(parents=True)
    (day_dir / "a.png").write_bytes(make_png_bytes())
    (day_dir / "b.png").write_bytes(make_png_bytes())
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    paths = [item["path"] for item in payload["images"]]
    assert "2026-08-20/text_to_image/a.png" in paths
    assert all(":" not in path for path in paths)
    assert payload["workspace_roots"] != "<工作区根（服务器配置）>"


async def test_browse_paginates_with_limit(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """limit 透传到扫描，has_more 与 next_offset 正确反馈剩余条目。"""
    save_root = write_workspace_config(tmp_path)
    day_dir = save_root / "2026-08-20" / "text_to_image"
    day_dir.mkdir(parents=True)
    for name in ("a.png", "b.png", "c.png"):
        (day_dir / name).write_bytes(make_png_bytes())
    app = build_web_app()

    response = await _post_browse(app, {"limit": 2})

    payload = response.json()
    assert payload["count"] == 2
    assert payload["has_more"] is True
    assert payload["next_offset"] == 2


async def test_browse_rejects_invalid_body(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """非法参数经 BrowseImagesInput 校验拒绝，映射 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"limit": 0})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
