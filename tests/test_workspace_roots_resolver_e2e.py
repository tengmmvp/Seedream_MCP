"""工具链 roots resolver 注入端到端守护测试。

经 SDK in-process Client 驱动真实 tools/call 管线：resolver 依赖在工具执行前按
协商版本取回客户端 roots 并注入，workspace_roots_scope_from_result 应用为文件
访问边界。SEP-2577 下工具链不经 ctx.session.list_roots 直连读取，本套件锁定
resolver 形态的边界生效、未声明能力的回退与越界拒绝语义。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client, ClientRequestContext
from mcp.types import CallToolResult, ListRootsResult, Root

import seedream_mcp.resources as resources
import seedream_mcp.server as server
from seedream_mcp import config as config_module
from seedream_mcp.config import SeedreamConfig

PNG_BYTES = b"\x89PNG\r\n\x1a\n"


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


def _make_callback(roots: list[Path]) -> Any:
    async def roots_callback(context: ClientRequestContext) -> ListRootsResult:
        del context
        return ListRootsResult(roots=[Root(uri=root.as_uri(), name=root.name) for root in roots])

    return roots_callback


async def _browse(client: Client, directory: str) -> CallToolResult:
    return await client.call_tool("browse_images", {"directory": directory, "recursive": False})


async def test_resolver_applies_client_roots_boundary(
    tmp_path: Path,
    reset_lifespan_singletons: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """legacy 协商加 roots callback 时，resolver 取回的根目录成为工具文件边界。

    声明根内可列出图片，声明的根之外即便环境变量根内有文件也拒绝访问。
    """
    declared_root = tmp_path / "declared"
    declared_root.mkdir()
    (declared_root / "inside.png").write_bytes(PNG_BYTES)
    env_root = tmp_path / "env"
    env_root.mkdir()
    (env_root / "outside.png").write_bytes(PNG_BYTES)
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(env_root))

    async with Client(
        server.mcp, mode="legacy", list_roots_callback=_make_callback([declared_root])
    ) as client:
        allowed = await _browse(client, ".")
        assert allowed.is_error is False
        structured = allowed.structured_content
        assert isinstance(structured, dict)
        assert structured["count"] == 1

        denied = await _browse(client, str(env_root))
        assert denied.is_error is True


async def test_resolver_falls_back_when_roots_capability_not_declared(
    tmp_path: Path,
    reset_lifespan_singletons: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """客户端未声明 roots capability 时 resolver 不发起取回，回退配置根。

    默认协商（2026-07-28）下无 roots callback 即不声明能力，工具照常执行并
    以 SEEDREAM_WORKSPACE_ROOT 为边界；活动配置的 workspace_root 与该环境
    变量同源，此处直接注入携带该值的配置等效表达。
    """
    env_root = tmp_path / "env"
    env_root.mkdir()
    (env_root / "fallback.png").write_bytes(PNG_BYTES)
    monkeypatch.setattr(
        config_module,
        "_active_config",
        SeedreamConfig(api_key="test_key", workspace_root=str(env_root)),
    )

    async with Client(server.mcp) as client:
        result = await _browse(client, ".")
        assert result.is_error is False
        structured = result.structured_content
        assert isinstance(structured, dict)
        assert structured["count"] == 1


async def test_resolver_over_modern_negotiation(
    tmp_path: Path,
    reset_lifespan_singletons: None,
) -> None:
    """默认协商（2026-07-28）加 roots callback 时，resolver 经 MRTR 取回根目录。

    2026-07-28 连接的 roots 取回由多轮往返承载，客户端应答后调用继续，边界
    语义与 legacy 协商一致。
    """
    declared_root = tmp_path / "modern"
    declared_root.mkdir()
    (declared_root / "modern.png").write_bytes(PNG_BYTES)

    async with Client(server.mcp, list_roots_callback=_make_callback([declared_root])) as client:
        result = await _browse(client, ".")
        assert result.is_error is False
        structured = result.structured_content
        assert isinstance(structured, dict)
        assert structured["count"] == 1
