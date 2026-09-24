"""Web 操作台文件端点测试：缩略图、原图、HEAD/Range 语义与路径越界防护矩阵。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import IO, Any

import pytest

from _web_fixtures import (
    asgi_body_bytes,
    asgi_start_headers,
    build_web_app,
    drive_asgi_messages,
    make_png_bytes,
    web_asgi_client,
    web_get,
    write_workspace_config,
)


@pytest.fixture
def web_app_with_image(tmp_path: Path, clean_web_routes: None, reset_http_app_state: None) -> Any:
    """带一张真实 PNG 的 Web 传输栈。"""
    images_root = write_workspace_config(tmp_path)
    day_dir = images_root / "2026-08-20" / "text_to_image"
    day_dir.mkdir(parents=True)
    (day_dir / "a.png").write_bytes(make_png_bytes())
    app = build_web_app()
    return app


def _image_payload(tmp_path: Path) -> bytes:
    """读取测试工作区内那张 PNG 的字节。"""
    return (
        tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png"
    ).read_bytes()


class _SpyHandle:
    """包装真实句柄记录 read 调用与关闭状态的探针。"""

    def __init__(self, inner: IO[bytes]) -> None:
        self.inner = inner
        self.read_calls: list[int] = []
        self.closed = False

    def fileno(self) -> int:
        return self.inner.fileno()

    def read(self, size: int = -1) -> bytes:
        self.read_calls.append(size)
        return self.inner.read(size)

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self.inner.seek(offset, whence)

    def close(self) -> None:
        self.closed = True
        self.inner.close()


class _CloseFailureSpyHandle(_SpyHandle):
    """close 仍落位但抛 OSError 的探针，模拟响应送达后的 EIO/ESTALE。"""

    def close(self) -> None:
        self.inner.close()
        self.closed = True
        raise OSError("simulated EIO on close")


class _BlockedReadHandle(_SpyHandle):
    """block_read 置位后 read 阻塞直至放行并记录事件时序的句柄，fd 转发真实内层。"""

    def __init__(self, inner: IO[bytes], payload: bytes, *, close_delay: float = 0.0) -> None:
        super().__init__(inner)
        self.payload = payload
        self.close_delay = close_delay
        self.block_read = False
        self.read_entered = threading.Event()
        self.release_read = threading.Event()
        self.events: list[str] = []

    def read(self, size: int = -1) -> bytes:
        self.events.append(f"read:{size}")
        if self.block_read:
            self.read_entered.set()
            self.release_read.wait(timeout=10)
        self.events.append("read-done")
        return self.payload

    def close(self) -> None:
        if self.close_delay:
            time.sleep(self.close_delay)
        self.events.append("close")
        super().close()


class _TruncatedReadHandle(_SpyHandle):
    """首块返回 payload、其后恒返空字节的读源，模拟响应中途文件被原地截断。"""

    def __init__(self, inner: IO[bytes], payload: bytes) -> None:
        super().__init__(inner)
        self.payload = payload
        self.read_count = 0

    def read(self, size: int = -1) -> bytes:
        del size
        self.read_count += 1
        if self.read_count == 1:
            return self.payload
        return b""


def _patch_stream_source(monkeypatch: pytest.MonkeyPatch, handle: Any) -> None:
    """把上游 FileResponse 的 anyio.open_file 顶替为包装指定读源，注入读行为。"""
    import anyio

    async def _open_wrapped(file: object, *args: object, **kwargs: object) -> object:
        del file, args, kwargs
        return anyio.wrap_file(handle)

    monkeypatch.setattr(anyio, "open_file", _open_wrapped)


async def test_thumbnail_returns_jpeg(web_app_with_image: Any) -> None:
    """缩略图端点返回可解码的 JPEG。"""
    response = await web_get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:3] == b"\xff\xd8\xff"


async def test_image_returns_file_with_media_type(web_app_with_image: Any) -> None:
    """原图端点按扩展名返回 PNG 与对应 media type，Content-Length 与实际字节一致。"""
    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert response.headers["content-length"] == str(len(response.content))


async def test_file_endpoints_missing_file_returns_404(web_app_with_image: Any) -> None:
    """未命中文件返回 404 而非 500。"""
    missing = await web_get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/none.png"
    )
    missing_image = await web_get(
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
    response = await web_get(web_app_with_image, f"/web/api/thumbnail?path={encoded}")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_path"


async def test_file_endpoints_reject_colon_ads_paths(web_app_with_image: Any) -> None:
    """路径任意位置含冒号一律 400，覆盖 Windows 盘符与 ADS 数据流形态。"""
    from urllib.parse import quote

    for path in ("sub\\file.png:.jpg", "file.png:$DATA", "2026-08-20/a:p/b.png"):
        encoded = quote(path, safe="")
        response = await web_get(web_app_with_image, f"/web/api/image?path={encoded}")

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_path"


async def test_thumbnail_goes_through_cache_wrapper(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缩略图端点经 cached_thumbnail_bytes 取图，命中落盘缓存或限流解码。"""
    from seedream_mcp.webapp import files as files_module

    calls: list[Path] = []

    async def _fake_cached(image_path: Path, images_root: Path) -> bytes | None:
        del images_root
        calls.append(image_path)
        return b"\xff\xd8\xffminimal"

    monkeypatch.setattr(files_module, "cached_thumbnail_bytes", _fake_cached)

    response = await web_get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xffminimal"
    assert [path.name for path in calls] == ["a.png"]


