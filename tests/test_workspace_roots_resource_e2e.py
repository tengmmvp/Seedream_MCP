"""seedream://workspace/roots 模板资源端到端守护测试。

经 SDK in-process Client 驱动真实读取管线：URI 模板匹配、pydantic validate_call
包装、Context 注入与 handler 执行全部在管线内完成，测试不直调 handler。项目曾因
参数化 Context 注解使模板资源以 Error creating resource from template 失败，本
套件锁定该管线与回退语义不回退。另守护 initialize 握手报告的 serverInfo.version
为项目版本号。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.client import Client, ClientRequestContext
from mcp.types import ListRootsResult, ReadResourceResult, Root

import seedream_mcp.resources as resources
import seedream_mcp.server as server
from seedream_mcp import config as config_module
from seedream_mcp.config import SeedreamConfig


@pytest.fixture
async def reset_lifespan_singletons(monkeypatch: pytest.MonkeyPatch):
    """重置 lifespan 单例并注入活动配置，测试后关闭残留实例并再次复位。"""
    server._reset_lifespan_state()
    monkeypatch.setattr(config_module, "_active_config", SeedreamConfig(api_key="test_key"))
    yield
    active = resources._active_resource
    if active is not None:
        await active.client.close()
        await active.download_manager.close()
    for retired in list(resources._retired_resources):
        await retired.client.close()
        await retired.download_manager.close()
    server._reset_lifespan_state()


def _single_text_payload(result: ReadResourceResult) -> dict:
    """断言读取结果为单一 JSON 文本内容并返回解析后的字典。"""
    assert len(result.contents) == 1
    content = result.contents[0]
    assert content.mime_type == "application/json"
    assert isinstance(content.text, str)
    return json.loads(content.text)


async def test_initialize_reports_project_version(
    reset_lifespan_singletons: None,
) -> None:
    """initialize 握手的 serverInfo.version 非空且等于项目 __version__。

    SDK 2.0 起未向 MCPServer 传 version 的服务器在 serverInfo 中报告空串，
    客户端侧的版本展示与兼容性判断将失去依据。
    """
    async with Client(server.mcp, mode="legacy") as client:
        server_info = client.server_info
        assert server_info is not None
        assert server_info.name == server.SERVER_NAME
        assert server_info.version != ""
        assert server_info.version == resources.SERVER_VERSION


async def test_workspace_roots_resource_reads_over_wire_without_roots_callback(
    reset_lifespan_singletons: None,
) -> None:
    """默认协商路径读取模板资源返回 JSON，客户端未声明 roots 时输出空 roots。

    客户端未设 roots callback 即不声明 roots capability，handler 跳过 roots/list
    往返回退环境变量边界。回退边界属服务器环境而非客户端授权声明，其绝对路径不
    进入面向调用方的输出，两种读取形态均为受控的空列表。
    """
    async with Client(server.mcp) as client:
        plain = await client.read_resource("seedream://workspace/roots")
        assert _single_text_payload(plain) == {"roots": []}

        verbose = await client.read_resource("seedream://workspace/roots?verbose=true")
        assert _single_text_payload(verbose) == {"roots": [], "resolved": []}


async def test_workspace_roots_resource_reports_client_roots(
    tmp_path: Path,
    reset_lifespan_singletons: None,
) -> None:
    """legacy 协商加 roots callback 时，模板资源经会话 Roots 往返输出授权根目录。

    handler 经注入的 Context 发起 roots/list，客户端 callback 应答的根目录进入
    输出：roots 为反斜杠归一后的展示形态，verbose 附 resolve 后的物理路径。此
    路径同时守护 Context 注入实例的 session 可用性，参数化注解回退为脱离请求的
    实例时首次访问 ctx.session 即失败。
    """
    declared_root = tmp_path / "workspace"
    declared_root.mkdir()

    async def roots_callback(context: ClientRequestContext) -> ListRootsResult:
        del context
        return ListRootsResult(roots=[Root(uri=declared_root.as_uri(), name="workspace")])

    async with Client(server.mcp, mode="legacy", list_roots_callback=roots_callback) as client:
        plain = await client.read_resource("seedream://workspace/roots")
        expected_display = str(declared_root.resolve()).replace("\\", "/")
        assert _single_text_payload(plain) == {"roots": [expected_display]}

        verbose = await client.read_resource("seedream://workspace/roots?verbose=true")
        assert _single_text_payload(verbose) == {
            "roots": [expected_display],
            "resolved": [str(declared_root.resolve())],
        }


async def test_workspace_roots_resource_modern_round_trip_reports_client_roots(
    tmp_path: Path,
    reset_lifespan_singletons: None,
) -> None:
    """默认协商（2026-07-28）加 roots callback 时，资源经多轮请求取回授权根目录。

    2026 会话无服务端反向通道，roots 直连必抛 NoBackChannelError；模板资源返回
    InputRequiredResult 携带 roots 请求，客户端 read_resource 驱动多轮循环、
    callback 应答后重试，最终输出与 legacy 直连等价的授权根目录。
    """
    declared_root = tmp_path / "workspace"
    declared_root.mkdir()
    callback_calls = 0

    async def roots_callback(context: ClientRequestContext) -> ListRootsResult:
        nonlocal callback_calls
        del context
        callback_calls += 1
        return ListRootsResult(roots=[Root(uri=declared_root.as_uri(), name="workspace")])

    async with Client(server.mcp, list_roots_callback=roots_callback) as client:
        plain = await client.read_resource("seedream://workspace/roots")
        expected_display = str(declared_root.resolve()).replace("\\", "/")
        assert _single_text_payload(plain) == {"roots": [expected_display]}

        verbose = await client.read_resource("seedream://workspace/roots?verbose=true")
        assert _single_text_payload(verbose) == {
            "roots": [expected_display],
            "resolved": [str(declared_root.resolve())],
        }

    assert callback_calls == 2
