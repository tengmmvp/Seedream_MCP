"""streamable-http 请求体大小限制中间件测试。

直接实例化 ``_LimitRequestBodyMiddleware`` 并以 ASGI scope/receive/send 模拟调用，
覆盖超限早拒（413）、未超限放行、非数字 Content-Length 降级 chunked 字节累计防护、
超限截断后下游异常被吞统一回 413、未超限下游异常原样重抛与非 http scope 透传，
与 Bearer 鉴权中间件测试同构。Content-Length 头仅作早拒快速路径，畸形声明降级后
由 receive 字节累计兜底。
"""

import json
from pathlib import Path

import pytest

import seedream_mcp.server as server
from seedream_mcp.config import build_config_from_sources
from seedream_mcp.utils.core.errors import SeedreamConfigError

_LIMIT = 64 * 1024 * 1024


async def test_request_body_limit_rejects_oversized_content_length() -> None:
    """Content-Length 超上限 → 413 + body 含 request_too_large。"""
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise AssertionError("超限请求不应进入下游应用")

    middleware = server._LimitRequestBodyMiddleware(downstream, _LIMIT)
    scope = {
        "type": "http",
        "headers": [(b"content-length", str(_LIMIT + 1).encode("ascii"))],
    }
    await middleware(scope, None, send)

    assert len(sent) == 2
    start, body_msg = sent[0], sent[1]
    assert start["type"] == "http.response.start"
    assert start["status"] == 413
    headers = dict(start["headers"])
    assert headers[b"content-type"] == b"application/json"
    assert int(headers[b"content-length"]) == len(body_msg["body"])
    assert body_msg["type"] == "http.response.body"
    body = json.loads(body_msg["body"].decode("utf-8"))
    assert body["error"] == "request_too_large"


async def test_request_body_limit_allows_within_limit() -> None:
    """未超限的声明长度应放行，调用下游 app。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._LimitRequestBodyMiddleware(downstream, _LIMIT)
    scope = {
        "type": "http",
        "headers": [(b"content-length", b"1048576")],  # 1MB
    }
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_request_body_limit_boundary_equal_to_limit_passes() -> None:
    """Content-Length 恰等于上限应放行：比较为严格大于。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._LimitRequestBodyMiddleware(downstream, _LIMIT)
    scope = {
        "type": "http",
        "headers": [(b"content-length", str(_LIMIT).encode("ascii"))],
    }
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_request_body_limit_missing_content_length_passes() -> None:
    """无 Content-Length 头按 0 处理，放行下游。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._LimitRequestBodyMiddleware(downstream, _LIMIT)
    scope = {"type": "http", "headers": []}
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_request_body_limit_passes_lifespan_scope() -> None:
    """lifespan 类型 ASGI 消息应直接透传，不检查请求体大小。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._LimitRequestBodyMiddleware(downstream, _LIMIT)
    scope = {"type": "lifespan", "headers": []}
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_request_body_limit_passes_websocket_scope() -> None:
    """websocket 类型 ASGI 消息应直接透传，不检查请求体大小。"""
    received: dict[str, object] = {}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True

    middleware = server._LimitRequestBodyMiddleware(downstream, _LIMIT)
    scope = {"type": "websocket", "headers": []}
    await middleware(scope, None, None)

    assert received == {"called": True}


async def test_request_body_limit_rejects_oversized_chunked_body() -> None:
    """无 Content-Length 的分块 body 累计超限 → receive_wrapper 截断并回 413。

    覆盖 chunked 防御路径：缺失 Content-Length 时按实际接收字节累计，超限短路。
    用小 limit 避免构造百兆级字节串。
    """
    small_limit = 1024
    sent: list[dict] = []
    messages = [
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
        {"type": "http.request", "body": b"x" * 600, "more_body": False},
    ]
    counter = {"i": 0}

    async def receive() -> dict:
        if counter["i"] < len(messages):
            msg = messages[counter["i"]]
            counter["i"] += 1
            return msg
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        while True:
            msg = await receive()
            if msg["type"] == "http.request" and not msg.get("more_body", False):
                break

    middleware = server._LimitRequestBodyMiddleware(downstream, small_limit)
    scope = {"type": "http", "headers": []}
    await middleware(scope, receive, send)

    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    body_msg = next(m for m in sent if m.get("type") == "http.response.body")
    assert json.loads(body_msg["body"].decode("utf-8"))["error"] == "request_too_large"


