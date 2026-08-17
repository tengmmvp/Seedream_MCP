"""下载安全测试：DNS 解析 TTL 缓存与连接后对端 IP 公网校验。"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from seedream_mcp.utils.io.io_download import DownloadError, DownloadManager

from _download_fakes import (
    _FakeResponse,
    _FakeSession,
    _PNG_BYTES,
    _TimeoutThenSuccessSession,
    _patch_download_network,
)


def _patch_unretrieved_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> "list[asyncio.Task[Any]]":
    """把 logs.log_unretrieved_task_exception 替换为记录 task 并检索异常的替身。

    arm_unretrieved_exception_logging 登记回调时经 logs 模块全局解析目标函数，对象式
    遮蔽即生效。替身检索异常保持 "Task exception was never retrieved" 静默。返回已
    触发回调的 task 列表，供断言登记时序。
    """
    from seedream_mcp.utils.core import logs

    fired: "list[asyncio.Task[Any]]" = []

    def record(task: "asyncio.Task[Any]") -> None:
        fired.append(task)
        if not task.cancelled():
            task.exception()

    monkeypatch.setattr(logs, "log_unretrieved_task_exception", record)
    return fired


class _FakeLoop:
    def __init__(self) -> None:
        self.calls = 0

    async def getaddrinfo(self, host, port, proto):  # type: ignore[no-untyped-def]
        del host, port, proto
        self.calls += 1
        return [
            (None, None, None, None, ("8.8.8.8", 0)),
            (None, None, None, None, ("1.1.1.1", 0)),
        ]


class _BlockingFakeLoop:
    """getaddrinfo 阻塞在 gate 上，使并发解析重叠以验证在途 task 去重。"""

    def __init__(self) -> None:
        self.calls = 0
        self.gate = asyncio.Event()

    async def getaddrinfo(self, host, port, proto):  # type: ignore[no-untyped-def]
        del host, port, proto
        self.calls += 1
        await self.gate.wait()
        return [(None, None, None, None, ("8.8.8.8", 0))]


@pytest.mark.asyncio
async def test_validate_public_dns_uses_ttl_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_loop = _FakeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    manager = DownloadManager(dns_cache_ttl=60)
    await manager._validate_public_dns("example.com")
    await manager._validate_public_dns("example.com")

    assert fake_loop.calls == 1


@pytest.mark.asyncio
async def test_resolve_public_ips_dedups_inflight_resolutions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同 host 并发解析在缓存冷启动时共享同一在途 task，仅触发一次 getaddrinfo。

    getaddrinfo 阻塞在 gate 上使两并发调用重叠：首个调用 miss 缓存创建并登记在途 task，
    次个调用发现登记项后 await 同一 task，避免各自独立解析。
    """
    fake_loop = _BlockingFakeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    manager = DownloadManager(dns_cache_ttl=60)
    t1 = asyncio.create_task(manager._resolve_public_ips("example.com"))
    t2 = asyncio.create_task(manager._resolve_public_ips("example.com"))
    # 让出控制权使两任务均调度到在途等待点；getaddrinfo 已被调用一次并阻塞在 gate
    for _ in range(20):
        await asyncio.sleep(0)
        if fake_loop.calls == 1:
            break

    assert fake_loop.calls == 1
    # 在途 task 已登记且尚未完成，证明两任务共享同一在途解析
    assert "example.com" in manager._dns_inflight
    fake_loop.gate.set()
    r1, r2 = await asyncio.gather(t1, t2)

    assert r1 == r2 == ("8.8.8.8",)
    # task 完成后在途登记已清空，缓存已写入
    assert manager._dns_inflight == {}
    assert "example.com" in manager._dns_cache


