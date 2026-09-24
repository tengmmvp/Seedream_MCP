"""streamable-http 端到端测试：经 httpx ASGITransport 驱动真实 ASGI 栈。

覆盖 Bearer 鉴权、请求体上限、健康检查等中间件集成，tools/call 平铺参数反序列化
与 CallToolResult 返回，SDK 内层 DNS rebinding 防护按绑定地址重配，以及真端口
uvicorn 生产启动器冒烟。

httpx.ASGITransport 不驱动 ASGI lifespan，触达 MCP 应用的用例以共享
_asgi_fakes._LifespanManager 显式运行 session_manager 生命周期；401/413 由中间件
在应用前短路，不依赖 lifespan。
"""

import asyncio
import json
import socket
import threading
import time
from typing import Any

import httpx
import pytest

import seedream_mcp.resources as resources
import seedream_mcp.transport as transport_module
from seedream_mcp.client import SeedreamClient
from seedream_mcp.transport import _transport_security_for_host
from seedream_mcp.utils.core.errors import SeedreamValidationError

from _asgi_fakes import _LifespanManager, build_transport_app

# MCPServer streamable-http 默认 MCP 端点路径。
_MCP_PATH = "/mcp"


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


# 传输栈复位 fixture reset_http_app_state 由 tests/conftest.py 共享提供，
# 在 lifespan 复位之上额外清空 streamable-http 会话管理器引用


