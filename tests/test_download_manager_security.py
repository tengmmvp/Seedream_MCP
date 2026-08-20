"""下载管理器测试：DNS 解析 TTL 缓存与在途去重、连接后对端 IP 公网校验、
重定向逐跳校验、重试退避与累计预算、DNS 缓存驱逐、扩展名等价类。

用 fake loop/session 模拟，不依赖真实网络。
"""

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from seedream_mcp.utils.io.io_download import DownloadError, DownloadManager, _DNS_CACHE_MAX_SIZE

from _download_fakes import (
    _FakeLoop,
    _FakeResponse,
    _FakeSession,
    _GatedFailingLoop,
    _PNG_BYTES,
    _TimeoutThenSuccessSession,
    _patch_download_network,
)

# 合法 JPEG 魔法字节，供扩展名等价类用例的字节签名嗅探。
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 24


def _patch_unretrieved_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> "list[asyncio.Task[Any]]":
    """把 logs.log_unretrieved_task_exception 替换为记录 task 并检索异常的替身。

    替身经模块属性遮蔽即生效，检索异常避免 "Task exception was never retrieved"
    告警。返回已触发回调的 task 列表，供断言登记时序。
    """
    from seedream_mcp.utils.core import logs

    fired: "list[asyncio.Task[Any]]" = []

    def record(task: "asyncio.Task[Any]") -> None:
        fired.append(task)
        if not task.cancelled():
            task.exception()

    monkeypatch.setattr(logs, "log_unretrieved_task_exception", record)
    return fired


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


async def test_resolve_public_ips_uses_ttl_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL 内重复解析同 host 命中缓存，仅触发一次 getaddrinfo。"""
    fake_loop = _FakeLoop(ips=["8.8.8.8", "1.1.1.1"])
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    manager = DownloadManager(dns_cache_ttl=60)
    first = await manager._resolve_public_ips("example.com")
    second = await manager._resolve_public_ips("example.com")

    assert fake_loop.calls == 1
    assert first == second == ("1.1.1.1", "8.8.8.8")


async def test_resolve_public_ips_dedups_inflight_resolutions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同 host 并发解析在缓存冷启动时共享同一在途 task，仅触发一次 getaddrinfo。"""
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


async def test_resolve_failure_consumed_by_caller_not_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在途解析抛错且由调用方正常消费时不登记「未取回异常」回调。

    异常经 shield 交还调用方并由既有错误通道记录，无条件挂回调会使同一异常
    以「后台共享任务失败」重复入日志。
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


async def test_resolve_creator_cancel_arms_unretrieved_logging_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用方被取消且无其他等待者时，在途解析失败经登记的回调检索且仅触发一次。"""
    fired = _patch_unretrieved_callback(monkeypatch)

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

    inflight = manager._dns_inflight["example.com"].task
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


async def test_resolve_cancel_with_surviving_waiter_not_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消的等待者仍有幸存同伴时不登记兜底日志，异常由幸存者消费。"""
    fired = _patch_unretrieved_callback(monkeypatch)

    fake_loop = _GatedFailingLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    manager = DownloadManager(dns_cache_ttl=60)
    cancelled = asyncio.create_task(manager._resolve_public_ips("example.com"))
    survivor = asyncio.create_task(manager._resolve_public_ips("example.com"))
    # 让出控制权使两任务均到达在途等待点
    for _ in range(20):
        await asyncio.sleep(0)
        if "example.com" in manager._dns_inflight:
            break
    assert "example.com" in manager._dns_inflight

    cancelled.cancel()
    fake_loop.gate.set()

    with pytest.raises(DownloadError):
        await survivor
    assert cancelled.cancelled()
    # 推进事件循环跑完在途 task 完成时排队的 done callback
    await asyncio.sleep(0)

    # 异常已由幸存者消费，取消方不登记兜底日志。
    assert fired == []


def test_validate_connected_peer_ip_blocks_non_public_ip() -> None:
    """连接后复核拒绝非公网对端 IP。"""
    manager = DownloadManager()
    fake_response = _FakeResponse(peer_ip="127.0.0.1")

    with pytest.raises(DownloadError, match="非公网地址"):
        manager._validate_connected_peer_ip(  # type: ignore[arg-type]
            fake_response, "https://example.com"
        )


def test_validate_connected_peer_ip_allows_public_ip() -> None:
    """连接后复核放行公网对端 IP。"""
    manager = DownloadManager()
    fake_response = _FakeResponse(peer_ip="8.8.8.8")

    manager._validate_connected_peer_ip(  # type: ignore[arg-type]
        fake_response, "https://example.com"
    )