@pytest.mark.asyncio
async def test_resolve_failure_consumed_by_caller_not_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在途解析抛错且由调用方正常消费时不登记"未取回异常"回调。

    常规失败路径的异常经 shield 交还调用方、由既有错误通道记录；若仍无条件挂回调，
    同一异常会以"后台共享任务失败"重复入日志。回调仅在消费方放弃等待时登记。
    """
    fired = _patch_unretrieved_callback(monkeypatch)

    class _FailingResolveLoop:
        """getaddrinfo 直接抛 OSError，构造在途解析失败。"""

        async def getaddrinfo(self, host, port, proto):  # type: ignore[no-untyped-def]
            del host, port, proto
            raise OSError("dns boom")

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _FailingResolveLoop())

    manager = DownloadManager(dns_cache_ttl=60)
    with pytest.raises(DownloadError):
        await manager._resolve_public_ips("example.com")

    # 推进事件循环跑完可能排队的 done callback 后仍无回调触发
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fired == []


@pytest.mark.asyncio
async def test_resolve_creator_cancel_arms_unretrieved_logging_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用方被取消且无其他等待者时，在途解析失败经登记的回调检索且仅触发一次。"""
    fired = _patch_unretrieved_callback(monkeypatch)

    class _GatedFailingLoop:
        """getaddrinfo 阻塞在 gate 上，放行后抛 OSError 以构造延迟的在途解析失败。"""

        def __init__(self) -> None:
            self.calls = 0
            self.gate = asyncio.Event()

        async def getaddrinfo(self, host, port, proto):  # type: ignore[no-untyped-def]
            del host, port, proto
            self.calls += 1
            await self.gate.wait()
            raise OSError("dns boom")

    fake_loop = _GatedFailingLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    manager = DownloadManager(dns_cache_ttl=60)
    caller = asyncio.create_task(manager._resolve_public_ips("example.com"))
    # 让出控制权使 caller 调度到在途等待点；getaddrinfo 已被调用一次并阻塞在 gate
    for _ in range(20):
        await asyncio.sleep(0)
        if fake_loop.calls == 1:
            break
    assert "example.com" in manager._dns_inflight

    inflight = manager._dns_inflight["example.com"]
    caller.cancel()
    fake_loop.gate.set()

    done, pending = await asyncio.wait({caller, inflight})
    assert pending == set()
    assert done == {caller, inflight}
    assert caller.cancelled()
    # 推进事件循环跑完 inflight 完成时排队的 done callback
    await asyncio.sleep(0)

    assert fired == [inflight]
    assert isinstance(inflight.exception(), DownloadError)


def test_validate_connected_peer_ip_blocks_non_public_ip() -> None:
    manager = DownloadManager()
    fake_response = _FakeResponse(peer_ip="127.0.0.1")

    with pytest.raises(DownloadError, match="非公网地址"):
        manager._validate_connected_peer_ip(  # type: ignore[arg-type]
            fake_response, "https://example.com"
        )


def test_validate_connected_peer_ip_allows_public_ip() -> None:
    manager = DownloadManager()
    fake_response = _FakeResponse(peer_ip="8.8.8.8")

    manager._validate_connected_peer_ip(  # type: ignore[arg-type]
        fake_response, "https://example.com"
    )


# ---- download_image 端到端：逐跳重定向校验与重试退避 ----
# mock 网络层，验证把各 SSRF 子组件串联起来的 download_image 主循环：
# 重定向目标须重新走静态校验、重定向上限、5xx 退避重试后成功落盘。
# 重定向到内网/回环的安全拒绝由 *_via_real_static_validation 用例覆盖（保留真实
# _validate_url_for_request 串联），避免架空校验的空芯用例。


@pytest.mark.asyncio
async def test_download_image_rejects_redirect_to_private_ip_via_real_static_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端串联：保留真实 _validate_url_for_request 的重定向内网拒绝。

    逐跳重定向到内网 IP 须被静态校验拒绝。上述两条重定向用例经 _patch_download_network
    把 _validate_url_for_request 架空为直通，实测的是重定向上限而非安全拒绝。本用例仅
    stub 依赖网络的 DNS 解析与 session 注入，保留真实的 _validate_url_for_request 串联，
    使 302 目标 169.254.169.254 经 _validate_url_static 命中非公网判定被拒绝，覆盖
    SSRF 第四层防护的端到端安全语义。
    """
    manager = DownloadManager()
    session = _FakeSession(
        [_FakeResponse(302, {"location": "http://169.254.169.254/latest/meta-data/"})]
    )

    async def _pass_dns(host: str) -> None:
        del host

    async def _fake_ensure_session() -> Any:
        return session

    monkeypatch.setattr(manager, "_validate_public_dns", _pass_dns)
    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)

    with pytest.raises(DownloadError, match="不安全|非公网"):
        # 起始 URL 用 http 使降级检查不先触发，保证静态校验路径真实可达
        await manager.download_image("http://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_redirect_to_loopback_via_real_static_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端串联：302 跳转至回环地址须被真实 _validate_url_static 拒绝。

    起始 URL 用 http 使降级检查不先触发，保证静态校验路径真实可达。
    """
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "http://127.0.0.1/"})])

    async def _pass_dns(host: str) -> None:
        del host

    async def _fake_ensure_session() -> Any:
        return session

    monkeypatch.setattr(manager, "_validate_public_dns", _pass_dns)
    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)

    with pytest.raises(DownloadError, match="不安全|非公网"):
        await manager.download_image("http://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_https_to_http_downgrade_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """https 起始的下载不允许经重定向降级到 http，消除明文链路攻击面。"""
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "http://mirror.example.com/x.png"})])
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError, match="降级"):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_uppercase_scheme_downgrade_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """初始 URL scheme 为大写 HTTPS 时，重定向降级到 http 同样被拒绝。

    降级判定的 scheme 比较经小写归一化，与 _url_origin 和 _validate_url_static 的
    口径一致，大写起始 URL 不构成绕过降级检查的输入面。
    """
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "http://mirror.example.com/x.png"})])
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError, match="降级"):
        await manager.download_image("HTTPS://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_rejects_excessive_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过 3 跳的重定向链须被拒绝。"""
    manager = DownloadManager()
    redirects = [_FakeResponse(302, {"location": f"https://example.com/r{i}"}) for i in range(5)]
    session = _FakeSession(redirects)
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError, match="重定向次数过多"):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


