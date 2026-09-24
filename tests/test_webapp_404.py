"""Web 操作台兜底 404 测试：风格化 404 页、API 前缀 JSON 错误与路由顺序。

兜底路由在 mount_web_static 中排在静态挂载之后追加；顺序若颠倒，静态资源
请求会被 ``/{path:path}`` 吞掉，静态资源用例即为本顺序的回归守护。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from _web_fixtures import (
    asgi_body_bytes,
    asgi_start_headers,
    build_web_app,
    drive_asgi_messages,
    prepare_static_dir,
    web_asgi_client,
    web_get,
    write_workspace_config,
)


def _page_scope(path: str) -> dict[str, object]:
    """构造直调 webapp handler 的最小 GET 页面 ASGI scope。"""
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
    }


async def test_unknown_path_returns_styled_html_404(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """未知路径返回风格化 404 页而非 Starlette 默认纯文本。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await web_get(app, "/random/nowhere")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "404" in response.text


async def test_unknown_api_path_returns_json_404(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """API 前缀下未知接口保持统一 JSON 错误形态，供前端程序化消费。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await web_get(app, "/web/api/nonexistent")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


async def test_api_prefix_without_trailing_slash_returns_json_404(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """无尾斜杠的 /web/api 同样回 JSON 404，与子路径口径一致。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await web_get(app, "/web/api")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


async def test_fallback_does_not_swallow_static_or_known_routes(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """兜底路由排在静态挂载之后：静态资源与既有入口路由全部正常命中。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    static_response = await web_get(app, "/web/static/app.js")
    index_response = await web_get(app, "/web")
    root_response = await web_get(app, "/")

    assert static_response.status_code == 200
    assert index_response.status_code == 200
    assert root_response.status_code == 307


async def test_web_disabled_keeps_default_plain_404(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """默认关闭时不注册兜底路由，未知路径保持 Starlette 默认 404 形态。"""
    write_workspace_config(tmp_path)
    app = build_web_app(web_enabled=False)

    response = await web_get(app, "/random/nowhere")

    assert response.status_code == 404
    assert not response.headers.get("content-type", "").startswith("text/html")


async def test_trailing_slash_redirects_to_trimmed_path(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """尾斜杠路径 307 到去尾斜杠形态，恢复被兜底路由吞掉的 redirect_slashes 语义。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with web_asgi_client(app) as client:
        mcp_response = await client.get("/mcp/", follow_redirects=False)
        web_response = await client.get("/web/", follow_redirects=False)
        unknown_final = await client.get("/unknown/", follow_redirects=True)

    assert mcp_response.status_code == 307
    assert mcp_response.headers["location"] == "/mcp"
    assert web_response.status_code == 307
    assert web_response.headers["location"] == "/web"
    assert unknown_final.status_code == 404


async def test_protocol_relative_path_not_redirected(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """协议相对形态 //host 去尾斜杠后仍是开放重定向目标，不走重定向落 404。

    请求以绝对 URL 直发：相对路径形态会被 httpx 按 RFC 3986 join 成外域地址。
    """
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with web_asgi_client(app) as client:
        response = await client.get("http://127.0.0.1//evil.com/", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers


async def test_backslash_protocol_relative_path_not_redirected(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """反斜杠与百分号编码形态的协议相对目标不重定向，封堵归一绕过的开放重定向。

    浏览器把特殊 scheme 路径中的反斜杠按斜杠解析，/\\evil.com 会跳到外域；请求
    以绝对 URL 直发，httpx 把字面反斜杠编码为 %5C，两种形态服务端同形处理。
    """
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with web_asgi_client(app) as client:
        backslash_response = await client.get(
            "http://127.0.0.1/\\evil.com/", follow_redirects=False
        )
        encoded_response = await client.get("http://127.0.0.1/%5Cevil.com/", follow_redirects=False)

    assert backslash_response.status_code == 404
    assert "location" not in backslash_response.headers
    assert encoded_response.status_code == 404
    assert "location" not in encoded_response.headers


async def test_decoded_backslash_path_not_redirected(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """解码后携带字面反斜杠的路径不重定向：生产服务器按 ASGI 规范解码 scope path。

    httpx 传输栈不解码百分号序列，字面反斜杠形态经合成 scope 直调兜底 handler
    锁定，确保 uvicorn 解码路径下的同形请求落 404。
    """
    from starlette.requests import Request

    from seedream_mcp.webapp import meta as meta_module

    prepare_static_dir(monkeypatch, tmp_path)
    scope = _page_scope("/\\evil.com/")

    response = await meta_module.web_not_found(Request(scope))

    assert response.status_code == 404


async def test_non_ascii_tail_slash_path_redirects_percent_encoded(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """含非 ASCII 字符（中文路径）的尾斜杠请求 307 到百分号编码形态。

    Location 头须可 latin-1 编码，非 ASCII 字符经 UTF-8 百分号编码后浏览器按
    归一化规则解码跟随，重定向便利对全部输入成立。
    """
    from urllib.parse import quote

    from starlette.requests import Request

    from seedream_mcp.webapp import meta as meta_module

    prepare_static_dir(monkeypatch, tmp_path)
    scope = _page_scope("/图库/")

    response = await meta_module.web_not_found(Request(scope))

    assert response.status_code == 307
    expected = "".join(
        char if ord(char) < 128 else quote(char, encoding="utf-8") for char in "/图库"
    )
    assert response.headers["location"] == expected
    response.headers["location"].encode("latin-1")


async def test_static_mount_denies_html_direct_access(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """静态挂载封禁 html 直达：页面只经 meta 端点携带安全头直出，JS 资源仍 200。

    大写 .HTML 变体同样拒绝：Windows 文件系统大小写不敏感会命中页面文件，
    小写匹配放行即绕过封禁；大小写不敏感文件系统之外该形态本就无文件可命中。
    封禁逻辑耦合 _GuardedStaticFiles 覆盖的 Starlette 内部方法 file_response
    与 SDK 私有属性 mcp._custom_starlette_routes，升级时本组用例为适配检查点。
    """
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    html_response = await web_get(app, "/web/static/index.html")
    html_upper_response = await web_get(app, "/web/static/index.HTML")
    script_response = await web_get(app, "/web/static/app.js")

    assert html_response.status_code == 404
    assert html_upper_response.status_code == 404
    assert script_response.status_code == 200


async def test_static_mount_denies_html_trailing_punctuation_variants(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """尾随斜杠、点与空格形态的 html 路径同样 404，封堵归一化绕过。

    Starlette normpath 剥尾斜杠、Win32 路径归一剥尾部点与空格，剥后仍命中
    真实页面文件；封禁判定不同口径归一即被这三种形态绕过直出页面。
    """
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    trailing_slash = await web_get(app, "/web/static/index.html/")
    trailing_dot = await web_get(app, "/web/static/index.html.")
    trailing_space = await web_get(app, "/web/static/index.html%20")

    assert trailing_slash.status_code == 404
    assert trailing_dot.status_code == 404
    assert trailing_space.status_code == 404


async def test_static_mount_denies_html_short_name_variant(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """Windows 8.3 短名形态的页面请求同样 404，封禁须按物理路径判定。

    index~1.htm 的请求路径后缀是 .htm，按路径后缀判定会放行，OS 解析短名却
    命中 index.html。短名生成随卷开关不可控，故在 lookup_path 层模拟 OS 把
    短名解析到真实文件；拦截记录非空保证别名确经模拟层命中，封禁断言不空转。
    """
    from starlette.staticfiles import StaticFiles

    from seedream_mcp.webapp.routes import _GuardedStaticFiles

    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    intercepted: list[str] = []
    original_lookup = StaticFiles.lookup_path

    def short_name_lookup(self: StaticFiles, path: str) -> tuple[str, Any]:
        if path == "index~1.htm":
            intercepted.append(path)
            return original_lookup(self, "index.html")
        return original_lookup(self, path)

    monkeypatch.setattr(_GuardedStaticFiles, "lookup_path", short_name_lookup)

    response = await web_get(app, "/web/static/index~1.htm")

    assert intercepted
    assert response.status_code == 404


async def test_page_responses_carry_security_headers(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """入口页与 404 页响应携带 CSP 与 nosniff，收敛脚本注入、iframe 嵌入与嗅探面。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    index_response = await web_get(app, "/web")
    missing_response = await web_get(app, "/random/nowhere")

    for response in (index_response, missing_response):
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert "connect-src 'self' https:" in response.headers["content-security-policy"]
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_missing_pages_fall_back_to_plain_text(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """静态页文件缺失时入口页降级纯文本 200、404 页降级纯文本 404，不落 500。"""
    from _log_fakes import capture_loguru_messages

    from seedream_mcp.webapp import constants as web_constants

    empty_dir = tmp_path / "empty-static"
    empty_dir.mkdir()
    monkeypatch.setattr(web_constants, "STATIC_DIR", empty_dir)
    write_workspace_config(tmp_path)
    app = build_web_app()

    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        index_response = await web_get(app, "/web")
        missing_response = await web_get(app, "/random/nowhere")

    assert index_response.status_code == 200
    assert index_response.headers["content-type"].startswith("text/plain")
    assert "页面缺失" in index_response.text
    assert missing_response.status_code == 404
    assert missing_response.headers["content-type"].startswith("text/plain")
    # 一次降级请求一条告警，缺失原因分类落档供排障定位。
    missing_warnings = [entry for entry in warnings if "静态页" in entry and "缺失" in entry]
    assert len(missing_warnings) == 2


@pytest.mark.parametrize(
    ("handler_name", "path", "expected_status"),
    [
        ("web_index", "/web", 200),
        ("web_not_found", "/random/nowhere", 404),
    ],
    ids=["index", "not-found"],
)
async def test_page_existence_check_runs_off_event_loop(
    tmp_path: Path,
    monkeypatch: Any,
    handler_name: str,
    path: str,
    expected_status: int,
) -> None:
    """存在性检查经工作线程执行，不在事件循环上同步触碰文件系统。"""
    import threading
    from typing import IO

    from starlette.requests import Request

    import seedream_mcp.utils.io.io_file as io_file_module
    from seedream_mcp.utils.io.io_file import open_no_follow_read as original_open
    from seedream_mcp.webapp import meta as meta_module

    prepare_static_dir(monkeypatch, tmp_path)
    loop_thread = threading.get_ident()
    check_threads: list[int] = []

    def _recording_open(page: Path, **_kwargs: object) -> IO[bytes]:
        check_threads.append(threading.get_ident())
        return original_open(page)

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _recording_open)
    scope = _page_scope(path)

    response = await getattr(meta_module, handler_name)(Request(scope))

    assert response.status_code == expected_status
    assert check_threads
    assert all(ident != loop_thread for ident in check_threads)


async def test_page_existence_checked_on_every_request(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """存在性检查不缓存：每次请求都重新打开页面并派发工作线程，补装与删除即时生效。"""
    import asyncio
    from typing import IO

    from starlette.requests import Request

    import seedream_mcp.utils.io.io_file as io_file_module
    from seedream_mcp.utils.io.io_file import open_no_follow_read as original_open
    from seedream_mcp.webapp import meta as meta_module

    prepare_static_dir(monkeypatch, tmp_path)
    open_calls: list[Path] = []
    thread_dispatches: list[object] = []
    original_to_thread = asyncio.to_thread

    def _counting_open(page: Path, **_kwargs: object) -> IO[bytes]:
        open_calls.append(page)
        return original_open(page)

    async def _counting_to_thread(func: object, /, *args: object, **kwargs: object) -> object:
        thread_dispatches.append(func)
        return await original_to_thread(func, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _counting_open)
    monkeypatch.setattr(asyncio, "to_thread", _counting_to_thread)
    scope = _page_scope("/web")

    first = await meta_module.web_index(Request(scope))
    second = await meta_module.web_index(Request(scope))

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(open_calls) == 2
    assert len(thread_dispatches) == 2


async def test_static_page_existence_recovers_immediately_when_installed(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """首次缺失后页面补装：下一次请求立即恢复页面服务。"""
    from starlette.requests import Request

    from seedream_mcp.webapp import constants as web_constants
    from seedream_mcp.webapp import meta as meta_module

    empty_dir = tmp_path / "empty-static"
    empty_dir.mkdir()
    monkeypatch.setattr(web_constants, "STATIC_DIR", empty_dir)
    scope = _page_scope("/web")

    missing = await meta_module.web_index(Request(scope))
    page = empty_dir / "index.html"
    page.write_text("<!doctype html><title>web</title>", encoding="utf-8")
    recovered = await meta_module.web_index(Request(scope))

    assert missing.headers["content-type"].startswith("text/plain")
    assert "页面缺失" in bytes(missing.body).decode("utf-8")
    assert recovered.status_code == 200
    assert recovered.headers["content-type"].startswith("text/html")
    assert recovered.headers["content-length"] == str(page.stat().st_size)


async def _drive_page_response(
    response: Any, scope: Mapping[str, object]
) -> tuple[dict[str, str], bytes]:
    """直驱页面响应对象并回传小写响应头映射与 body 字节。"""
    messages = await drive_asgi_messages(response, scope)
    return asgi_start_headers(messages), asgi_body_bytes(messages)


async def test_static_page_existence_degrades_immediately_when_deleted(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """首次存在后页面被删：下一次请求立即回到优雅降级文本。"""
    from starlette.requests import Request

    from seedream_mcp.webapp import meta as meta_module

    static_dir = prepare_static_dir(monkeypatch, tmp_path)
    scope = _page_scope("/web")

    served = await meta_module.web_index(Request(scope))
    served_headers, served_body = await _drive_page_response(served, scope)
    # 发送完结释放句柄后删除页面，注入两次请求之间的消失窗口。
    (static_dir / "index.html").unlink()
    degraded = await meta_module.web_index(Request(scope))

    assert served_headers["content-type"].startswith("text/html")
    assert served_body
    assert degraded.status_code == 200
    assert degraded.headers["content-type"].startswith("text/plain")
    assert "页面缺失" in bytes(degraded.body).decode("utf-8")


async def test_static_page_symlink_degrades_to_plain_text_and_warns(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """页面以符号链接形态安装时降级纯文本且告警携带符号链接原因。

    Windows 无 O_NOFOLLOW，拒绝经 io_file 的 lstat/S_ISLNK 兜底路径产生；
    POSIX 等价路径为内核 O_NOFOLLOW 原子拒绝，同为 SymlinkRejectedError 分类。
    无法创建符号链接的环境跳过，由 monkeypatch 形态用例保底覆盖。
    """
    from starlette.requests import Request

    from _log_fakes import capture_loguru_messages
    from seedream_mcp.webapp import meta as meta_module

    static_dir = prepare_static_dir(monkeypatch, tmp_path)
    page = static_dir / "index.html"
    target = tmp_path / "elsewhere.html"
    target.write_text("<!doctype html><title>elsewhere</title>", encoding="utf-8")
    page.unlink()
    try:
        page.symlink_to(target)
    except (OSError, AttributeError):
        pytest.skip("当前进程无法创建符号链接，Windows 需开发者模式或管理员权限")

    scope = _page_scope("/web")
    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        response = await meta_module.web_index(Request(scope))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # 符号链接页面存在，降级文案归打开失败档，不误称缺失。
    assert "无法读取" in bytes(response.body).decode("utf-8")
    assert "页面缺失" not in bytes(response.body).decode("utf-8")
    matched = [entry for entry in warnings if "静态页" in entry and "符号链接拒绝" in entry]
    assert len(matched) == 1
    assert "index.html" in matched[0]


async def test_static_page_symlink_rejection_degrades_and_warns(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """打开点拒绝符号链接时降级纯文本且恰好告警一条，不依赖创建符号链接的特权。"""
    import errno
    from typing import IO

    from starlette.requests import Request

    from _log_fakes import capture_loguru_messages
    import seedream_mcp.utils.io.io_file as io_file_module
    from seedream_mcp.utils.io.io_file import SymlinkRejectedError
    from seedream_mcp.webapp import meta as meta_module

    prepare_static_dir(monkeypatch, tmp_path)

    def _rejected(path: object, **_kwargs: object) -> IO[bytes]:
        raise SymlinkRejectedError(errno.ELOOP, "拒绝读取符号链接", str(path))

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _rejected)

    scope = _page_scope("/web")
    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        response = await meta_module.web_index(Request(scope))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # 页面文件存在，降级文案归打开失败档，不误称缺失。
    assert "无法读取" in bytes(response.body).decode("utf-8")
    assert "页面缺失" not in bytes(response.body).decode("utf-8")
    matched = [entry for entry in warnings if "静态页" in entry and "符号链接拒绝" in entry]
    assert len(matched) == 1


@pytest.mark.parametrize(
    ("handler_name", "path", "expected_status", "expected_body"),
    [
        ("web_index", "/web", 200, "Web 操作台页面无法读取，详情请查看服务端日志。"),
        ("web_not_found", "/random/nowhere", 404, "404 Not Found"),
    ],
    ids=["index", "not-found"],
)
async def test_static_page_transient_io_error_uses_open_failure_text(
    tmp_path: Path,
    monkeypatch: Any,
    handler_name: str,
    path: str,
    expected_status: int,
    expected_body: str,
) -> None:
    """页面存在但打开抛瞬时 IO 错误时，入口页降级文案指向日志且不误称缺失与引导重装。

    404 页的兜底文案只陈述 404 结果，不携带页面状态断言，打开失败降级共用同文案。
    """
    import errno
    from typing import IO

    from starlette.requests import Request

    from _log_fakes import capture_loguru_messages
    import seedream_mcp.utils.io.io_file as io_file_module
    from seedream_mcp.webapp import meta as meta_module

    prepare_static_dir(monkeypatch, tmp_path)

    def _io_error(target: object, **_kwargs: object) -> IO[bytes]:
        raise OSError(errno.EIO, "simulated EIO", str(target))

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _io_error)

    scope = _page_scope(path)
    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        response = await getattr(meta_module, handler_name)(Request(scope))

    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith("text/plain")
    assert bytes(response.body).decode("utf-8") == expected_body
    # errno 细节落告警日志，降级文案指向该日志。
    matched = [entry for entry in warnings if "静态页" in entry and "IO 错误" in entry]
    assert len(matched) == 1


async def test_static_page_unreadable_regular_file_returns_500(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """常规文件不可读归 500 诊断响应，不误并入页面缺失降级。"""
    import errno
    import json
    from typing import IO

    from starlette.requests import Request

    import seedream_mcp.utils.io.io_file as io_file_module
    from seedream_mcp.webapp import meta as meta_module

    prepare_static_dir(monkeypatch, tmp_path)

    def _denied(target: object, **_kwargs: object) -> IO[bytes]:
        raise PermissionError(errno.EACCES, "simulated EACCES", str(target))

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _denied)

    response = await meta_module.web_index(Request(_page_scope("/web")))

    assert response.status_code == 500
    payload = json.loads(bytes(response.body))
    assert payload["error"] == "page_open_failed"
    assert "页面缺失" not in bytes(response.body).decode("utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 权限位仅 POSIX 生效")
async def test_static_page_mode_zero_file_returns_500(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """POSIX mode 000 的页面文件不可读，按打开失败归 500 而非缺失降级。"""
    import os

    from starlette.requests import Request

    from seedream_mcp.webapp import meta as meta_module

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root 不受权限位约束")
    static_dir = prepare_static_dir(monkeypatch, tmp_path)
    page = static_dir / "index.html"
    page.chmod(0o000)
    try:
        response = await meta_module.web_index(Request(_page_scope("/web")))
    finally:
        page.chmod(0o644)

    assert response.status_code == 500
    assert "页面缺失" not in bytes(response.body).decode("utf-8")


async def test_static_page_degrade_warning_deduped_per_page_and_reason(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """同一页面的同因降级进程内只告警一条，持续探测不无限刷日志。"""
    from starlette.requests import Request

    from _log_fakes import capture_loguru_messages
    from seedream_mcp.webapp import constants as web_constants
    from seedream_mcp.webapp import meta as meta_module

    empty_dir = tmp_path / "empty-static"
    empty_dir.mkdir()
    monkeypatch.setattr(web_constants, "STATIC_DIR", empty_dir)

    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        first = await meta_module.web_index(Request(_page_scope("/web")))
        second = await meta_module.web_index(Request(_page_scope("/web")))

    assert first.status_code == 200
    assert second.status_code == 200
    matched = [entry for entry in warnings if "静态页" in entry and "缺失" in entry]
    assert len(matched) == 1


@pytest.mark.parametrize(
    ("handler_name", "page_name", "path", "expected_status"),
    [
        ("web_index", "index.html", "/web", 200),
        ("web_not_found", "404.html", "/random/nowhere", 404),
    ],
    ids=["index", "not-found"],
)
async def test_page_replaced_during_open_window_serves_stat_consistent_response(
    tmp_path: Path,
    monkeypatch: Any,
    handler_name: str,
    page_name: str,
    path: str,
    expected_status: int,
) -> None:
    """打开时刻捕获的快照与发送内容恒一致，窗口内页面被替换不产生长短错配。

    打开点经替身取走旧快照句柄、页面路径已换为不同长度的新内容，模拟打开后
    发送前的替换窗口；响应的 content-length 与 body 必须同描述打开时刻的
    快照，替换后的请求拿到新文件的完整一致响应。
    """
    from typing import IO

    from starlette.requests import Request

    import seedream_mcp.utils.io.io_file as io_file_module
    from seedream_mcp.utils.io.io_file import open_no_follow_read as original_open
    from seedream_mcp.webapp import meta as meta_module

    static_dir = prepare_static_dir(monkeypatch, tmp_path)
    old_payload = (static_dir / page_name).read_bytes()
    snapshot = tmp_path / "old-page-snapshot.html"
    snapshot.write_bytes(old_payload)
    new_payload = "<!doctype html><p>replacement page content</p>\n".encode() * 8
    assert len(new_payload) != len(old_payload)
    (static_dir / page_name).write_bytes(new_payload)

    def _open_snapshot(page: Path, **_kwargs: object) -> IO[bytes]:
        del page
        return open(snapshot, "rb")

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _open_snapshot)
    scope = _page_scope(path)

    replaced_window = await getattr(meta_module, handler_name)(Request(scope))
    assert replaced_window.status_code == expected_status
    headers, body = await _drive_page_response(replaced_window, scope)
    assert int(headers["content-length"]) == len(old_payload)
    assert body == old_payload

    # 仅恢复打开函数，保留静态目录顶替：路径上的新内容成为后续请求的读源。
    monkeypatch.setattr(io_file_module, "open_no_follow_read", original_open)
    followup = await getattr(meta_module, handler_name)(Request(scope))
    followup_headers, followup_body = await _drive_page_response(followup, scope)
    assert followup_headers["content-type"].startswith("text/html")
    assert int(followup_headers["content-length"]) == len(followup_body) == len(new_payload)
    assert followup_body == new_payload


async def test_page_deleted_between_requests_degrades_without_500(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """端到端驱动完整发送路径：页面被删后的下一次请求降级纯文本，不出现 500。"""
    static_dir = prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    served = await web_get(app, "/web")
    (static_dir / "index.html").unlink()
    degraded = await web_get(app, "/web")

    assert served.status_code == 200
    assert served.text.startswith("<!doctype html>")
    assert degraded.status_code == 200
    assert degraded.headers["content-type"].startswith("text/plain")
    assert "页面缺失" in degraded.text


async def test_static_direct_output_carries_security_headers(
    tmp_path: Path,
    monkeypatch: Any,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """静态直出的 js/css/svg 附 nosniff 与 CSP，阻断 MIME 嗅探与 svg 同源脚本面。"""
    prepare_static_dir(monkeypatch, tmp_path)
    write_workspace_config(tmp_path)
    app = build_web_app()

    script_response = await web_get(app, "/web/static/app.js")

    assert script_response.status_code == 200
    assert script_response.headers["x-content-type-options"] == "nosniff"
    static_csp = script_response.headers["content-security-policy"]
    assert "default-src 'self'" in static_csp
    assert "script-src 'self'" in static_csp
    assert "object-src 'none'" in static_csp
    assert "frame-ancestors 'self'" in static_csp