async def test_thumbnail_decode_concurrency_capped_by_semaphore(
    tmp_path: Path, web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发缩略图请求的解码并发峰值不超过 CPU_OFFLOAD_DECODE_SLOTS。"""
    import asyncio
    import time

    from seedream_mcp.utils.core.executors import CPU_OFFLOAD_DECODE_SLOTS
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
    async with web_asgi_client(web_app_with_image) as client:
        responses = await asyncio.gather(
            *(client.get(f"/web/api/thumbnail?path={path}") for path in paths)
        )

    assert all(response.status_code == 200 for response in responses)
    assert calls == len(paths)
    # 上界验证限流生效，下界验证并发真实发生，串行执行不会触到信号量语义。
    assert 2 <= peak <= CPU_OFFLOAD_DECODE_SLOTS


async def test_file_endpoints_reject_non_image_extension(web_app_with_image: Any) -> None:
    """非图片扩展名在白名单阶段拒绝。"""
    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.txt"
    )

    assert response.status_code == 400


async def test_file_endpoints_empty_path_returns_400(web_app_with_image: Any) -> None:
    """空路径参数直接拒绝。"""
    response = await web_get(web_app_with_image, "/web/api/thumbnail")

    assert response.status_code == 400


async def test_thumbnail_build_failure_returns_422(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缩略图解码返回 None 时回 422，与文件不存在的 404 分档供排障区分。"""
    from seedream_mcp.webapp import files as files_module

    async def _none(image_path: Path, images_root: Path) -> bytes | None:
        del image_path, images_root
        return None

    monkeypatch.setattr(files_module, "cached_thumbnail_bytes", _none)

    response = await web_get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "thumbnail_failed"
    assert payload["error_description"] == "缩略图生成失败"


async def test_thumbnail_source_missing_mid_request_returns_404(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解析通过后源图被后台清理删除时按缺失口径回 404，不误报生成失败。"""
    from seedream_mcp.webapp import files as files_module

    async def _missing(image_path: Path, images_root: Path) -> bytes | None:
        del images_root
        raise FileNotFoundError(image_path)

    monkeypatch.setattr(files_module, "cached_thumbnail_bytes", _missing)

    response = await web_get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "not_found"


async def test_thumbnail_transient_stat_failure_returns_422(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """源图存在但 stat 瞬时失败（权限、共享冲突）不谎报缺失，按生成失败归 422。"""
    from seedream_mcp.webapp import files as files_module

    async def _denied(image_path: Path, images_root: Path) -> bytes | None:
        del images_root
        raise PermissionError(image_path)

    monkeypatch.setattr(files_module, "cached_thumbnail_bytes", _denied)

    response = await web_get(
        web_app_with_image, "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "thumbnail_failed"


async def test_thumbnail_pool_closed_returns_500(web_app_with_image: Any) -> None:
    """CPU 卸载池关闭取消排队解码时回 500 服务不可用，不落入 422 图片缺陷口径。"""
    import asyncio

    from _cpu_offload_spy import saturate_cpu_offload_pool
    from seedream_mcp.utils.core.executors import shutdown_cpu_offload_executor

    async with saturate_cpu_offload_pool() as saturated:
        path = "/web/api/thumbnail?path=2026-08-20/text_to_image/a.png"
        request_task = asyncio.ensure_future(web_get(web_app_with_image, path))
        await saturated.wait_queued()
        shutdown_cpu_offload_executor()
        response = await request_task

    await asyncio.wait_for(asyncio.gather(*saturated.tasks), timeout=5)

    assert response.status_code == 500
    payload = response.json()
    assert payload["error"] == "thumbnail_unavailable"


async def test_image_endpoint_source_vanished_before_send_returns_404(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解析通过后、发送前源图消失时按缺失口径回 404，不落流式发送失败。"""
    from seedream_mcp.webapp import files as files_module

    real_resolve = files_module.resolve_web_relative_path

    def _resolve_then_unlink(rel: str, images_root: Path) -> Path:
        # resolve 的存在性检查通过后删除源文件，注入发送前的消失窗口。
        resolved = real_resolve(rel, images_root)
        resolved.unlink()
        return resolved

    monkeypatch.setattr(files_module, "resolve_web_relative_path", _resolve_then_unlink)

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "not_found"


async def test_image_endpoint_symlink_swapped_after_resolve_rejected(
    web_app_with_image: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """越界判定通过后源图被换为指向目录外的符号链接时，打开点拒绝且不读出目标内容。"""
    import os

    from seedream_mcp.webapp import files as files_module

    secret = tmp_path / "secret.png"
    secret.write_bytes(b"secret-payload-not-for-web")
    probe = tmp_path / "probe.png"
    try:
        os.symlink(secret, probe)
    except (OSError, AttributeError):
        pytest.skip("当前进程无法创建符号链接，Windows 需开发者模式或管理员权限")
    probe.unlink()

    real_resolve = files_module.resolve_web_relative_path

    def _resolve_then_swap_symlink(rel: str, images_root: Path) -> Path:
        # 边界校验通过后把源图替换为指向目录外的符号链接，注入换链窗口。
        resolved = real_resolve(rel, images_root)
        resolved.unlink()
        os.symlink(secret, resolved)
        return resolved

    monkeypatch.setattr(files_module, "resolve_web_relative_path", _resolve_then_swap_symlink)

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 404
    assert b"secret-payload-not-for-web" not in response.content
    assert secret.exists()


async def test_image_endpoint_directory_named_like_image_returns_404(
    web_app_with_image: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目录名带图片扩展名时按缺失口径回 404，Windows 的 EACCES 打开形态不落 500。"""
    from seedream_mcp.webapp import files as files_module

    real_resolve = files_module.resolve_web_relative_path

    def _resolve_then_replace_with_directory(rel: str, images_root: Path) -> Path:
        # 存在性检查通过后把源图换为同名目录，注入解析后打开前的目录形态。
        resolved = real_resolve(rel, images_root)
        resolved.unlink()
        resolved.mkdir()
        return resolved

    monkeypatch.setattr(
        files_module, "resolve_web_relative_path", _resolve_then_replace_with_directory
    )

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "not_found"


async def test_image_endpoint_non_regular_handle_returns_404(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """句柄打开成功但非常规文件（字符设备等）时按缺失口径归 404。"""
    import os
    from typing import IO

    import seedream_mcp.utils.io.io_file as io_file_module

    def _open_device(path: object, **_kwargs: object) -> IO[bytes]:
        del path
        return os.fdopen(os.open(os.devnull, os.O_RDONLY), "rb")

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _open_device)

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "not_found"


@pytest.mark.parametrize("endpoint", ["thumbnail", "image"])
async def test_file_endpoints_images_root_unavailable_returns_400(
    endpoint: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """图片目录不可解析时缩略图与原图端点均回 400 images_root_unavailable。"""
    import seedream_mcp.utils.io.io_path as io_path_module
    from seedream_mcp.config import SeedreamConfig, set_active_config

    def _unresolvable(configured_dir: str) -> Path:
        del configured_dir
        raise OSError("simulated unresolvable path")

    set_active_config(SeedreamConfig(api_key="test_key", data_root=str(tmp_path / "pics")))
    monkeypatch.setattr(io_path_module, "resolve_cached_data_root", _unresolvable)
    app = build_web_app()

    response = await web_get(app, f"/web/api/{endpoint}?path=2026-08-20/text_to_image/a.png")

    assert response.status_code == 400
    assert response.json()["error"] == "images_root_unavailable"


async def test_image_etag_matches_fileresponse_algorithm(
    web_app_with_image: Any, tmp_path: Path
) -> None:
    """etag 为 md5(mtime,size) 引号形态，与 FileResponse 同算法保缓存兼容。"""
    import hashlib

    stat_result = os.stat(
        tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png"
    )
    etag_base = f"{stat_result.st_mtime}-{stat_result.st_size}"
    expected = f'"{hashlib.md5(etag_base.encode()).hexdigest()}"'

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.headers["etag"] == expected
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["last-modified"].endswith("GMT")


async def test_image_head_returns_headers_without_reading_body(
    web_app_with_image: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEAD 只发头不做分块读，content-length 为全文件长度，句柄随即关闭。"""
    import seedream_mcp.utils.io.io_file as io_file_module

    payload = _image_payload(tmp_path)
    spy = _SpyHandle(
        open(tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png", "rb")
    )
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: spy)

    async with web_asgi_client(web_app_with_image) as client:
        response = await client.head("/web/api/image?path=2026-08-20/text_to_image/a.png")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["etag"]
    assert response.headers["accept-ranges"] == "bytes"
    assert spy.read_calls == []
    assert spy.closed


async def test_image_head_with_range_returns_206_headers_only(web_app_with_image: Any) -> None:
    """HEAD 携带单区间 Range 时只回 206 头部，content-length 为区间长度。"""
    async with web_asgi_client(web_app_with_image) as client:
        response = await client.head(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=0-9"},
        )

    assert response.status_code == 206
    assert response.content == b""
    assert response.headers["content-length"] == "10"
    assert "content-range" in response.headers


async def test_image_single_range_returns_206(web_app_with_image: Any, tmp_path: Path) -> None:
    """单区间 Range 回 206 与 content-range，body 为区间字节。"""
    payload = _image_payload(tmp_path)

    async with web_asgi_client(web_app_with_image) as client:
        response = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": f"bytes=0-{min(9, len(payload) - 1)}"},
        )

    assert response.status_code == 206
    assert response.content == payload[:10]
    assert response.headers["content-range"] == f"bytes 0-9/{len(payload)}"
    assert response.headers["content-length"] == "10"
    assert response.headers["accept-ranges"] == "bytes"


async def test_image_open_ended_and_suffix_ranges(web_app_with_image: Any, tmp_path: Path) -> None:
    """开区间与后缀区间形态按 FileResponse 语义折算为 206。"""
    payload = _image_payload(tmp_path)
    tail = 7

    async with web_asgi_client(web_app_with_image) as client:
        open_ended = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=10-"},
        )
        suffix = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": f"bytes=-{tail}"},
        )

    assert open_ended.status_code == 206
    assert open_ended.content == payload[10:]
    assert open_ended.headers["content-range"] == f"bytes 10-{len(payload) - 1}/{len(payload)}"
    assert suffix.status_code == 206
    assert suffix.content == payload[-tail:]
    assert (
        suffix.headers["content-range"]
        == f"bytes {len(payload) - tail}-{len(payload) - 1}/{len(payload)}"
    )


async def test_image_multiple_ranges_returns_206_multipart(
    web_app_with_image: Any, tmp_path: Path
) -> None:
    """多区间按上游 FileResponse 行为回 206 multipart/byteranges。"""
    payload = _image_payload(tmp_path)

    async with web_asgi_client(web_app_with_image) as client:
        response = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=0-4,6-9"},
        )

    assert response.status_code == 206
    assert response.headers["content-type"].startswith("multipart/byteranges; boundary=")
    assert response.headers["content-length"] == str(len(response.content))
    boundary = response.headers["content-type"].split("boundary=")[1].encode()

    segments = response.content.split(b"--" + boundary)
    assert segments[0] == b""
    assert segments[-1] == b"--"
    first = segments[1][2:-2]
    second = segments[2][2:-2]
    first_head, _, first_body = first.partition(b"\r\n\r\n")
    second_head, _, second_body = second.partition(b"\r\n\r\n")
    assert f"Content-Range: bytes 0-4/{len(payload)}".encode() in first_head
    assert b"Content-Type: image/png" in first_head
    assert first_body == payload[:5]
    assert f"Content-Range: bytes 6-9/{len(payload)}".encode() in second_head
    assert second_body == payload[6:10]