async def test_request_body_limit_allows_chunked_body_within_limit() -> None:
    """无 Content-Length 的分块 body 累计未超限 → 正常放行下游。"""
    small_limit = 1024
    received: dict[str, object] = {}
    messages = [
        {"type": "http.request", "body": b"x" * 400, "more_body": True},
        {"type": "http.request", "body": b"x" * 400, "more_body": False},
    ]
    counter = {"i": 0}

    async def receive() -> dict:
        if counter["i"] < len(messages):
            msg = messages[counter["i"]]
            counter["i"] += 1
            return msg
        return {"type": "http.request", "body": b"", "more_body": False}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True
        while True:
            msg = await receive()
            if msg["type"] == "http.request" and not msg.get("more_body", False):
                break

    middleware = server._LimitRequestBodyMiddleware(downstream, small_limit)
    scope = {"type": "http", "headers": []}
    await middleware(scope, receive, None)

    assert received == {"called": True}


async def test_request_body_limit_skips_413_when_downstream_already_responded() -> None:
    """下游先发 response.start 再触发超限时不得补发第二个 response.start。

    防御性路径：现行实现截断 body 后下游应在发出响应头前失败或返回，此处模拟
    下游已开始响应才读到超限 body 的形态，断言真实 send 只收到一个
    http.response.start，连接异常交由服务器协议层处理。
    """
    small_limit = 1024
    sent: list[dict] = []
    messages = [
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
        {"type": "http.request", "body": b"x" * 600, "more_body": False},
    ]
    counter = {"i": 0}

    async def receive() -> dict:
        if counter["i"] < len(messages):
            msg = messages[counter["i"]]
            counter["i"] += 1
            return msg
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        # 先正常开始响应，再读取 body 触发超限判定。
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            msg = await receive()
            if msg["type"] == "http.request" and not msg.get("more_body", False):
                break
        await send({"type": "http.response.body", "body": b"partial"})

    middleware = server._LimitRequestBodyMiddleware(downstream, small_limit)
    scope = {"type": "http", "headers": []}
    await middleware(scope, receive, send)

    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 200


async def test_request_body_limit_non_numeric_content_length_falls_back_to_chunked() -> None:
    """非数字 Content-Length 头降级为 0，超限防护由 chunked 字节累计承担。

    被污染上游可声明畸形 Content-Length 绕过早拒快速路径，降级后请求进入下游，
    receive 字节累计仍须拦截超限并以 413 短路。
    """
    small_limit = 1024
    sent: list[dict] = []
    received: dict[str, object] = {}
    messages = [
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
        {"type": "http.request", "body": b"x" * 600, "more_body": False},
    ]
    counter = {"i": 0}

    async def receive() -> dict:
        if counter["i"] < len(messages):
            msg = messages[counter["i"]]
            counter["i"] += 1
            return msg
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True
        while True:
            msg = await receive()
            if msg["type"] == "http.request" and not msg.get("more_body", False):
                break

    middleware = server._LimitRequestBodyMiddleware(downstream, small_limit)
    scope = {"type": "http", "headers": [(b"content-length", b"abc")]}
    await middleware(scope, receive, send)

    # 畸形头未触发早拒，请求确实进入了下游，拦截由字节累计路径完成
    assert received == {"called": True}
    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    body_msg = next(m for m in sent if m.get("type") == "http.response.body")
    assert json.loads(body_msg["body"].decode("utf-8"))["error"] == "request_too_large"


async def test_request_body_limit_non_numeric_content_length_within_limit_passes() -> None:
    """非数字 Content-Length 且实际字节未超限时放行下游，不因畸形头误拒。"""
    small_limit = 1024
    received: dict[str, object] = {}
    messages = [
        {"type": "http.request", "body": b"x" * 400, "more_body": True},
        {"type": "http.request", "body": b"x" * 400, "more_body": False},
    ]
    counter = {"i": 0}

    async def receive() -> dict:
        if counter["i"] < len(messages):
            msg = messages[counter["i"]]
            counter["i"] += 1
            return msg
        return {"type": "http.request", "body": b"", "more_body": False}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        received["called"] = True
        while True:
            msg = await receive()
            if msg["type"] == "http.request" and not msg.get("more_body", False):
                break

    middleware = server._LimitRequestBodyMiddleware(downstream, small_limit)
    scope = {"type": "http", "headers": [(b"content-length", b"abc")]}
    await middleware(scope, receive, None)

    assert received == {"called": True}