@pytest.mark.asyncio
async def test_download_image_retries_5xx_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """连续 5xx 后成功：验证退避重试串联与最终原子落盘。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _FakeResponse(500, {}),
            _FakeResponse(500, {}),
            _FakeResponse(200, {"content-type": "image/png"}, content_chunks=[_PNG_BYTES]),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.exists()
    assert save_path.read_bytes() == _PNG_BYTES


@pytest.mark.asyncio
async def test_download_image_exhausts_retries_then_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """所有尝试均返回 5xx → 退避重试用尽后抛出 last_error，文件未落盘。"""
    manager = DownloadManager()
    # _FakeSession 超出序列后重复返回最后一个响应，故所有尝试均为 500
    session = _FakeSession([_FakeResponse(500, {})])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError):
        await manager.download_image("https://example.com/img.png", save_path)

    assert not save_path.exists()
    # 默认 max_retries=3，共首次 + 3 次重试 = 4 次尝试
    assert session._idx == manager.max_retries + 1


@pytest.mark.asyncio
async def test_download_image_stops_retry_when_total_budget_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """跨尝试累计时长预算耗尽即停止重试，单个保存任务占用不随尝试数成倍放大。

    恶意慢滴流响应每次尝试都可耗满单次会话总时长上限，无累计预算时 max_retries
    次重试使最长占用放大为单次封顶乘尝试数。以每次读时钟即前进超过预算的伪时钟
    驱动：首次尝试失败后累计耗时已超预算，直接停止重试，尝试次数为 1 而非
    max_retries + 1。
    """
    import seedream_mcp.utils.io.io_download as download_module

    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(500, {})])
    _patch_download_network(monkeypatch, manager, session)

    clock_now = [1000.0]

    def _advancing_time() -> float:
        value = clock_now[0]
        # 每次读时钟前进 7200 秒，超过默认预算下限 3600 秒
        clock_now[0] += 7200.0
        return value

    monkeypatch.setattr(download_module.time, "time", _advancing_time)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError):
        await manager.download_image("https://example.com/img.png", save_path)

    assert not save_path.exists()
    assert session._idx == 1, "预算耗尽后应停止重试，仅执行首次尝试"


class _SlowHopClockSession:
    """每跳推进伪时钟一个完整预算窗口的重定向链会话，模拟慢滴流链。

    伪时钟读数恒定，仅 get 调用推进时间，建模会话 total 超时按单次请求计窗、
    每跳各享全额窗口的最坏情形。
    """

    def __init__(self, responses: list, clock_now: list[float], hop_seconds: float) -> None:
        self._responses = list(responses)
        self._idx = 0
        self._clock_now = clock_now
        self._hop_seconds = hop_seconds

    def get(self, url: str, **kwargs: object) -> object:  # type: ignore[no-untyped-def]
        del url
        assert kwargs.get("allow_redirects") is False, "allow_redirects 必须为 False"
        self._clock_now[0] += self._hop_seconds
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


@pytest.mark.asyncio
async def test_download_image_redirect_chain_stops_when_cumulative_budget_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """重定向链按起始时间累计校验总预算，单次尝试不随跳数放大到多倍封顶。

    每跳耗满一个预算窗口的慢链与读数恒定的伪时钟驱动：首跳后累计已满预算，
    跟随下一跳前即按超时分类截停，经退避外层的跨尝试预算检查停止重试。总占用
    为一个预算窗口，而非 1+3 跳各享全额窗口的约 4 倍。
    """
    import seedream_mcp.utils.io.io_download as download_module

    manager = DownloadManager()
    redirects = [_FakeResponse(302, {"location": f"https://example.com/r{i}"}) for i in range(4)]
    budget = 10.0
    clock_now = [1000.0]
    session = _SlowHopClockSession(redirects, clock_now, hop_seconds=budget)
    _patch_download_network(monkeypatch, manager, session)
    monkeypatch.setattr(manager, "_download_total_budget", lambda: budget)
    monkeypatch.setattr(download_module.time, "time", lambda: clock_now[0])

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="下载超时.*重定向链累计耗时"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 首跳后累计耗满预算即截停，仅发出一次请求；总占用恰为一个预算窗口
    assert session._idx == 1, "累计预算已耗尽时不得跟随下一跳"
    assert clock_now[0] - 1000.0 == budget
    assert not save_path.exists()


@pytest.mark.asyncio
async def test_download_image_retries_timeout_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """首次下载超时经退避重试后成功：覆盖 asyncio.TimeoutError except 臂的重试路径。"""
    manager = DownloadManager()
    success = _FakeResponse(200, {"content-type": "image/png"}, content_chunks=[_PNG_BYTES])
    session = _TimeoutThenSuccessSession(success)
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    assert result["success"] is True
    assert save_path.exists()
    assert save_path.read_bytes() == _PNG_BYTES
    assert session.call_count == 2


class _HeaderCaptureSession:
    """按序返回预设响应并捕获每次 get 的请求头，供重定向头剥离断言使用。"""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.captured_headers: list[dict[str, str]] = []

    def get(self, url: str, **kwargs: object) -> object:
        del url
        assert kwargs.get("allow_redirects") is False, "allow_redirects 必须为 False"
        headers = kwargs.get("headers") or {}
        self.captured_headers.append(dict(headers))  # type: ignore[arg-type]
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


_CUSTOM_HEADERS = {
    "User-Agent": "Seedream-MCP/test",
    "Accept": "image/*",
    "Authorization": "Bearer secret-token",
    "X-Trace-Id": "trace-1",
}


@pytest.mark.asyncio
async def test_download_image_strips_custom_headers_on_cross_origin_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重定向跳向不同源时剥离定制请求头，仅保留 User-Agent 与 Accept 等通用头。

    调用方为原始主机定制的 Authorization、跟踪头原样发给重定向目标会向第三方源
    泄露凭据与内部信息。
    """
    manager = DownloadManager()
    session = _HeaderCaptureSession(
        [
            _FakeResponse(302, {"location": "https://cdn.example.net/img.png"}),
            _FakeResponse(200, {"content-type": "image/png"}, content_chunks=[_PNG_BYTES]),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image(
        "https://example.com/img.png", save_path, headers=dict(_CUSTOM_HEADERS)
    )

    assert result["success"] is True
    assert len(session.captured_headers) == 2
    # 起始请求发往原始主机，定制头原样保留
    assert session.captured_headers[0] == _CUSTOM_HEADERS
    # 跨源跳转剥离定制头，仅保留通用头
    second_keys = {key.lower() for key in session.captured_headers[1]}
    assert "authorization" not in second_keys
    assert "x-trace-id" not in second_keys
    assert "user-agent" in second_keys
    assert "accept" in second_keys


@pytest.mark.asyncio
async def test_download_image_keeps_headers_on_same_origin_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同源重定向不剥离请求头，调用方定制头在原主机内保持原样。"""
    manager = DownloadManager()
    session = _HeaderCaptureSession(
        [
            _FakeResponse(302, {"location": "https://example.com/img_final.png"}),
            _FakeResponse(200, {"content-type": "image/png"}, content_chunks=[_PNG_BYTES]),
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image(
        "https://example.com/img.png", save_path, headers=dict(_CUSTOM_HEADERS)
    )

    assert result["success"] is True
    assert len(session.captured_headers) == 2
    assert session.captured_headers[0] == _CUSTOM_HEADERS
    assert session.captured_headers[1] == _CUSTOM_HEADERS


def test_url_origin_treats_default_port_as_same_origin() -> None:
    """显式默认端口与省略端口判定同源；scheme 与 host 差异判定跨源。"""
    from seedream_mcp.utils.io.io_download import _url_origin

    assert _url_origin("https://a.example/x") == _url_origin("https://a.example:443/y")
    assert _url_origin("http://a.example/x") == _url_origin("http://a.example:80/y")
    assert _url_origin("https://a.example/x") != _url_origin("https://b.example/x")
    assert _url_origin("https://a.example/x") != _url_origin("http://a.example/x")
    assert _url_origin("https://a.example/x") != _url_origin("https://a.example:8443/x")