async def test_image_overlapping_ranges_merge_to_single_206(
    web_app_with_image: Any, tmp_path: Path
) -> None:
    """重叠区间经上游合并为单区间 206，不落 multipart。"""
    payload = _image_payload(tmp_path)

    async with web_asgi_client(web_app_with_image) as client:
        response = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=0-4,2-9"},
        )

    assert response.status_code == 206
    assert response.content == payload[:10]
    assert response.headers["content-range"] == f"bytes 0-9/{len(payload)}"
    assert not response.headers["content-type"].startswith("multipart/byteranges")


async def test_image_reversed_range_returns_400(web_app_with_image: Any) -> None:
    """倒置区间按畸形 400 拒绝，混入合法区间与 merge 折叠都不得漏检为 206。"""
    async with web_asgi_client(web_app_with_image) as client:
        reversed_only = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=5-4"},
        )
        reversed_among_valid = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=0-1,9-8"},
        )
        reversed_mergeable = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=5-4,3-9"},
        )

    assert reversed_only.status_code == 400
    assert reversed_among_valid.status_code == 400
    assert reversed_mergeable.status_code == 400


async def test_image_response_parity_with_fileresponse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同输入下与上游 FileResponse 输出逐消息一致，boundary 固定，锁语义收敛不漂移。"""
    import hashlib
    import os

    import seedream_mcp.webapp._responses as responses_module
    from seedream_mcp.utils.io.io_file import open_no_follow_read
    from seedream_mcp.webapp._responses import PRIVATE_CACHE_HEADER
    from starlette import responses as starlette_responses
    from starlette.responses import FileResponse

    monkeypatch.setattr(starlette_responses, "token_hex", lambda num_bytes: "fixedboundary")

    source = tmp_path / "a.png"
    source.write_bytes(make_png_bytes())
    stat_result = os.stat(source)
    etag_base = f"{stat_result.st_mtime}-{stat_result.st_size}"
    etag = f'"{hashlib.md5(etag_base.encode()).hexdigest()}"'.encode()

    cases: list[tuple[str, list[tuple[bytes, bytes]]]] = [
        ("GET", []),
        ("GET", [(b"range", b"bytes=0-9")]),
        ("GET", [(b"range", b"bytes=10-")]),
        ("GET", [(b"range", b"bytes=-7")]),
        ("GET", [(b"range", b"bytes=0-4,6-9")]),
        ("GET", [(b"range", b"bytes=0-4,2-9")]),
        ("GET", [(b"range", b"bytes=999999-")]),
        ("GET", [(b"range", b"bytes=abc")]),
        ("GET", [(b"range", b"bytes=5-2")]),
        ("GET", [(b"range", b"bytes=0-9"), (b"if-range", etag)]),
        ("GET", [(b"range", b"bytes=0-9"), (b"if-range", b'"stale-etag"')]),
        ("HEAD", []),
        ("HEAD", [(b"range", b"bytes=0-9")]),
        ("HEAD", [(b"range", b"bytes=0-4,6-9")]),
    ]
    for method, headers in cases:
        source_handle = open_no_follow_read(source)
        ours = responses_module._NoFollowFileResponse(
            source_handle,
            stat_result,
            media_type="image/png",
            headers=dict(PRIVATE_CACHE_HEADER),
        )
        upstream = FileResponse(
            source,
            headers=dict(PRIVATE_CACHE_HEADER),
            media_type="image/png",
            stat_result=stat_result,
        )
        assert await _drive_response(ours, method, headers) == await _drive_response(
            upstream, method, headers
        ), (method, headers)


async def test_image_stream_read_dispatch_uses_large_chunks(
    web_app_with_image: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全量 GET 的每次线程派发读取不低于 512KB，锁线程往返收敛。"""
    import seedream_mcp.utils.io.io_file as io_file_module
    from seedream_mcp.webapp._responses import _NoFollowFileResponse

    image_path = tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png"
    chunk = _NoFollowFileResponse.chunk_size
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(chunk * 2 + 12345))
    spy = _SpyHandle(open(image_path, "rb"))
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: spy)
    _patch_stream_source(monkeypatch, spy)

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(response.content))
    assert chunk >= 512 * 1024
    assert spy.read_calls == [chunk, chunk, chunk]


