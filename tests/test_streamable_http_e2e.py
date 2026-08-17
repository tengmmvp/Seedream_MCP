"""streamable-http 端到端测试：经 httpx ASGITransport 驱动真实 ASGI 栈。

覆盖 _run_streamable_http 装配的中间件栈与 Starlette lifespan 的集成：
Bearer 鉴权放行/拒绝、请求体上限超限早拒、合法请求经全栈返回 200。另含
tools/call 协议集成：平铺参数经真实 JSON-RPC 调用路径反序列化，成功与失败
结果均以 HTTP 200 的 CallToolResult 返回，isError 透传，structuredContent
经 MCPServer 内部以 outputSchema 校验。以及 SDK 内层 DNS rebinding 防护按绑定
地址重配的集成：非回环绑定下非白名单 Host 的 /mcp 请求放行，回环绑定下外部
域名 Host 被最外层自定义回环 Host 守卫以 403 先行短路。

httpx.ASGITransport 仅发送 http scope 不驱动 ASGI lifespan，故对触达 MCP 应用的
200 用例以自建 _LifespanManager 显式运行 session_manager 生命周期（建立任务组）；
401/413 由中间件在应用前短路，不依赖 lifespan。
"""

import asyncio
import json
from typing import Any, MutableMapping

import httpx
import pytest

import seedream_mcp.resources as resources
import seedream_mcp.server as server
from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.transport import _attach_streamable_http_middleware, _transport_security_for_host
from seedream_mcp.utils.core.errors import SeedreamValidationError

# MCPServer streamable-http 默认 MCP 端点路径
_MCP_PATH = "/mcp"
# 生产请求体上限默认值，与 SeedreamConfig.http_max_body_size 默认一致
_MAX_BODY = 64 * 1024 * 1024


class _LifespanManager:
    """最小 ASGI lifespan 驱动器。

    项目未依赖 asgi_lifespan，此处实现等价的 startup/shutdown 协议：发送
    lifespan.startup 等待 startup.complete，退出时发送 shutdown 等待
    shutdown.complete。Starlette 据此运行 session_manager.run() 建立请求处理任务组，
    否则 _handle_stateless_request 会因 _task_group 为 None 抛 RuntimeError。
    """

    def __init__(self, app: Any) -> None:
        self._app = app

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
            elif msg_type == "lifespan.shutdown.complete":
                self._shutdown_complete.set()

        self._task = asyncio.ensure_future(self._app({"type": "lifespan"}, receive, send))
        await self._queue.put({"type": "lifespan.startup"})
        await self._startup_complete.wait()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._queue.put({"type": "lifespan.shutdown"})
        await self._shutdown_complete.wait()
        await self._task


def _build_app(
    auth_token: str,
    *,
    body_limit: int = _MAX_BODY,
    stateless: bool = False,
    json_response: bool = False,
    host: str = "127.0.0.1",
) -> Any:
    """按生产 _run_streamable_http 的装配路径构建传输栈。

    中间件经 transport._attach_streamable_http_middleware 复用生产装配函数：
    请求体上限取注入活动配置的 http_max_body_size，Bearer 鉴权按 auth_token
    装配，回环绑定额外叠加最外层 Host 守卫，装配顺序与生产完全同源。传输参数
    stateless/transport_security/max_request_body_size 与生产一致直传
    streamable_http_app 构造，transport_security 按绑定地址派生。请求体上限经
    活动配置注入且配置合法下限为 1MB，超限用例以 1MB 上限配 1MB+1 请求体触发。
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
    """每测试重置 session manager 单例并注入测试配置。

    streamable_http_app 首次调用后缓存 _session_manager，而其 run() 仅可调用一次，
    故每测试前置 None 强制重建；同时注入活动配置供 app_lifespan 构造共享 client，
    _build_app 按用例再覆盖为携带指定请求体上限的配置。退出时关闭可能由 stateless
    请求触发的 lifespan 共享单例，避免连接池跨测试泄漏。
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
    # 响应体须为合法 JSON-RPC 2.0，且 tools/list 结果非空
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

    上限经注入活动配置的 http_max_body_size 控制并取配置合法下限 1MB，以
    1MB+1 字节的真实超限请求走全栈，确认中间件在 ASGI 栈内短路；上限单值
    与配置解析由 test_request_body_limit 覆盖。
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

    SeedreamClient.text_to_image 以类级 fake 替换并捕获入参，锁定「wire 平铺键名 →
    工具签名 → 输入模型 → 流水线 → 客户端」的参数透传链路。auto_save 显式关闭，
    避免 fake 返回的占位 URL 触发真实下载。structuredContent 由 MCPServer 内部以
    outputSchema 完成校验，校验失败会转为错误结果使本用例失败。
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
                    "seedream_text_to_image",
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
    assert structured["tool"] == "seedream_text_to_image"
    assert structured["success"] is True
    assert structured["data"][0]["url"] == "https://example.com/out.png"
    # 平铺参数经完整调用链透传到客户端入参
    assert captured["prompt"] == "一只戴墨镜的猫坐在月球上"
    assert captured["size"] == "2K"
    assert captured["watermark"] is False


async def test_e2e_tools_call_error_result_is_error_passthrough(
    monkeypatch: pytest.MonkeyPatch, reset_http_app_state: None
) -> None:
    """下游校验失败经处理器封装为 isError 结果透传，仍以 HTTP 200 的 JSON-RPC 返回。

    工具级失败不上升为 JSON-RPC error：客户端据 isError 分支处理，错误详情在
    structuredContent.error 与文本 content 中。
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
                    "seedream_text_to_image", {"prompt": "一只猫", "auto_save": False}
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
    """非回环绑定按实际地址重配 SDK 内层 Host 校验，非白名单 Host 的 /mcp 不再 421。

    streamable_http_app 的 host 参数决定 SDK 内层 Host 校验默认；未按实际绑定地址派生时，
    非回环部署的全部 /mcp 请求都会被 SDK 内层以 421 拒绝。本用例经 host 参数按实际绑定地址派生 transport_security，以部署域名 Host 请求断言放行。
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

    生产回环栈最外层为 _LoopbackHostGuardMiddleware，rebinding 域名请求先于健康
    检查、鉴权与 SDK 内层被 403 拒绝，不会走到 SDK 内层的 421。分层断言同时锁定
    SDK 内层白名单仍按回环绑定配置：防护按绑定地址重配不得把回环防线一并关闭，
    自定义守卫失效时 SDK 内层仍兜底。
    """
    app = _build_app("s3cret", stateless=True, json_response=True, host="127.0.0.1")

    response = await _post_mcp_with_host(app, "mcp.example.com")

    assert response.status_code == 403
    assert response.json()["error"] == "invalid_host"
    security = _transport_security_for_host("127.0.0.1")
    assert security.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in security.allowed_hosts
