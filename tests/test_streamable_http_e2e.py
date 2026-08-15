"""streamable-http 端到端测试：经 httpx ASGITransport 驱动真实 ASGI 栈。

覆盖 _run_streamable_http 装配的两中间件与 Starlette lifespan 的集成：
Bearer 鉴权放行/拒绝、请求体上限超限早拒、合法请求经全栈返回 200。另含
tools/call 协议集成：平铺参数经真实 JSON-RPC 调用路径反序列化，成功与失败
结果均以 HTTP 200 的 CallToolResult 返回，isError 透传，structuredContent
经 FastMCP 内部以 outputSchema 校验。

httpx.ASGITransport 仅发送 http scope 不驱动 ASGI lifespan，故对触达 MCP 应用的
200 用例以自建 _LifespanManager 显式运行 session_manager 生命周期（建立任务组）；
401/413 由中间件在应用前短路，不依赖 lifespan。
"""

import asyncio
import json
from importlib import import_module
from typing import Any, MutableMapping

import httpx
import pytest

import seedream_mcp.resources as resources
import seedream_mcp.server as server
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamValidationError

# FastMCP streamable-http 默认 MCP 端点路径
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


def _build_app(auth_token: str, *, body_limit: int = _MAX_BODY) -> Any:
    """复刻 _run_streamable_http 的中间件装配顺序。

    Starlette add_middleware 经 insert(0) 使后添加者为更外层：健康检查最外，其后请求体
    上限，再后 Bearer 鉴权，应用在内。请求体上限位于鉴权之外，故声明超长 Content-Length
    的请求在鉴权前即被 413 早拒；已认证 chunked 请求由 receive 字节累计保护，未授权
    chunked 请求不读 body 直接 401，其体积限制依赖 uvicorn 或反向代理层。
    """
    app = server.mcp.streamable_http_app()
    if auth_token:
        app.add_middleware(server._BearerTokenAuthMiddleware, expected_token=auth_token)
    app.add_middleware(server._LimitRequestBodyMiddleware, max_body_size=body_limit)
    app.add_middleware(server._HealthCheckMiddleware)
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
    故每测试前置 None 强制重建；同时注入活动配置供 app_lifespan 构造共享 client。
    退出时关闭可能由 stateless 请求触发的 lifespan 共享单例，避免连接池跨测试泄漏。
    """
    monkeypatch.setattr(server.mcp, "_session_manager", None)
    server.set_active_config(SeedreamConfig(api_key="test_key"))
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
    monkeypatch.setattr(server.mcp.settings, "stateless_http", True)
    monkeypatch.setattr(server.mcp.settings, "json_response", True)
    app = _build_app("s3cret")

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

    生产阈值默认 64MB（config.http_max_body_size），单值由 test_request_body_limit 覆盖；
    此处装配小阈值以真实发送超限字节体验证全栈集成，确认中间件在 ASGI 栈内短路。
    """
    app = _build_app("s3cret", body_limit=64)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.post(
            _MCP_PATH,
            content=b"x" * 128,
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
    避免 fake 返回的占位 URL 触发真实下载。structuredContent 由 FastMCP 内部以
    outputSchema 完成校验，校验失败会转为错误结果使本用例失败。
    """
    # 运行时解析 client 模块取类对象：test_package_lazy_import 会弹出并重建
    # seedream_mcp.client 模块，收集期绑定的类对象会与 lifespan 实例所属类分裂。
    client_cls = getattr(import_module("seedream_mcp.client"), "SeedreamClient")
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

    monkeypatch.setattr(client_cls, "text_to_image", fake_text_to_image)
    monkeypatch.setattr(server.mcp.settings, "stateless_http", True)
    monkeypatch.setattr(server.mcp.settings, "json_response", True)
    app = _build_app("s3cret")

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
    # 同上：运行时解析类对象，避免收集期绑定在模块重建后失配
    client_cls = getattr(import_module("seedream_mcp.client"), "SeedreamClient")
    captured: dict[str, Any] = {}

    async def failing_text_to_image(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        captured["called"] = True
        raise SeedreamValidationError("提示词不能为空", field="prompt", value="")

    monkeypatch.setattr(client_cls, "text_to_image", failing_text_to_image)
    monkeypatch.setattr(server.mcp.settings, "stateless_http", True)
    monkeypatch.setattr(server.mcp.settings, "json_response", True)
    app = _build_app("s3cret")

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
