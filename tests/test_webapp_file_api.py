"""Web 操作台文件端点测试：缩略图、原图与路径越界防护矩阵。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from _web_fixtures import build_web_app, make_png_bytes, write_workspace_config


async def _get(app: Any, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return await client.get(path)


@pytest.fixture
def web_app_with_image(tmp_path: Path, clean_web_routes: None, reset_http_app_state: None) -> Any:
    """带一张真实 PNG 的 Web 传输栈。"""
    save_root = write_workspace_config(tmp_path)
    day_dir = save_root / "2026-08-20" / "text_to_image"
    day_dir.mkdir(parents=True)
    (day_dir / "a.png").write_bytes(make_png_bytes())
    app = build_web_app()
    return app


async def test_thumbnail_returns_jpeg(web_app_with_image: Any) -> None:
    """缩略图端点返回可解码的 JPEG。"""
    response = await _get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:3] == b"\xff\xd8\xff"


async def test_image_returns_file_with_media_type(web_app_with_image: Any) -> None:
    """原图端点按扩展名返回 PNG 与对应 media type。"""
    response = await _get(web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


async def test_file_endpoints_missing_file_returns_404(web_app_with_image: Any) -> None:
    """未命中文件返回 404 而非 500。"""
    missing = await _get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/none.png"
    )
    missing_image = await _get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/none.png"
    )

    assert missing.status_code == 404
    assert missing_image.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "2026-08-20/../../../etc/passwd.png",
        "C:/Windows/system32/a.png",
        "/etc/passwd.png",
        "\\\\server\\share\\a.png",
    ],
)
async def test_file_endpoints_reject_traversal(web_app_with_image: Any, path: str) -> None:
    """绝对路径与上跳段一律 400，不触达文件系统。"""
    from urllib.parse import quote

    encoded = quote(path, safe="")
    response = await _get(web_app_with_image, f"/web/api/thumbnail?path={encoded}")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_path"


async def test_file_endpoints_reject_non_image_extension(web_app_with_image: Any) -> None:
    """非图片扩展名在白名单阶段拒绝。"""
    response = await _get(web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.txt")

    assert response.status_code == 400


async def test_file_endpoints_empty_path_returns_400(web_app_with_image: Any) -> None:
    """空路径参数直接拒绝。"""
    response = await _get(web_app_with_image, "/web/api/thumbnail")

    assert response.status_code == 400