async def test_image_unsatisfiable_range_returns_416(
    web_app_with_image: Any, tmp_path: Path
) -> None:
    """区间起点超出文件长度回 416 并携带 bytes */长度。"""
    payload = _image_payload(tmp_path)

    async with web_asgi_client(web_app_with_image) as client:
        response = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": f"bytes={len(payload)}-"},
        )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(payload)}"


@pytest.mark.parametrize("range_header", ["bytes=abc", "octets=0-9", "bytes=5-2", "bytes="])
async def test_image_malformed_range_returns_400(
    web_app_with_image: Any, range_header: str
) -> None:
    """畸形 Range 头回 400，不落入 200 全量。"""
    async with web_asgi_client(web_app_with_image) as client:
        response = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": range_header},
        )

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("range_header", "expected_status"),
    [("bytes=abc", 400), ("bytes=999999-", 416)],
)
async def test_image_range_error_responses_close_handle(
    web_app_with_image: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    range_header: str,
    expected_status: int,
) -> None:
    """畸形与不可满足 Range 的早退分支显式关闭句柄，不依赖 GC 释放。"""
    import seedream_mcp.utils.io.io_file as io_file_module

    spy = _SpyHandle(
        open(tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png", "rb")
    )
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: spy)

    async with web_asgi_client(web_app_with_image) as client:
        response = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": range_header},
        )

    assert response.status_code == expected_status
    assert spy.read_calls == []
    assert spy.closed