def test_validate_connected_peer_ip_blocks_multicast() -> None:
    """组播地址经 is_global 放行，须由显式分支拒绝，阻断向内网组播监听者的探测。"""
    manager = DownloadManager()

    for peer_ip in ("224.0.0.1", "239.1.1.1", "ff02::1", "fec0::1"):
        fake_response = _FakeResponse(peer_ip=peer_ip)
        with pytest.raises(DownloadError, match="组播地址|site-local"):
            manager._validate_connected_peer_ip(  # type: ignore[arg-type]
                fake_response, "https://example.com"
            )


# ---- download_image 端到端：逐跳重定向校验与重试退避 ----
# mock 网络层，验证重定向目标重新走静态校验、重定向上限与 5xx 退避重试。
# 重定向到内网/回环的安全拒绝由 *_via_real_static_validation 用例覆盖，保留真实
# _validate_url_for_request 串联，避免架空校验的空芯用例。


async def test_download_image_rejects_redirect_to_private_ip_via_real_static_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端串联：保留真实 _validate_url_for_request 的重定向内网拒绝。

    仅 stub 依赖网络的 DNS 解析与 session 注入，使 302 目标 169.254.169.254 经
    _validate_url_static 命中非公网判定被拒绝。
    """
    manager = DownloadManager()
    session = _FakeSession(
        [_FakeResponse(302, {"location": "http://169.254.169.254/latest/meta-data/"})]
    )

    async def _pass_dns(host: str) -> tuple[str, ...]:
        del host
        return ("93.184.216.34",)

    async def _fake_ensure_session() -> Any:
        return session

    monkeypatch.setattr(manager, "_resolve_public_ips", _pass_dns)
    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)

    with pytest.raises(DownloadError, match="不安全|非公网"):
        # 起始 URL 用 http 使降级检查不先触发，保证静态校验路径真实可达
        await manager.download_image("http://example.com/img.png", tmp_path / "out.png")


async def test_download_image_rejects_redirect_to_loopback_via_real_static_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端串联：302 跳转至回环地址须被真实 _validate_url_static 拒绝。

    起始 URL 用 http 使降级检查不先触发，保证静态校验路径真实可达。
    """
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "http://127.0.0.1/"})])

    async def _pass_dns(host: str) -> tuple[str, ...]:
        del host
        return ("93.184.216.34",)

    async def _fake_ensure_session() -> Any:
        return session

    monkeypatch.setattr(manager, "_resolve_public_ips", _pass_dns)
    monkeypatch.setattr(manager, "_ensure_session", _fake_ensure_session)

    with pytest.raises(DownloadError, match="不安全|非公网"):
        await manager.download_image("http://example.com/img.png", tmp_path / "out.png")


async def test_download_image_rejects_https_to_http_downgrade_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """https 起始的下载不允许经重定向降级到 http，消除明文链路攻击面。"""
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "http://mirror.example.com/x.png"})])
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError, match="降级"):
        await manager.download_image("https://example.com/img.png", tmp_path / "out.png")


async def test_download_image_rejects_uppercase_scheme_downgrade_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """初始 URL scheme 为大写 HTTPS 时，重定向降级到 http 同样被拒绝。

    降级判定的 scheme 比较经小写归一化，大写起始 URL 不构成绕过输入面。
    """
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "http://mirror.example.com/x.png"})])
    _patch_download_network(monkeypatch, manager, session)

    with pytest.raises(DownloadError, match="降级"):
        await manager.download_image("HTTPS://example.com/img.png", tmp_path / "out.png")


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


async def test_download_image_rejects_malformed_redirect_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Location 为不闭合 IPv6 括号等畸形目标时抛终态 DownloadError，ValueError 不逃逸。"""
    manager = DownloadManager()
    session = _FakeSession([_FakeResponse(302, {"location": "//[::1/x"})])
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="无效的URL"):
        await manager.download_image("https://example.com/img.png", save_path)

    assert not save_path.exists()


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


async def test_download_image_stops_retry_when_total_budget_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """跨尝试累计时长预算耗尽即停止重试，占用不随尝试数成倍放大。

    每次读时钟即前进超过预算的伪时钟驱动：首次尝试失败后累计已超预算，
    尝试次数为 1 而非 max_retries + 1。
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

    monkeypatch.setattr(download_module.time, "monotonic", _advancing_time)

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError):
        await manager.download_image("https://example.com/img.png", save_path)

    assert not save_path.exists()
    assert session._idx == 1, "预算耗尽后应停止重试，仅执行首次尝试"


