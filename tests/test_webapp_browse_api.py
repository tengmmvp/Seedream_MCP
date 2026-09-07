"""Web 操作台图库浏览 API 测试：读权限边界、相对路径输出与分页。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from _web_fixtures import build_web_app, make_png_bytes, write_workspace_config


async def _post_browse(app: Any, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return await client.post("/web/api/browse", json=body)


async def test_browse_lists_images_with_relative_paths(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """存储区内图片以相对路径返回，不出现盘符绝对路径，边界回显字段被剥除。"""
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
    assert "workspace_roots" not in payload
    assert "resolved_directories" not in payload


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


async def test_browse_internal_error_returns_500_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """run_browse_images 抛出未归类异常时兜底为 500 统一 JSON 而非裸异常。"""
    from seedream_mcp.webapp import gallery as gallery_module

    async def _explode(params: Any, ctx: Any = None, workspace_roots: Any = None) -> Any:
        del params, ctx, workspace_roots
        raise RuntimeError("boom")

    monkeypatch.setattr(gallery_module, "run_browse_images", _explode)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"


async def test_browse_rejects_directory_outside_save_root(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """读权限（工作区）内、存储区外的目录在端点拒绝：Web 文件端点仅服务存储区。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"directory": str(tmp_path)})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_directory"
    assert "存储区" in body["error_description"]


async def test_browse_rejects_directory_outside_read_scope(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """读权限之外的绝对路径目录同样在端点以 400 拒绝，不进入核心扫描。"""
    write_workspace_config(tmp_path)
    outside = tmp_path.parent / "seedream-browse-outside"
    outside.mkdir(exist_ok=True)
    app = build_web_app()

    response = await _post_browse(app, {"directory": str(outside)})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_directory"


async def test_browse_rejects_parent_escape_relative_directory(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """相对 ``..`` 穿越解析出存储区的请求目录被端点拒绝，不回显服务器路径。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"directory": ".."})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_directory"


async def test_browse_rejects_unc_directory_as_clean_400(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """UNC 形态 directory 在 browse 核心 resolve 前被拒，端点回统一 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"directory": r"\\attacker\share\x"})

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "browse_failed"
    assert "UNC" in body["error"]["message"]


async def test_browse_rejects_null_byte_directory_as_clean_400(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """空字节 directory 在 browse 核心被输入级拒绝，端点回统一 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"directory": "a\u0000b"})

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "browse_failed"


async def test_browse_echoes_original_directory_not_absolute_save_root(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """成功响应的 directory 回显用户原始输入，绝对存储区路径不出端点。"""
    save_root = write_workspace_config(tmp_path)
    day_dir = save_root / "2026-08-21"
    day_dir.mkdir()
    (day_dir / "a.png").write_bytes(make_png_bytes())
    app = build_web_app()

    response = await _post_browse(app, {"directory": "2026-08-21"})

    assert response.status_code == 200
    assert response.json()["directory"] == "2026-08-21"
    assert str(save_root) not in response.text


async def test_browse_serves_explicit_save_root_outside_workspace(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """显式存储区独立于工作区时图库正常浏览，读权限包含存储区自身。"""
    from seedream_mcp.config import SeedreamConfig, set_active_config

    outside_root = tmp_path / "elsewhere"
    day_dir = outside_root / "2026-08-22"
    day_dir.mkdir(parents=True)
    (day_dir / "a.png").write_bytes(make_png_bytes())
    set_active_config(
        SeedreamConfig(
            api_key="test_key",
            workspace_root=str(tmp_path / "ws"),
            auto_save_base_dir=str(outside_root),
        )
    )
    (tmp_path / "ws").mkdir()
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["images"][0]["path"] == "2026-08-22/a.png"


async def test_browse_save_root_unavailable_returns_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """显式存储声明无法解析时回 400 save_root_unavailable，携带配置指引文案。"""
    import seedream_mcp.utils.io.io_path as io_path_module
    from seedream_mcp.config import SeedreamConfig, set_active_config

    def _unresolvable(configured_dir: str) -> Path:
        raise OSError("simulated unresolvable path")

    write_workspace_config(tmp_path)
    set_active_config(SeedreamConfig(api_key="test_key", auto_save_base_dir=str(tmp_path / "pics")))
    monkeypatch.setattr(io_path_module, "resolve_cached_save_base_dir", _unresolvable)
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "save_root_unavailable"
    assert "SEEDREAM_WORKSPACE_ROOT" in payload["error_description"]