async def test_image_if_range_matching_validator_honors_range(
    web_app_with_image: Any, tmp_path: Path
) -> None:
    """If-Range 与 etag 或 last-modified 一致时按 Range 应答 206。"""
    async with web_asgi_client(web_app_with_image) as client:
        full = await client.get("/web/api/image?path=2026-08-20/text_to_image/a.png")
        by_etag = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=0-9", "If-Range": full.headers["etag"]},
        )
        by_last_modified = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=0-9", "If-Range": full.headers["last-modified"]},
        )

    assert by_etag.status_code == 206
    assert by_last_modified.status_code == 206


async def test_image_if_range_stale_ignores_range(web_app_with_image: Any, tmp_path: Path) -> None:
    """If-Range 校验器过期时忽略 Range，回 200 全量。"""
    payload = _image_payload(tmp_path)

    async with web_asgi_client(web_app_with_image) as client:
        response = await client.get(
            "/web/api/image?path=2026-08-20/text_to_image/a.png",
            headers={"Range": "bytes=0-9", "If-Range": '"stale-etag"'},
        )

    assert response.status_code == 200
    assert response.content == payload


async def test_image_open_denied_returns_500(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """权限等打开失败保留 500 诊断信号，不折叠为缺失。"""
    import seedream_mcp.utils.io.io_file as io_file_module

    def _denied(path: object, **_kwargs: object) -> IO[bytes]:
        del path
        raise PermissionError("simulated EACCES")

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _denied)

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 500
    payload = response.json()
    assert payload["error"] == "image_open_failed"


