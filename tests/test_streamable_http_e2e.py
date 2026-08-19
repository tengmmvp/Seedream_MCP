"""streamable-http 端到端测试：经 httpx ASGITransport 驱动真实 ASGI 栈。

覆盖 Bearer 鉴权、请求体上限、健康检查等中间件集成，tools/call 平铺参数反序列化
与 CallToolResult 返回，SDK 内层 DNS rebinding 防护按绑定地址重配，以及真端口
uvicorn 生产启动器冒烟。

httpx.ASGITransport 不驱动 ASGI lifespan，触达 MCP 应用的用例以自建
_LifespanManager 显式运行 session_manager 生命周期；401/413 由中间件在应用前
短路，不依赖 lifespan。
"""

import asyncio
import json
import socket
import threading
import time
from typing import Any, MutableMapping

import httpx
import pytest

import seedream_mcp.resources as resources
import seedream_mcp.server as server
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.transport import _attach_streamable_http_middleware, _transport_security_for_host
from seedream_mcp.utils.core.errors import SeedreamValidationError

# MCPServer streamable-http 默认 MCP 端点路径。
_MCP_PATH = "/mcp"
# 生产请求体上限默认值，与 SeedreamConfig.http_max_body_size 默认一致。
_MAX_BODY = 64 * 1024 * 1024


