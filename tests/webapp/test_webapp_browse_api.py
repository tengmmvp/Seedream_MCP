"""Web 操作台图库浏览 API 测试：读权限边界、相对路径输出与分页。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from _lib.web_fixtures import (
    _assert_endpoint_error,
    _assert_structured_failure,
    build_web_app,
    make_images_root_unresolvable,
    make_png_bytes,
    web_asgi_client,
    web_post_raw,
    write_workspace_config,
)


async def _post_browse(app: Any, body: dict[str, Any]) -> httpx.Response:
    async with web_asgi_client(app) as client:
        return await client.post("/web/api/browse", json=body)


async def test_browse_lists_images_with_relative_paths(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """图片目录内图片以相对路径返回，不出现盘符绝对路径，边界回显字段被剥除。"""
    images_root = write_workspace_config(tmp_path)
    day_dir = images_root / "2026-08-20" / "text_to_image"
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
    images_root = write_workspace_config(tmp_path)
    day_dir = images_root / "2026-08-20" / "text_to_image"
    day_dir.mkdir(parents=True)
    for name in ("a.png", "b.png", "c.png"):
        (day_dir / name).write_bytes(make_png_bytes())
    app = build_web_app()

    response = await _post_browse(app, {"limit": 2})

    payload = response.json()
    assert payload["count"] == 2
    assert payload["has_more"] is True
    assert payload["next_offset"] == 2
    assert payload["rescan_futile"] is False


async def test_browse_rejects_invalid_body(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """非法参数经 BrowseImagesInput 校验拒绝，映射 400 结构化结果。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"limit": 0})

    _assert_structured_failure(response, "browse_images", "validation_error", status=400)


async def test_browse_invalid_json_returns_400_structured(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """非法 JSON 请求体映射 400 结构化结果，与生成端点的错误形态一致。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await web_post_raw(app, "/web/api/browse", b"not-json")

    _assert_structured_failure(response, "browse_images", "invalid_json", status=400)


async def test_browse_web_entry_does_not_assemble_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """图库 Web 入口驱动真实 browse 流水线且镜像装配关闭：镜像构建函数不被触达。"""
    from _lib.cpu_offload_spy import CpuOffloadSpy
    import seedream_mcp.tools.outputs as outputs_module

    images_root = write_workspace_config(tmp_path)
    day_dir = images_root / "2026-08-20"
    day_dir.mkdir()
    (day_dir / "a.png").write_bytes(make_png_bytes())
    spy = CpuOffloadSpy(outputs_module.build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    assert response.status_code == 200
    assert spy.calls == []


async def test_browse_internal_error_returns_500_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """run_browse_images 抛出未归类异常时兜底为 500 结构化结果而非裸异常。"""
    from seedream_mcp.webapp import gallery as gallery_module

    async def _explode(params: Any, ctx: Any = None, **kwargs: Any) -> Any:
        del params, ctx, kwargs
        raise RuntimeError("boom")

    monkeypatch.setattr(gallery_module, "run_browse_images", _explode)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    _assert_structured_failure(response, "browse_images", "internal_error", status=500)


@pytest.mark.parametrize("structured", [None, "bad", {"success": False}])
async def test_browse_runner_error_without_structured_content_maps_internal_error(
    structured: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """runner 回归返回 is_error 而结构化负载不可判型时显式归 internal_error，不落网关 502。"""
    from mcp.types import CallToolResult

    from seedream_mcp.webapp import gallery as gallery_module

    async def _bare_error(params: Any, ctx: Any = None, **kwargs: Any) -> Any:
        del params, ctx, kwargs
        return CallToolResult(content=[], structured_content=structured, is_error=True)

    monkeypatch.setattr(gallery_module, "run_browse_images", _bare_error)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    if isinstance(structured, dict):
        # dict 负载保形合成：保留原顶层键仅补 error，无完整信封的 tool/status。
        _assert_endpoint_error(response, "internal_error", status=500)
    else:
        _assert_structured_failure(response, "browse_images", "internal_error", status=500)


async def test_browse_rejects_directory_outside_images_root(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """读权限（工作区）内、图片目录外的目录在端点拒绝：Web 文件端点仅服务图片目录。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"directory": str(tmp_path)})

    _assert_structured_failure(
        response, "browse_images", "invalid_directory", status=400, message_contains="图片目录"
    )