async def test_image_open_symlink_rejection_returns_404(
    web_app_with_image: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """符号链接拒绝按缺失口径归 404，与权限类 500 分档。"""
    import errno

    import seedream_mcp.utils.io.io_file as io_file_module
    from seedream_mcp.utils.io.io_file import SymlinkRejectedError

    def _rejected(path: object, **_kwargs: object) -> IO[bytes]:
        raise SymlinkRejectedError(errno.ELOOP, "拒绝读取符号链接", str(path))

    monkeypatch.setattr(io_file_module, "open_no_follow_read", _rejected)

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


async def test_image_fstat_failure_closes_handle_and_returns_500(
    web_app_with_image: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fstat 抛错时句柄先行关闭再上抛，响应按 500 分档不谎报缺失。"""
    import seedream_mcp.utils.io.io_file as io_file_module

    spy = _SpyHandle(
        open(tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png", "rb")
    )
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: spy)

    def _failing_fstat(fd: int) -> os.stat_result:
        del fd
        raise OSError("simulated ESTALE")

    monkeypatch.setattr(os, "fstat", _failing_fstat)

    response = await web_get(
        web_app_with_image, "/web/api/image?path=2026-08-20/text_to_image/a.png"
    )

    assert response.status_code == 500
    assert response.json()["error"] == "image_open_failed"
    assert spy.closed


def _image_scope(
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
) -> dict[str, object]:
    """构造直驱响应对象的最小 HTTP scope。"""
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/web/api/image",
        "raw_path": b"/web/api/image",
        "query_string": query_string,
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 80),
    }


async def _drive_response(
    response: Any, method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None
) -> list[dict[str, object]]:
    """以共享驱动器直驱响应对象并收集全部 ASGI 消息。"""
    return await drive_asgi_messages(response, _image_scope(method, headers))


def _make_response(handle: IO[bytes], source: Path) -> Any:
    """以指定读源句柄构造原图响应对象，复制品 fd 的生产保持生产侧的延迟形态。"""
    import os

    from seedream_mcp.webapp._responses import _NoFollowFileResponse

    return _NoFollowFileResponse(handle, os.stat(source), media_type="application/octet-stream")


async def test_image_response_single_cancel_settles_read_and_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单次取消击中在途读时读线程照常结算、句柄恰好关闭一次，事件循环不冻结。"""
    import asyncio
    from typing import cast

    payload = b"x" * 16
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    handle = _BlockedReadHandle(open(source, "rb"), payload)
    response = _make_response(cast("IO[bytes]", handle), source)
    _patch_stream_source(monkeypatch, cast("IO[bytes]", handle))

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        del message

    handle.block_read = True
    task = asyncio.ensure_future(response(_image_scope(), receive, send))
    await asyncio.to_thread(handle.read_entered.wait, 10)

    beats = 0

    async def _heartbeat() -> None:
        nonlocal beats
        for _ in range(500):
            if handle.closed:
                break
            await asyncio.sleep(0.01)
            beats += 1

    heartbeat = asyncio.ensure_future(_heartbeat())
    task.cancel()
    await asyncio.sleep(0.1)

    # 在途读未结算期间事件循环保持可调度。
    assert beats >= 1

    handle.release_read.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    heartbeat.cancel()
    for _ in range(500):
        if handle.events.count("read-done") == 1:
            break
        await asyncio.sleep(0.01)

    # 任务完结即句柄已关，关闭由展开路径同步完成，无后台游离任务补关。
    assert handle.closed
    chunk = type(response).chunk_size
    # 读恰好派发一次并结算，关闭恰好发生一次，无双重关闭。
    assert handle.events.count(f"read:{chunk}") == 1
    assert handle.events.count("read-done") == 1
    assert handle.events.count("close") == 1


async def test_image_response_sticky_cancel_storm_settles_and_closes_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starlette 任务组式粘性取消风暴下读照常结算、句柄必关且仅关一次。"""
    import asyncio
    from functools import partial
    from typing import cast

    from starlette._utils import create_collapsing_task_group

    payload = b"y" * 32
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    handle = _BlockedReadHandle(open(source, "rb"), payload, close_delay=0.05)
    response = _make_response(cast("IO[bytes]", handle), source)
    _patch_stream_source(monkeypatch, cast("IO[bytes]", handle))

    async def receive() -> dict[str, object]:
        # 断连通知等在途读就位后送达，锁定取消恰好击中阻塞读。
        await asyncio.to_thread(handle.read_entered.wait, 10)
        return {"type": "http.disconnect"}

    sent: list[str] = []

    async def send(message: dict[str, object]) -> None:
        # 先让出再登记，镜像真实 socket 发送的检查点，取消在此处送达。
        await asyncio.sleep(0)
        sent.append(str(message["type"]))

    handle.block_read = True
    beats = 0

    async def _heartbeat() -> None:
        nonlocal beats
        for _ in range(1000):
            if handle.closed:
                break
            await asyncio.sleep(0.01)
            beats += 1

    heartbeat = asyncio.ensure_future(_heartbeat())

    async def _listen_for_disconnect() -> None:
        message = await receive()
        assert message["type"] == "http.disconnect"

    async with create_collapsing_task_group() as task_group:

        async def _wrap(func: Any) -> None:
            await func()
            task_group.cancel_scope.cancel()

        task_group.start_soon(_wrap, partial(response, _image_scope(), receive, send))
        await _wrap(_listen_for_disconnect)
        # 放行在途读，粘性取消在读结算后的首个非屏蔽检查点送达并终结响应。
        handle.release_read.set()

    # 事件循环全程可调度，粘性取消下响应只发出过起始行。
    assert beats >= 1
    assert sent == ["http.response.start"]
    heartbeat.cancel()

    # 任务组退出即任务全部完结：读已在屏蔽作用域内完成结算，关闭由收尾同步落位，
    # 不存在等后台任务补关的游离形态。
    assert handle.closed
    chunk = type(response).chunk_size
    assert handle.events.count(f"read:{chunk}") == 1
    assert handle.events.count("read-done") == 1
    assert handle.events.count("close") == 1


async def test_image_multiple_ranges_truncated_source_aborts_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多区间读源中途截断时断开连接，不发送总长不足 content-length 的完整帧。"""
    import asyncio
    from typing import cast

    from seedream_mcp.webapp._responses import _TruncatedSourceError

    source = tmp_path / "payload.bin"
    source.write_bytes(b"z" * 40)
    handle = _TruncatedReadHandle(open(source, "rb"), b"0123456789")
    response = _make_response(cast("IO[bytes]", handle), source)
    _patch_stream_source(monkeypatch, cast("IO[bytes]", handle))

    messages: list[dict[str, object]] = []
    with pytest.raises(_TruncatedSourceError):
        await asyncio.wait_for(
            drive_asgi_messages(
                response, _image_scope("GET", [(b"range", b"bytes=0-29,35-39")]), messages
            ),
            timeout=5,
        )

    body_messages = [message for message in messages if message["type"] == "http.response.body"]
    headers = asgi_start_headers(messages)
    sent_bytes = sum(len(cast("bytes", message["body"])) for message in body_messages)
    # 未发出任何 more_body False 的完整帧，已发字节不足以覆盖预告的 content-length。
    assert all(message["more_body"] for message in body_messages)
    assert sent_bytes < int(headers["content-length"])
    # 一次成功读加一次零长读即中止，句柄随异常收尾关闭。
    assert handle.read_count == 2
    assert handle.closed


async def test_image_response_skips_pathsend_extension(tmp_path: Path) -> None:
    """scope 声明 pathsend 扩展时不按路径移交服务器，仍以预开 fd 流式读出全量。"""
    import asyncio
    from typing import cast

    from seedream_mcp.utils.io.io_file import open_no_follow_read

    payload = b"q" * 24
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    handle = open_no_follow_read(source)
    response = _make_response(handle, source)

    scope = _image_scope()
    scope["extensions"] = {"http.response.pathsend": {}}

    messages = await asyncio.wait_for(drive_asgi_messages(response, scope), timeout=5)

    assert all(message["type"] != "http.response.pathsend" for message in messages)
    chunks = [
        cast("bytes", message["body"])
        for message in messages
        if message["type"] == "http.response.body"
    ]
    assert b"".join(chunks) == payload
    assert chunks[-1] and not messages[-1]["more_body"]


async def test_web_image_client_disconnect_stream_completes_and_closes_handle(
    web_app_with_image: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """客户端断开通知不打断 FileResponse 流式，走完后句柄正常关闭。"""
    import asyncio
    from urllib.parse import urlencode

    import seedream_mcp.utils.io.io_file as io_file_module

    image_path = tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png"
    spy = _SpyHandle(open(image_path, "rb"))
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: spy)
    _patch_stream_source(monkeypatch, spy)

    scope = _image_scope(
        headers=[(b"host", b"127.0.0.1")],
        query_string=urlencode({"path": "2026-08-20/text_to_image/a.png"}).encode(),
    )
    body_seen = asyncio.Event()
    disconnected = asyncio.Event()

    async def receive() -> dict[str, object]:
        if not disconnected.is_set():
            disconnected.set()
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            body_seen.set()

    task = asyncio.ensure_future(web_app_with_image(scope, receive, send))
    await asyncio.wait_for(body_seen.wait(), timeout=10)
    await asyncio.wait_for(task, timeout=10)

    assert spy.closed
    assert spy.read_calls


async def test_web_image_send_failure_surfaces_and_closes_handle(
    web_app_with_image: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """发送侧断连抛错中断流式时错误照常上抛，句柄仍最终关闭。"""
    import asyncio
    from urllib.parse import urlencode

    import seedream_mcp.utils.io.io_file as io_file_module

    image_path = tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png"
    spy = _SpyHandle(open(image_path, "rb"))
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: spy)
    _patch_stream_source(monkeypatch, spy)

    scope = _image_scope(
        headers=[(b"host", b"127.0.0.1")],
        query_string=urlencode({"path": "2026-08-20/text_to_image/a.png"}).encode(),
    )

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("connection lost")

    task = asyncio.ensure_future(web_app_with_image(scope, receive, send))
    with pytest.raises(OSError, match="connection lost"):
        await asyncio.wait_for(task, timeout=10)

    # 上游 async with 在展开中同步等待关闭完成，任务完结即句柄已关。
    assert spy.closed
    assert spy.read_calls


async def test_image_head_close_failure_after_headers_keeps_response(
    web_app_with_image: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEAD 送达后兜底关闭抛错只记 warning，客户端已收完整头部且无外泄异常。"""
    from _log_fakes import capture_loguru_messages

    import seedream_mcp.utils.io.io_file as io_file_module

    payload = _image_payload(tmp_path)
    spy = _CloseFailureSpyHandle(
        open(tmp_path / ".seedream" / "images" / "2026-08-20" / "text_to_image" / "a.png", "rb")
    )
    monkeypatch.setattr(io_file_module, "open_no_follow_read", lambda _path, **_kwargs: spy)

    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        async with web_asgi_client(web_app_with_image) as client:
            response = await client.head("/web/api/image?path=2026-08-20/text_to_image/a.png")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(payload))
    assert spy.closed
    close_warnings = [entry for entry in warnings if "预开句柄关闭失败" in entry]
    assert len(close_warnings) == 1
    # 堆栈经 logger.opt(exception=True) 随告警落档。
    assert "OSError" in close_warnings[0]


async def test_image_response_send_failure_with_overlapping_cancel_keeps_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """send 抛 OSError 与重叠二次取消并发时最终异常仍是 OSError，close 仍完成。"""
    import asyncio
    from typing import cast

    payload = b"w" * 24
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    handle = _BlockedReadHandle(open(source, "rb"), payload, close_delay=0.05)
    response = _make_response(cast("IO[bytes]", handle), source)
    _patch_stream_source(monkeypatch, cast("IO[bytes]", handle))

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    task_box: list[asyncio.Task[None]] = []

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            # 先请求二次取消再抛异常，挂起的取消全程无送达点，同步关闭收尾不被打断。
            task_box[0].cancel()
            raise OSError("connection lost")

    task_box.append(asyncio.ensure_future(response(_image_scope(), receive, send)))
    with pytest.raises(OSError, match="connection lost"):
        await asyncio.wait_for(task_box[0], timeout=10)

    # 任务完结即句柄已关：收尾为同步关闭，结局 OSError 不被重叠取消改写。
    assert handle.closed
    assert handle.events == ["close"]


def test_image_cleanup_spares_fd_number_reused_for_same_file(tmp_path: Path) -> None:
    """abort 后复制品号码被并发请求复用读同一文件时，收尾清理不得误关复用 fd。

    上游 async with 退出即关闭复制品 fd，其后最低空位分配使并发请求拿到同号 fd
    读同一文件；清理若按 fstat 探测补关会把并发请求的活 fd 一并关闭。
    """
    import os

    from seedream_mcp.webapp._responses import _NoFollowFileResponse

    payload = b"v" * 64
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    handle = open(source, "rb")
    response = _NoFollowFileResponse(
        handle, os.fstat(handle.fileno()), media_type="application/octet-stream"
    )

    # 镜像上游 async with：open 经 __index__ 取走复制品，abort 后随文件对象关闭。
    upstream_file = open(response.path, "rb")
    reused_number = upstream_file.fileno()
    assert upstream_file.read(8) == payload[:8]
    upstream_file.close()

    # 复制品关闭后最低空位分配使并发请求以同号 fd 打开同一文件。
    victim = open(source, "rb")
    assert victim.fileno() == reused_number

    response._close_handle()

    assert handle.closed
    assert victim.read() == payload
    victim.close()


async def test_image_abort_leaves_concurrent_same_file_stream_intact(tmp_path: Path) -> None:
    """同图并发请求其一发送失败 abort 时，另一路流不受清理牵连，完整读到全量字节。"""
    import asyncio

    payload = b"u" * 4096
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    aborted = _make_response(open(source, "rb"), source)
    intact = _make_response(open(source, "rb"), source)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send_aborting(message: dict[str, object]) -> None:
        # 首个数据分块发送失败，复制品已生产、流式已开走后 abort。
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("connection lost")

    intact_messages: list[dict[str, object]] = []

    async def send_collecting(message: dict[str, object]) -> None:
        intact_messages.append(message)

    aborted_task = asyncio.ensure_future(aborted(_image_scope(), receive, send_aborting))
    intact_task = asyncio.ensure_future(intact(_image_scope(), receive, send_collecting))
    with pytest.raises(OSError, match="connection lost"):
        await asyncio.wait_for(aborted_task, timeout=10)
    await asyncio.wait_for(intact_task, timeout=10)

    headers = asgi_start_headers(intact_messages)
    body = asgi_body_bytes(intact_messages)
    assert headers["content-length"] == str(len(payload))
    assert body == payload
    assert aborted._handle.closed
    assert intact._handle.closed
