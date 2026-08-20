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


async def test_file_endpoints_reject_colon_ads_paths(web_app_with_image: Any) -> None:
    """路径任意位置含冒号一律 400，覆盖 Windows 盘符与 ADS 数据流形态。"""
    from urllib.parse import quote

    for path in ("sub\\file.png:.jpg", "file.png:$DATA", "2026-08-20/a:p/b.png"):
        encoded = quote(path, safe="")
        response = await _get(web_app_with_image, f"/web/api/image?path={encoded}")

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_path"


async def test_thumbnail_goes_through_decode_limited_wrapper(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缩略图端点经 build_thumbnail_bytes_limited 解码，接入进程级限流信号量。"""
    from seedream_mcp.webapp import files as files_module

    calls: list[Path] = []

    async def _fake_limited(image_path: Path) -> bytes | None:
        calls.append(image_path)
        return b"\xff\xd8\xffminimal"

    monkeypatch.setattr(files_module, "build_thumbnail_bytes_limited", _fake_limited)

    response = await _get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xffminimal"
    assert [path.name for path in calls] == ["a.png"]


async def test_thumbnail_decode_concurrency_capped_by_semaphore(
    tmp_path: Path, web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发缩略图请求的解码并发峰值不超过 PREVIEW_DECODE_CONCURRENCY。"""
    import asyncio
    import time

    from seedream_mcp.utils.images import image_thumbnail as image_thumbnail_module

    day_dir = tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image"
    for name in ("b.png", "c.png", "d.png", "e.png", "f.png"):
        (day_dir / name).write_bytes(make_png_bytes())

    active = 0
    peak = 0
    calls = 0

    def _fake_decode(image_path: Path) -> bytes | None:
        del image_path
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        time.sleep(0.05)
        active -= 1
        return b"\xff\xd8\xff"

    monkeypatch.setattr(image_thumbnail_module, "build_thumbnail_bytes", _fake_decode)

    paths = [f"2026-08-20/text_to_image/{name}.png" for name in "abcdef"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web_app_with_image), base_url="http://127.0.0.1"
    ) as client:
        responses = await asyncio.gather(
            *(client.get(f"/web/api/thumbnail?path={path}") for path in paths)
        )

    assert all(response.status_code == 200 for response in responses)
    assert calls == len(paths)
    assert peak <= image_thumbnail_module.PREVIEW_DECODE_CONCURRENCY


async def test_file_endpoints_reject_non_image_extension(web_app_with_image: Any) -> None:
    """非图片扩展名在白名单阶段拒绝。"""
    response = await _get(web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.txt")

    assert response.status_code == 400


async def test_file_endpoints_empty_path_returns_400(web_app_with_image: Any) -> None:
    """空路径参数直接拒绝。"""
    response = await _get(web_app_with_image, "/web/api/thumbnail")

    assert response.status_code == 400


async def test_thumbnail_build_failure_returns_404(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缩略图解码返回 None 时回 404 统一 JSON，而非 500 或空响应。"""
    from seedream_mcp.webapp import files as files_module

    async def _none(image_path: Path) -> bytes | None:
        del image_path
        return None

    monkeypatch.setattr(files_module, "build_thumbnail_bytes_limited", _none)

    response = await _get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "not_found"
    assert payload["error_description"] == "缩略图生成失败"


@pytest.mark.parametrize("endpoint", ["thumbnail", "image"])
async def test_file_endpoints_save_root_unavailable_returns_400(
    endpoint: str, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """保存根不可解析时缩略图与原图端点均回 400 save_root_unavailable。"""
    import seedream_mcp.utils.io.io_path as io_path_module

    app = build_web_app()
    token = io_path_module._WORKSPACE_ROOTS_VAR.set([])
    try:
        response = await _get(app, f"/web/api/{endpoint}?path=2026-08-20/text_to_image/a.png")
    finally:
        io_path_module._WORKSPACE_ROOTS_VAR.reset(token)

    assert response.status_code == 400
    assert response.json()["error"] == "save_root_unavailable"