class _LifespanManager:
    """最小 ASGI lifespan 驱动器，等价替代未引入的 asgi_lifespan。

    发送 lifespan.startup/shutdown 并等待 complete，使 Starlette 运行
    session_manager 建立请求处理任务组；failed 消息转译为 RuntimeError，用例立即
    失败而非在等待 complete 事件上无限挂起。
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._error: BaseException | None = None

    def _fail(self, message: MutableMapping[str, Any]) -> None:
        detail = message.get("message", "")
        self._error = RuntimeError(f"ASGI lifespan 失败: {detail}")
        self._startup_complete.set()
        self._shutdown_complete.set()

    async def __aenter__(self) -> "_LifespanManager":
        self._startup_complete = asyncio.Event()
        self._shutdown_complete = asyncio.Event()
        self._queue: "asyncio.Queue[MutableMapping[str, Any]]" = asyncio.Queue()

        async def receive() -> MutableMapping[str, Any]:
            return await self._queue.get()

        async def send(message: MutableMapping[str, Any]) -> None:
            msg_type = message["type"]
            if msg_type == "lifespan.startup.complete":
                self._startup_complete.set()
            elif msg_type == "lifespan.startup.failed":
                self._fail(message)
            elif msg_type == "lifespan.shutdown.complete":
                self._shutdown_complete.set()
            elif msg_type == "lifespan.shutdown.failed":
                self._fail(message)

        self._task = asyncio.ensure_future(self._app({"type": "lifespan"}, receive, send))
        await self._queue.put({"type": "lifespan.startup"})
        await self._startup_complete.wait()
        if self._error is not None:
            await self._task
            raise self._error
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._queue.put({"type": "lifespan.shutdown"})
        await self._shutdown_complete.wait()
        await self._task
        if self._error is not None:
            raise self._error


def _build_app(
    auth_token: str,
    *,
    body_limit: int = _MAX_BODY,
    stateless: bool = False,
    json_response: bool = False,
    host: str = "127.0.0.1",
) -> Any:
    """按生产 _run_streamable_http 的装配路径构建传输栈。

    中间件经 transport._attach_streamable_http_middleware 复用生产装配且顺序同源，
    transport_security 按绑定地址派生后与其余传输参数直传 streamable_http_app；
    请求体上限经活动配置注入，配置合法下限 1MB，超限用例以 1MB 配 1MB+1 触发。
    """
    set_active_config(SeedreamConfig(api_key="test_key", http_max_body_size=body_limit))
    app = server.mcp.streamable_http_app(
        host=host,
        stateless_http=stateless,
        json_response=json_response,
        transport_security=_transport_security_for_host(host),
        max_request_body_size=body_limit,
    )
    _attach_streamable_http_middleware(app, host, auth_token)
    return app


def _mcp_request(method: str, request_id: int = 1) -> bytes:
    """构造 MCP JSON-RPC 2.0 请求体。"""
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method}).encode("utf-8")


def _tools_call_request(name: str, arguments: dict[str, Any]) -> bytes:
    """构造 MCP tools/call JSON-RPC 2.0 请求体，arguments 为工具级平铺参数。"""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    ).encode("utf-8")


@pytest.fixture
async def reset_http_app_state(monkeypatch: pytest.MonkeyPatch):
    """每测试清除遗留的 session manager 引用并注入测试配置。

    streamable_http_app 每次调用无条件新建并覆盖 _session_manager，前置置 None 属
    跨测试隔离；退出时关闭可能由 stateless 请求触发的 lifespan 共享单例，避免
    连接池跨测试泄漏。
    """
    server.mcp._lowlevel_server._session_manager = None
    set_active_config(SeedreamConfig(api_key="test_key"))
    yield
    active = resources._active_resource
    if active is not None:
        await active.client.close()
        await active.download_manager.close()
    server._reset_lifespan_state()


async def test_e2e_valid_token_tools_list_returns_200(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """合法 Bearer + 正常请求体经两中间件放行，触达 MCP 应用返回 200。

    stateless 模式下 ServerSession 以 Initialized 态启动，tools/list 无需先发 initialize。
    json_response=True 使响应为确定性 JSON 200，避免 SSE 流在 ASGITransport 下的不确定性。
    """
    app = _build_app("s3cret", stateless=True, json_response=True)

    async with _LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.post(
                _MCP_PATH,
                content=_mcp_request("tools/list"),
                headers={
                    "authorization": "Bearer s3cret",
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
            )

    assert response.status_code == 200
    # 响应体须为合法 JSON-RPC 2.0，且 tools/list 结果非空。
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert "error" not in body
    tools = body["result"]["tools"]
    assert isinstance(tools, list)
    assert len(tools) > 0


async def test_e2e_missing_bearer_token_returns_401(reset_http_app_state: None) -> None:
    """无 Authorization 头由 Bearer 中间件最外层短路返回 401，不触达应用。"""
    app = _build_app("s3cret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.post(
            _MCP_PATH,
            content=_mcp_request("tools/list"),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


async def test_e2e_wrong_bearer_token_returns_401(reset_http_app_state: None) -> None:
    """错误 Bearer 令牌经 hmac.compare_digest 判定不匹配，返回 401。"""
    app = _build_app("s3cret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.post(
            _MCP_PATH,
            content=_mcp_request("tools/list"),
            headers={
                "authorization": "Bearer wrong-token",
                "content-type": "application/json",
            },
        )

    assert response.status_code == 401


async def test_e2e_oversized_body_returns_413(reset_http_app_state: None) -> None:
    """请求体超 Content-Length 上限由请求体中间件在鉴权前返回 413。

    上限取配置合法下限 1MB 并以 1MB+1 请求体走全栈；单值与配置解析由
    test_request_body_limit 覆盖。
    """
    app = _build_app("s3cret", body_limit=1024 * 1024)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.post(
            _MCP_PATH,
            content=b"x" * (1024 * 1024 + 1),
            headers={
                "authorization": "Bearer s3cret",
                "content-type": "application/json",
            },
        )

    assert response.status_code == 413
    assert response.json()["error"] == "request_too_large"


async def test_e2e_health_check_returns_200_without_token(reset_http_app_state: None) -> None:
    """GET /health 由最外层健康检查中间件短路返回 200，无需 Bearer 令牌。"""
    app = _build_app("s3cret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_e2e_tools_call_flat_params_success(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """平铺参数经真实 tools/call 路径反序列化成功，返回 200 与结构化输出。

    text_to_image 以类级 fake 替换并捕获入参，锁定平铺键名到客户端入参的透传链路；
    auto_save 显式关闭以避免占位 URL 触发真实下载，structuredContent 经 outputSchema
    校验。
    """
    captured: dict[str, Any] = {}

    async def fake_text_to_image(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self
        captured.update(kwargs)
        return {
            "success": True,
            "data": [{"url": "https://example.com/out.png"}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }

    monkeypatch.setattr(SeedreamClient, "text_to_image", fake_text_to_image)
    app = _build_app("s3cret", stateless=True, json_response=True)

    async with _LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.post(
                _MCP_PATH,
                content=_tools_call_request(
                    "text_to_image",
                    {
                        "prompt": "一只戴墨镜的猫坐在月球上",
                        "size": "2K",
                        "watermark": False,
                        "response_format": "url",
                        "auto_save": False,
                    },
                ),
                headers={
                    "authorization": "Bearer s3cret",
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert "error" not in body
    result = body["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["tool"] == "text_to_image"
    assert structured["success"] is True
    assert structured["data"][0]["url"] == "https://example.com/out.png"
    # 平铺参数经完整调用链透传到客户端入参。
    assert captured["prompt"] == "一只戴墨镜的猫坐在月球上"
    assert captured["size"] == "2K"
    assert captured["watermark"] is False


async def test_e2e_tools_call_error_result_is_error_passthrough(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """下游校验失败经处理器封装为 isError 结果透传，仍以 HTTP 200 的 JSON-RPC 返回。

    工具级失败不上升为 JSON-RPC error，客户端据 isError 分支处理。
    """
    captured: dict[str, Any] = {}

    async def failing_text_to_image(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        captured["called"] = True
        raise SeedreamValidationError("提示词不能为空", field="prompt", value="")

    monkeypatch.setattr(SeedreamClient, "text_to_image", failing_text_to_image)
    app = _build_app("s3cret", stateless=True, json_response=True)

    async with _LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            response = await client.post(
                _MCP_PATH,
                content=_tools_call_request(
                    "text_to_image", {"prompt": "一只猫", "auto_save": False}
                ),
                headers={
                    "authorization": "Bearer s3cret",
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert "error" not in body
    assert captured.get("called") is True
    result = body["result"]
    assert result["isError"] is True
    structured = result["structuredContent"]
    assert structured["success"] is False
    assert structured["status"] == "failed"
    assert "提示词不能为空" in structured["error"]["message"]


async def _post_mcp_with_host(app: Any, host_header: str) -> httpx.Response:
    """以指定 Host 头经完整 ASGI 栈发起 tools/list 请求，返回响应。"""
    async with _LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as client:
            return await client.post(
                _MCP_PATH,
                content=_mcp_request("tools/list"),
                headers={
                    "host": host_header,
                    "authorization": "Bearer s3cret",
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                },
            )


async def test_e2e_non_loopback_bind_accepts_non_loopback_host(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """非回环绑定按实际地址重配 SDK 内层 Host 校验，非白名单 Host 不再被 421 拒绝。

    host 参数未按实际绑定地址派生时，非回环部署的全部 /mcp 请求都会被 SDK 内层
    以 421 拒绝。
    """
    app = _build_app("s3cret", stateless=True, json_response=True, host="0.0.0.0")

    response = await _post_mcp_with_host(app, "mcp.example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert "error" not in body


async def test_e2e_loopback_bind_guard_rejects_external_host_before_sdk_allowlist(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """回环绑定下外部域名 Host 被最外层自定义 Host 守卫以 403 先行短路。

    分层断言同时锁定 SDK 内层白名单仍按回环绑定配置，自定义守卫失效时内层仍兜底。
    """
    app = _build_app("s3cret", stateless=True, json_response=True, host="127.0.0.1")

    response = await _post_mcp_with_host(app, "mcp.example.com")

    assert response.status_code == 403
    assert response.json()["error"] == "invalid_host"
    security = _transport_security_for_host("127.0.0.1")
    assert security.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in security.allowed_hosts


async def test_e2e_localhost_bind_keeps_sdk_host_allowlist(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """localhost 绑定保留 SDK 内层 Host 白名单，外部域名 Host 被内层以 421 拒绝。

    localhost 若按派生逻辑关闭内层防护，hosts 污染使监听实际暴露公网后请求将失去
    全部 Host 头防线。第二个 app 经 streamable_http_app 无条件新建会话管理器，两次
    lifespan 进入各运行一次。
    """
    app = _build_app("s3cret", stateless=True, json_response=True, host="localhost")

    allowed = await _post_mcp_with_host(app, "localhost:8000")
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["jsonrpc"] == "2.0"
    assert "error" not in body

    app = _build_app("s3cret", stateless=True, json_response=True, host="localhost")
    rejected = await _post_mcp_with_host(app, "mcp.example.com")
    assert rejected.status_code == 421

    security = _transport_security_for_host("localhost")
    assert security.enable_dns_rebinding_protection is True
    assert "localhost:*" in security.allowed_hosts


# ==================== 生产启动器真端口冒烟 ====================


def _pick_free_port() -> int:
    """占用一个 127.0.0.1 随机空闲端口并释放，返回端口号供服务器绑定。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_run_streamable_http_sse_smoke_and_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """生产启动器真端口冒烟：默认 SSE 模式完成工具列表后优雅关闭无异常无挂起。

    _run_streamable_http 在后台线程以真实 uvicorn 监听随机端口，全链走生产代码；
    断言 serverInfo.version 为项目版本号，置 should_exit 后以 30 秒上限 join 线程
    防关闭链挂死。
    """
    import uvicorn
    from mcp.client import Client

    port = _pick_free_port()
    created_servers: list[uvicorn.Server] = []
    real_server_cls = uvicorn.Server

    class _CapturingServer(real_server_cls):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created_servers.append(self)

    monkeypatch.setattr(uvicorn, "Server", _CapturingServer)

    thread_errors: list[BaseException] = []

    def _serve() -> None:
        try:
            server._run_streamable_http("127.0.0.1", port, "")
        except BaseException as exc:
            thread_errors.append(exc)

    thread = threading.Thread(target=_serve, name="seedream-http-smoke", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20.0
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    pytest.fail("streamable-http 冒烟服务器未在时限内开始监听")
                await asyncio.sleep(0.05)

        async with Client(
            f"http://127.0.0.1:{port}/mcp", mode="legacy", read_timeout_seconds=10.0
        ) as client:
            server_info = client.server_info
            assert server_info is not None
            assert server_info.version == resources.SERVER_VERSION
            tools = await client.list_tools()
            assert len(tools.tools) == 5

        assert created_servers, "uvicorn.Server 未按生产路径构造"
        created_servers[0].should_exit = True
    finally:
        if created_servers:
            created_servers[0].should_exit = True
        thread.join(timeout=30.0)
        server.mcp._lowlevel_server._session_manager = None

    assert not thread.is_alive(), "_run_streamable_http 优雅关闭链挂起"
    assert thread_errors == []
