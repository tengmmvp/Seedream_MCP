"""SDK 行为特征化与项目侧补偿守护测试。

六个用例：未知 prompt 名称经项目侧补偿归位 -32602 的线缆级与名称查找点
两级，已知名渲染期真实失败不被改写为 Unknown prompt；缺失 name 的参数面
校验错误不被补偿改写；未知工具走 isError 结果通道而非协议错误；纪元路由
按请求头派生。后两类为 SDK 2.2.0 行为特征化，规范未定义或与其相悖，SDK
升级行为漂移时测试变红，须复测线缆级语义。
"""

from __future__ import annotations

import json
from typing import Literal

import httpx
import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import (
    HEADER_MISMATCH,
    INVALID_PARAMS,
    TextContent,
)

import seedream_mcp.server as server

from _asgi_fakes import _LifespanManager, build_transport_app, modern_meta_envelope


@pytest.mark.parametrize("client_mode", ["auto", "legacy"])
async def test_unknown_prompt_get_returns_invalid_params(
    reset_lifespan_singletons: None, client_mode: Literal["auto", "legacy"]
) -> None:
    """未知 prompt 名称在两条分发路径上线缆级均为 -32602，守护项目侧补偿。

    auto 走 2026-07-28 直连分发，legacy 走 initialize 握手的 JSON-RPC 流分发，
    未补偿时分别归约为 -32603 与 code=0。SDK 变更 get_prompt 的 MCPError
    透传或分发层异常归约时本测试变红。
    """
    async with Client(server.mcp, mode=client_mode) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.get_prompt("definitely_not_a_prompt")

    assert exc_info.value.code == INVALID_PARAMS
    assert "Unknown prompt" in str(exc_info.value)


async def test_unknown_prompt_get_prompt_raises_invalid_params_at_lookup() -> None:
    """未知名的 -32602 在 get_prompt 名称查找点抛出，不经分发层异常网改写。

    直接调用绕开分发层，仅子类 override 的预检生效；分类若退回异常网形
    态，未知名在直接调用处是 SDK 裸 ValueError。
    """
    with pytest.raises(MCPError) as exc_info:
        await server.mcp.get_prompt("definitely_not_a_prompt")

    assert exc_info.value.code == INVALID_PARAMS
    assert "Unknown prompt" in str(exc_info.value)


async def test_registered_prompt_render_failure_passes_through() -> None:
    """已知名渲染期的真实失败经 super() 原样冒泡，不改写为 Unknown prompt。

    SDK 把渲染异常包装为携带 prompt 名的 ValueError；名称分类只发生在查找点。
    """

    @server.mcp.prompt(name="seedream_render_boom")
    async def _render_boom() -> str:
        raise RuntimeError("render boom")

    try:
        with pytest.raises(ValueError, match="Error rendering prompt seedream_render_boom"):
            await server.mcp.get_prompt("seedream_render_boom")
    finally:
        server.mcp.remove_prompt("seedream_render_boom")


def _prompts_get_body_without_name(envelope_version: str | None) -> bytes:
    """构造缺失 name 的 prompts/get 请求体，envelope_version 非 None 时携带现代协议信封。"""
    params: dict[str, object] = {}
    if envelope_version is not None:
        params["_meta"] = modern_meta_envelope(envelope_version)
    return json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": params}
    ).encode("utf-8")


@pytest.mark.parametrize("protocol_version", ["2026-07-28", "2025-11-25"])
async def test_malformed_prompt_get_keeps_invalid_params_shape(
    reset_http_app_state: None, protocol_version: str
) -> None:
    """缺失 name 的 prompts/get 在两条纪元路径保持参数校验错误形态，不被补偿改写。

    子类 override 的预检只归位未知名的错误码，畸变参数在进入名称查找前已被
    参数面校验拒绝，线缆级应保持 Invalid request parameters 而非 Unknown prompt。类型化
    Client 在本地拒绝畸变参数，故经原始 HTTP 直发。
    """
    app = build_transport_app("s3cret", stateless=True, json_response=True)
    headers = {
        "authorization": "Bearer s3cret",
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": protocol_version,
    }
    envelope = "2026-07-28" if protocol_version == "2026-07-28" else None
    if envelope is not None:
        # 现代纪元要求 Mcp-Method 头与 body.method 一致，缺失 name 时名称头免检。
        headers["mcp-method"] = "prompts/get"

    async with _LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as client:
            response = await client.post(
                "/mcp", content=_prompts_get_body_without_name(envelope), headers=headers
            )

    body = response.json()
    # 现代纪元按错误码映射 HTTP 状态，legacy 纪元 JSON-RPC 错误随 200 应答。
    assert response.status_code == (400 if envelope is not None else 200)
    assert body["error"]["code"] == INVALID_PARAMS
    assert "Invalid request parameters" in body["error"]["message"]


async def test_unknown_tool_call_returns_is_error_result(reset_lifespan_singletons: None) -> None:
    """锁定 SDK 2.2.0 行为：未知工具走 isError 结果通道而非 -32602 协议错误，SDK 升级行为漂移时本测试变红。"""
    async with Client(server.mcp) as client:
        result = await client.call_tool("definitely_not_a_tool", {})

    assert result.is_error is True
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    assert any("Unknown tool" in text for text in texts)


def _tools_list_body(envelope_version: str) -> bytes:
    """构造 tools/list 请求体，_meta 携带现代协议信封并声明给定版本。"""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": modern_meta_envelope(envelope_version)},
        }
    ).encode("utf-8")


async def test_era_routing_follows_protocol_version_header(reset_http_app_state: None) -> None:
    """锁定 SDK 2.2.0 行为：请求头与 body _meta 声明不同纪元时按请求头派生路由，SDK 升级行为漂移时本测试变红。

    头为 2026-07-28 而 body 信封为 2025-11-25 时由现代入口以 HEADER_MISMATCH
    拒绝；头为 2025-11-25 而 body 信封为 2026-07-28 时仍走 legacy 入口返回
    tools/list 结果，两侧共同锁定 body 声明不参与纪元裁决。
    """
    app = build_transport_app("s3cret", stateless=True, json_response=True)
    base_headers = {
        "authorization": "Bearer s3cret",
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }

    async with _LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as client:
            header_modern = await client.post(
                "/mcp",
                content=_tools_list_body("2025-11-25"),
                headers={**base_headers, "mcp-protocol-version": "2026-07-28"},
            )
            header_legacy = await client.post(
                "/mcp",
                content=_tools_list_body("2026-07-28"),
                headers={**base_headers, "mcp-protocol-version": "2025-11-25"},
            )

    modern_body = header_modern.json()
    assert header_modern.status_code == 400
    assert modern_body["error"]["code"] == HEADER_MISMATCH
    assert "does not match" in modern_body["error"]["message"]

    legacy_body = header_legacy.json()
    assert header_legacy.status_code == 200
    assert "error" not in legacy_body
    assert len(legacy_body["result"]["tools"]) > 0