async def test_browse_rejects_directory_outside_read_scope(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """读权限之外的绝对路径目录同样在端点以 400 拒绝，不进入核心扫描。"""
    write_workspace_config(tmp_path)
    outside = tmp_path.parent / "seedream-browse-outside"
    outside.mkdir(exist_ok=True)
    app = build_web_app()

    response = await _post_browse(app, {"directory": str(outside)})

    _assert_structured_failure(response, "browse_images", "invalid_directory", status=400)


async def test_browse_rejects_parent_escape_relative_directory(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """相对 ``..`` 穿越解析出图片目录的请求目录被端点拒绝，不回显服务器路径。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"directory": ".."})

    _assert_structured_failure(response, "browse_images", "invalid_directory", status=400)


async def test_browse_rejects_unc_directory_as_clean_400(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """UNC 形态 directory 在 browse 核心 resolve 前被拒，端点回统一 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"directory": r"\\attacker\share\x"})

    _assert_structured_failure(
        response, "browse_images", "validation_error", status=400, message_contains="UNC"
    )


async def test_browse_rejects_null_byte_directory_as_clean_400(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """空字节 directory 在 browse 核心被输入级拒绝，端点回统一 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"directory": "a\u0000b"})

    _assert_structured_failure(response, "browse_images", "validation_error", status=400)


async def test_browse_echoes_original_directory_not_absolute_images_root(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """成功响应的 directory 回显用户原始输入，绝对图片目录路径不出端点。"""
    images_root = write_workspace_config(tmp_path)
    day_dir = images_root / "2026-08-21"
    day_dir.mkdir()
    (day_dir / "a.png").write_bytes(make_png_bytes())
    app = build_web_app()

    response = await _post_browse(app, {"directory": "2026-08-21"})

    assert response.status_code == 200
    assert response.json()["directory"] == "2026-08-21"
    assert str(images_root) not in response.text
    assert images_root.as_posix() not in response.text
    # JSON 编码把反斜杠双写，泄漏须按转义后形态检查。
    assert str(images_root).replace("\\", "\\\\") not in response.text


async def test_browse_serves_explicit_images_root_outside_workspace(
    tmp_path: Path, clean_web_routes: None, reset_http_app_state: None
) -> None:
    """显式图片目录独立于工作区时图库正常浏览，读权限包含图片目录自身。"""
    from seedream_mcp.config import SeedreamConfig, set_active_config

    outside_root = tmp_path / "elsewhere"
    day_dir = outside_root / ".seedream" / "images" / "2026-08-22"
    day_dir.mkdir(parents=True)
    (day_dir / "a.png").write_bytes(make_png_bytes())
    set_active_config(
        SeedreamConfig(
            api_key="test_key",
            workspace_root=str(tmp_path / "ws"),
            data_root=str(outside_root),
        )
    )
    (tmp_path / "ws").mkdir()
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["images"][0]["path"] == "2026-08-22/a.png"


async def test_browse_images_root_unavailable_returns_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """显式数据根目录无法解析时回 400 结构化 images_root_unavailable，携带配置指引文案。"""
    make_images_root_unresolvable(monkeypatch, tmp_path)
    app = build_web_app()

    response = await _post_browse(app, {"show_details": True})

    _assert_structured_failure(
        response,
        "browse_images",
        "images_root_unavailable",
        status=400,
        message_contains="SEEDREAM_WORKSPACE_ROOT",
    )


def test_converge_for_web_rewrites_paths_without_dropping(tmp_path: Path) -> None:
    """条目 path 改写为图片目录相对形态；越界剔除在扫描源头完成，此处不删条目。"""
    from seedream_mcp.webapp.gallery import _converge_for_web

    images_root = tmp_path / "images"
    structured: dict[str, Any] = {
        "workspace_roots": [],
        "resolved_directories": [],
        "count": 2,
        "total_count": 2,
        "images": [
            {"path": str(images_root / "a.png")},
            {"path": str(tmp_path / "outside.png")},
        ],
    }

    _converge_for_web(structured, images_root)

    assert "workspace_roots" not in structured
    assert "resolved_directories" not in structured
    assert structured["count"] == 2
    assert len(structured["images"]) == 2
    assert structured["images"][0]["web_path"] == "a.png"
    assert "web_path" not in structured["images"][1]


async def test_web_browse_filters_outside_entries_at_source(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """越界条目经 bounds_scope 在扫描源头剔除，响应不出现界外路径与计数虚高。"""
    import seedream_mcp.tools.handlers.browse_images as browse_module
    from seedream_mcp.utils.io.scan import find_images_in_directory

    images_root = write_workspace_config(tmp_path)
    day_dir = images_root / "2026-08-20" / "text_to_image"
    day_dir.mkdir(parents=True)
    (day_dir / "a.png").write_bytes(make_png_bytes())
    outside = tmp_path / "outside.png"
    outside.write_bytes(make_png_bytes())
    real_scanner = find_images_in_directory

    def _scanner(directory: str, **kwargs: Any) -> list[Path]:
        found = real_scanner(directory, **kwargs)
        return found + [outside] if found else found

    monkeypatch.setattr(browse_module, "find_images_in_directory", _scanner)
    app = build_web_app()

    response = await _post_browse(app, {})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert [item["web_path"] for item in payload["images"]] == ["2026-08-20/text_to_image/a.png"]


async def test_web_browse_permanent_stall_suppresses_resume_offset(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一停滞谱系随重复请求收敛为永久态后，端点显式下发 stall_permanent 且不再回传续扫偏移，前端不进入原地翻页循环。"""
    from _lib.os_fakes import _install_failing_resolve_for

    # 坏条目须填满三轮停滞各自的末轮扩窗（12、48、192）才不中途扫尽。
    images_root = write_workspace_config(tmp_path)
    bad_names = [f"stall_{index:03d}.png" for index in range(192)]
    for name in bad_names:
        (images_root / name).write_bytes(make_png_bytes())
    _install_failing_resolve_for(monkeypatch, *bad_names)
    app = build_web_app()

    transient = [(await _post_browse(app, {"limit": 2})).json() for _ in range(2)]
    for payload in transient:
        assert payload["has_more"] is None
        assert payload["next_offset"] == 0
        assert payload["stall_permanent"] is False

    permanent = (await _post_browse(app, {"limit": 2})).json()
    assert permanent["count"] == 0
    assert permanent["has_more"] is None
    assert permanent["next_offset"] is None
    assert permanent["stall_permanent"] is True


async def test_web_browse_truncated_empty_page_flags_rescan_futile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """空截断页显式下发 rescan_futile：前端据此禁用推进，不进入全预算重扫循环。

    截断预算与偏移无关，无跳过条目的空截断页原地重扫必复现同一空窗；仅撤
    next_offset 时前端更多未知态仍放行推进，每次点击都付一次全预算重扫。
    """
    import seedream_mcp.utils.io.scan as scan_module

    images_root = write_workspace_config(tmp_path)
    for index in range(8):
        (images_root / f"img_{index:02d}.png").write_bytes(make_png_bytes())
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 5)
    app = build_web_app()

    response = await _post_browse(app, {})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 0
    assert payload["has_more"] is None
    assert payload["next_offset"] is None
    assert payload["stall_permanent"] is False
    assert payload["rescan_futile"] is True