async def test_request_body_limit_swallows_downstream_exception_after_truncation() -> None:
    """超限截断后下游读到空终帧抛异常时被吞掉，统一回 413 而非冒泡 500。

    下游按截断的空终帧判定请求畸形而抛异常属预期形态；too_large 已置位时
    异常被中间件吞掉并补发 413，保证客户端收到确定的超限语义。
    """
    small_limit = 1024
    sent: list[dict] = []
    messages = [
        {"type": "http.request", "body": b"x" * 600, "more_body": True},
        {"type": "http.request", "body": b"x" * 600, "more_body": False},
    ]
    counter = {"i": 0}

    async def receive() -> dict:
        if counter["i"] < len(messages):
            msg = messages[counter["i"]]
            counter["i"] += 1
            return msg
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        # 读到被截断的空终帧后以异常失败，模拟下游对畸形请求的拒绝。
        while True:
            msg = await receive()
            if msg["type"] == "http.request" and not msg.get("more_body", False):
                break
        raise RuntimeError("downstream rejected truncated request")

    middleware = server._LimitRequestBodyMiddleware(downstream, small_limit)
    scope = {"type": "http", "headers": []}
    await middleware(scope, receive, send)

    starts = [m for m in sent if m.get("type") == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    body_msg = next(m for m in sent if m.get("type") == "http.response.body")
    assert json.loads(body_msg["body"].decode("utf-8"))["error"] == "request_too_large"


async def test_request_body_limit_reraises_downstream_exception_within_limit() -> None:
    """未超限时下游异常原样重抛，不吞掉非超限语义的失败。"""
    small_limit = 1024
    messages = [
        {"type": "http.request", "body": b"x" * 400, "more_body": True},
        {"type": "http.request", "body": b"x" * 400, "more_body": False},
    ]
    counter = {"i": 0}

    async def receive() -> dict:
        if counter["i"] < len(messages):
            msg = messages[counter["i"]]
            counter["i"] += 1
            return msg
        return {"type": "http.request", "body": b"", "more_body": False}

    async def downstream(scope, receive, send):  # type: ignore[no-untyped-def]
        raise RuntimeError("downstream boom")

    middleware = server._LimitRequestBodyMiddleware(downstream, small_limit)
    scope = {"type": "http", "headers": []}
    with pytest.raises(RuntimeError, match="downstream boom"):
        await middleware(scope, receive, None)


# ==================== SEEDREAM_HTTP_MAX_BODY_SIZE 配置解析 ====================


def test_http_max_body_size_defaults_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置环境变量时回退默认 64MB。"""
    env_file = tmp_path / "config.env"
    env_file.write_text("ARK_API_KEY=test_key\n", encoding="utf-8")
    monkeypatch.delenv("SEEDREAM_HTTP_MAX_BODY_SIZE", raising=False)
    config = build_config_from_sources(env_file=str(env_file))
    assert config.http_max_body_size == 64 * 1024 * 1024


def test_http_max_body_size_uses_env_file_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合法整数值经 .env 覆盖默认上限。"""
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "ARK_API_KEY=test_key\nSEEDREAM_HTTP_MAX_BODY_SIZE=2097152\n", encoding="utf-8"
    )
    monkeypatch.delenv("SEEDREAM_HTTP_MAX_BODY_SIZE", raising=False)
    config = build_config_from_sources(env_file=str(env_file))
    assert config.http_max_body_size == 2 * 1024 * 1024


def test_http_max_body_size_rejects_below_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """低于下限 1MB 的值视为配置错误，构建失败。"""
    env_file = tmp_path / "config.env"
    env_file.write_text("ARK_API_KEY=test_key\nSEEDREAM_HTTP_MAX_BODY_SIZE=100\n", encoding="utf-8")
    monkeypatch.delenv("SEEDREAM_HTTP_MAX_BODY_SIZE", raising=False)
    with pytest.raises(SeedreamConfigError):
        build_config_from_sources(env_file=str(env_file))


def test_http_max_body_size_rejects_non_integer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非整数环境变量视为配置错误，构建失败。"""
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "ARK_API_KEY=test_key\nSEEDREAM_HTTP_MAX_BODY_SIZE=not-a-number\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SEEDREAM_HTTP_MAX_BODY_SIZE", raising=False)
    with pytest.raises(SeedreamConfigError):
        build_config_from_sources(env_file=str(env_file))