class _SlowHopClockSession:
    """每跳推进伪时钟一个完整预算窗口的重定向链会话，模拟慢滴流链。

    伪时钟仅随 get 调用推进，建模每跳各享全额超时窗口的最坏情形。
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


async def test_download_image_redirect_chain_stops_when_cumulative_budget_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    """重定向链按起始时间累计校验总预算，单次尝试不随跳数放大到多倍封顶。

    首跳后累计已满预算，跟随下一跳前即按超时分类截停，总占用恰为一个预算窗口。
    """
    import seedream_mcp.utils.io.io_download as download_module

    manager = DownloadManager()
    redirects = [_FakeResponse(302, {"location": f"https://example.com/r{i}"}) for i in range(4)]
    budget = 10.0
    clock_now = [1000.0]
    session = _SlowHopClockSession(redirects, clock_now, hop_seconds=budget)
    _patch_download_network(monkeypatch, manager, session)
    monkeypatch.setattr(manager, "_download_total_budget", lambda: budget)
    monkeypatch.setattr(download_module.time, "monotonic", lambda: clock_now[0])

    save_path = tmp_path / "out.png"
    with pytest.raises(DownloadError, match="下载超时.*重定向链累计耗时"):
        await manager.download_image("https://example.com/img.png", save_path)

    # 首跳后累计耗满预算即截停，仅发出一次请求；总占用恰为一个预算窗口
    assert session._idx == 1, "累计预算已耗尽时不得跟随下一跳"
    assert clock_now[0] - 1000.0 == budget
    assert not save_path.exists()


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


async def test_download_image_strips_custom_headers_on_cross_origin_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重定向跳向不同源时剥离定制请求头，仅保留 User-Agent 与 Accept 等通用头。"""
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


# ---- DNS 缓存超限驱逐 ----


def test_enforce_dns_cache_limit_evicts_expired_then_oldest() -> None:
    """缓存超限时先清过期条目，仍超限再按最旧 expires_at 驱逐，条目数不超上限。"""
    manager = DownloadManager()
    # expires_at 以 time.monotonic 为基准，构造条目须与代码同基准
    now = time.monotonic()
    expired_hosts = [f"expired{i}.example.com" for i in range(3)]
    for i, host in enumerate(expired_hosts):
        manager._dns_cache[host] = (now - 100.0 - i, ("203.0.113.1",))
    # 未过期条目比上限多 5 个：过期清理后仍超限，须按 expires_at 最旧强制驱逐
    live_count = _DNS_CACHE_MAX_SIZE + 5
    live_hosts = [f"live{i}.example.com" for i in range(live_count)]
    for i, host in enumerate(live_hosts):
        manager._dns_cache[host] = (now + 1000.0 + i, ("203.0.113.2",))

    manager._enforce_dns_cache_limit()

    # 过期条目无条件先清理
    assert all(host not in manager._dns_cache for host in expired_hosts)
    # 上限为硬限制，最终条目数恰为上限
    assert len(manager._dns_cache) == _DNS_CACHE_MAX_SIZE
    # 未过期条目按 expires_at 从旧到新驱逐最旧的 5 个
    for host in live_hosts[:5]:
        assert host not in manager._dns_cache
    for host in live_hosts[5:]:
        assert host in manager._dns_cache


# ---- 扩展名等价类：同格式别名不改名，跨格式仍修正 ----


async def test_download_keeps_jpg_suffix_for_jpeg_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.jpg URL 的 JPEG 内容属同格式等价类，落盘保留 .jpg 不改名为 .jpeg。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _FakeResponse(
                status=200,
                headers={"content-type": "image/jpeg", "content-length": str(len(_JPEG_BYTES))},
                content_chunks=[_JPEG_BYTES],
            )
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.jpg"
    result = await manager.download_image("https://example.com/img.jpg", save_path)

    assert result["success"] is True
    assert result["file_path"] == str(save_path)
    assert save_path.exists()
    assert save_path.read_bytes() == _JPEG_BYTES


async def test_download_corrects_png_suffix_to_jpeg_for_jpeg_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.png URL 返回 JPEG 内容属跨格式不一致，落盘扩展名仍修正为嗅探结果 .jpeg。"""
    manager = DownloadManager()
    session = _FakeSession(
        [
            _FakeResponse(
                status=200,
                headers={"content-type": "image/jpeg", "content-length": str(len(_JPEG_BYTES))},
                content_chunks=[_JPEG_BYTES],
            )
        ]
    )
    _patch_download_network(monkeypatch, manager, session)

    save_path = tmp_path / "out.png"
    result = await manager.download_image("https://example.com/img.png", save_path)

    corrected = tmp_path / "out.jpeg"
    assert result["success"] is True
    assert result["file_path"] == str(corrected)
    assert corrected.exists()
    assert corrected.read_bytes() == _JPEG_BYTES
    assert not save_path.exists()