async def test_web_browse_truncated_empty_page_with_skips_keeps_resume_offset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """带跳过条目的空截断页不作徒劳论：回传续扫偏移且 rescan_futile=false。

    跳过条目负缓存到期恢复或缓存按原始扫描量扩窗可使窗口增长，原地续扫
    非注定徒劳，前端维持推进导引；与无跳过条目的空截断页（rescan_futile=true、
    撤续扫偏移）对照。
    """
    import seedream_mcp.utils.io.scan as scan_module

    from _lib.os_fakes import _install_failing_resolve_for

    images_root = write_workspace_config(tmp_path)
    early = images_root / "a"
    early.mkdir()
    (early / "x.png").write_bytes(make_png_bytes())
    late = images_root / "b"
    late.mkdir()
    for index in range(20):
        (late / f"note_{index:02d}.txt").write_bytes(b"x")
    _install_failing_resolve_for(monkeypatch, "x.png")
    # 预算使子目录 a 产出坏图后子目录 b 触发截断，窗口唯一条目被 resolve 剔除。
    monkeypatch.setattr(scan_module, "_SCAN_ENTRY_BUDGET", 15)
    app = build_web_app()

    response = await _post_browse(app, {})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 0
    assert payload["has_more"] is None
    assert payload["next_offset"] == 0
    assert payload["stall_permanent"] is False
    assert payload["rescan_futile"] is False
    assert payload["skipped_count"] == 1
