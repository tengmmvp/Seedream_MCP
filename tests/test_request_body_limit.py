"""streamable-http 请求体大小限制中间件测试。

直接实例化 ``_LimitRequestBodyMiddleware`` 并以 ASGI scope/receive/send 模拟调用，
覆盖超限早拒（413）、未超限放行与非 http scope 透传三条路径，与 Bearer 鉴权中间件
测试同构。中间件按 Content-Length 头判定，仅检查声明长度。
"""

import json
from pathlib import Path

import pytest

import seedream_mcp.server as server
from seedream_mcp.config import build_config_from_sources
from seedream_mcp.utils.errors import SeedreamConfigError

_LIMIT = 100 * 1024 * 1024


async def test_request_body_limit_rejects_oversized_content_length() -> None:
    """Content-Length 超 100MB 上限 → 413 + body 含 request_too_large。"""
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


# ==================== SEEDREAM_HTTP_MAX_BODY_SIZE 配置解析 ====================


def test_http_max_body_size_defaults_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置环境变量时回退默认 100MB。"""
    env_file = tmp_path / "config.env"
    env_file.write_text("ARK_API_KEY=test_key\n", encoding="utf-8")
    monkeypatch.delenv("SEEDREAM_HTTP_MAX_BODY_SIZE", raising=False)
    config = build_config_from_sources(env_file=str(env_file))
    assert config.http_max_body_size == 100 * 1024 * 1024


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
