"""守护测试：HTTP 客户端 trust_env=False 不变量。

防止 HTTP_PROXY/HTTPS_PROXY/NO_PROXY 等环境变量绕过 SSRF 防护或截获 API Key。
trust_env=False 确保 httpx 与 aiohttp 仅使用显式配置的代理，忽略系统代理环境变量。
"""

import aiohttp
import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.io.io_download import DownloadManager


@pytest.mark.asyncio
async def test_seedream_client_httpx_async_client_disables_trust_env() -> None:
    """SeedreamClient 内部 httpx.AsyncClient 的 trust_env 必须为 False。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)
    try:
        # 触发 httpx.AsyncClient 创建，这是 _call_api 路径的前置步骤
        await client._ensure_client()
        assert client._client is not None
        # 直接读 httpx.AsyncClient.trust_env 验证守护不变量
        assert client._client.trust_env is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_download_manager_aiohttp_session_disables_trust_env() -> None:
    """DownloadManager 内部 aiohttp.ClientSession 的 trust_env 必须为 False。"""
    manager = DownloadManager()
    try:
        session = await manager._ensure_session()
        assert isinstance(session, aiohttp.ClientSession)
        # 直接读 aiohttp.ClientSession.trust_env 验证守护不变量
        assert session.trust_env is False
    finally:
        await manager.close()