async def test_e2e_valid_token_tools_list_returns_200(reset_http_app_state: None) -> None:
    """合法 Bearer + 正常请求体经两中间件放行，触达 MCP 应用返回 200。

    stateless 模式下 ServerSession 以 Initialized 态启动，tools/list 无需先发 initialize。
    json_response=True 使响应为确定性 JSON 200，避免 SSE 流在 ASGITransport 下的不确定性。
    """
    app = build_transport_app("s3cret", stateless=True, json_response=True)

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
    """无 Authorization 头由 Bearer 中间件最外层短路返回 401 裸质询，不触达应用。"""
    app = build_transport_app("s3cret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.post(
            _MCP_PATH,
            content=_mcp_request("tools/list"),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"] == "missing_token"
    assert response.json()["error_description"] == "Authentication required"


async def test_e2e_wrong_bearer_token_returns_401(reset_http_app_state: None) -> None:
    """错误 Bearer 令牌经 hmac.compare_digest 判定不匹配，返回 401 并附 invalid_token 质询。"""
    app = build_transport_app("s3cret")
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
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    assert response.json()["error"] == "invalid_token"
    assert response.json()["error_description"] == "Invalid or expired token"


async def test_e2e_oversized_body_returns_413(reset_http_app_state: None) -> None:
    """请求体超 Content-Length 上限由请求体中间件在鉴权前返回 413。

    上限取配置合法下限 1MB 并以 1MB+1 请求体走全栈；单值与配置解析由
    test_request_body_limit 覆盖。
    """
    app = build_transport_app("s3cret", body_limit=1024 * 1024)
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
    assert response.json()["error_description"] == "Request body exceeds limit"


async def test_e2e_health_check_returns_200_without_token(reset_http_app_state: None) -> None:
    """GET /health 由最外层健康检查中间件短路返回 200，无需 Bearer 令牌。"""
    app = build_transport_app("s3cret")
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
    app = build_transport_app("s3cret", stateless=True, json_response=True)

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
    app = build_transport_app("s3cret", stateless=True, json_response=True)

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
    reset_http_app_state: None,
) -> None:
    """非回环绑定按实际地址重配 SDK 内层 Host 校验，非白名单 Host 不再被 421 拒绝。

    host 参数未按实际绑定地址派生时，非回环部署的全部 /mcp 请求都会被 SDK 内层
    以 421 拒绝。
    """
    app = build_transport_app("s3cret", stateless=True, json_response=True, host="0.0.0.0")

    response = await _post_mcp_with_host(app, "mcp.example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert "error" not in body


async def test_e2e_loopback_bind_guard_rejects_external_host_before_sdk_allowlist(
    reset_http_app_state: None,
) -> None:
    """回环绑定下外部域名 Host 被最外层自定义 Host 守卫以 403 先行短路。

    分层断言同时锁定 SDK 内层白名单仍按回环绑定配置，自定义守卫失效时内层仍兜底。
    """
    app = build_transport_app("s3cret", stateless=True, json_response=True, host="127.0.0.1")

    response = await _post_mcp_with_host(app, "mcp.example.com")

    assert response.status_code == 403
    assert response.json()["error"] == "invalid_host"
    assert response.json()["error_description"] == "Host not allowed"
    security = _transport_security_for_host("127.0.0.1")
    assert security.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in security.allowed_hosts


async def test_e2e_localhost_bind_keeps_sdk_host_allowlist(
    reset_http_app_state: None,
) -> None:
    """localhost 绑定保留 SDK 内层 Host 白名单，外部域名 Host 被内层以 421 拒绝。

    localhost 若按派生逻辑关闭内层防护，hosts 污染使监听实际暴露公网后请求将失去
    全部 Host 头防线。第二个 app 经 streamable_http_app 无条件新建会话管理器，两次
    lifespan 进入各运行一次。
    """
    app = build_transport_app("s3cret", stateless=True, json_response=True, host="localhost")

    allowed = await _post_mcp_with_host(app, "localhost:8000")
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["jsonrpc"] == "2.0"
    assert "error" not in body

    app = build_transport_app("s3cret", stateless=True, json_response=True, host="localhost")
    rejected = await _post_mcp_with_host(app, "mcp.example.com")
    assert rejected.status_code == 421

    security = _transport_security_for_host("localhost")
    assert security.enable_dns_rebinding_protection is True
    assert "localhost:*" in security.allowed_hosts


# ==================== 生产启动器真端口冒烟 ====================


def _pick_free_port() -> int:
    """占用一个 127.0.0.1 随机空闲端口并释放，返回端口号供服务器绑定。

    bind 失败时换下一个端口重试一次，仍失败则上抛。
    """
    for _ in range(2):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                return int(sock.getsockname()[1])
        except OSError:
            continue
    raise OSError("两次尝试均未能绑定 127.0.0.1 空闲端口")


async def _start_smoke_server_and_wait(
    port: int, thread_errors: list[BaseException]
) -> tuple[threading.Thread, bool]:
    """后台线程运行生产启动器并等待端口可连接，返回线程与是否就绪。

    未就绪时线程句柄仍随返回值交回，供调用方收尾后换端口重试。
    """

    def _serve() -> None:
        try:
            transport_module.run_streamable_http("127.0.0.1", port, "")
        except BaseException as exc:
            thread_errors.append(exc)

    thread = threading.Thread(target=_serve, name="seedream-http-smoke", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20.0
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return thread, True
        except OSError:
            if time.monotonic() > deadline:
                return thread, False
            await asyncio.sleep(0.05)


@pytest.mark.slow
async def test_run_streamable_http_sse_smoke_and_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """生产启动器真端口冒烟：默认 SSE 模式完成工具列表后优雅关闭无异常无挂起。

    run_streamable_http 在后台线程以真实 uvicorn 监听随机端口，全链走生产代码；
    端口被抢等监听未就绪时收尾线程并换端口重试一次，两次失败才判失败。断言
    serverInfo.version 为项目版本号，置 should_exit 后以 30 秒上限 join 线程
    防关闭链挂死。
    """
    import uvicorn
    from mcp.client import Client

    created_servers: list[uvicorn.Server] = []

    class _CapturingServer(uvicorn.Server):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created_servers.append(self)

    monkeypatch.setattr(uvicorn, "Server", _CapturingServer)

    thread_errors: list[BaseException] = []
    thread: threading.Thread | None = None
    listening = False
    for attempt in range(2):
        port = _pick_free_port()
        thread, listening = await _start_smoke_server_and_wait(port, thread_errors)
        if listening:
            break
        # 监听未就绪：收尾本线程后丢弃失败痕迹，换端口重试一次
        for created in created_servers:
            created.should_exit = True
        thread.join(timeout=30.0)
        thread_errors.clear()
    if not listening:
        pytest.fail("streamable-http 冒烟服务器未在时限内开始监听")

    assert thread is not None

    try:
        async with Client(
            f"http://127.0.0.1:{port}/mcp", mode="legacy", read_timeout_seconds=10.0
        ) as client:
            server_info = client.server_info
            assert server_info is not None
            assert server_info.version == resources.SERVER_VERSION
            tools = await client.list_tools()
            assert len(tools.tools) == 5

        assert created_servers, "uvicorn.Server 未按生产路径构造"
        created_servers[-1].should_exit = True
    finally:
        for created in created_servers:
            created.should_exit = True
        thread.join(timeout=30.0)

    assert not thread.is_alive(), "run_streamable_http 优雅关闭链挂起"
    assert thread_errors == []
