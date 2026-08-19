"""validate_image_input 回归测试，重点守护校验异常不得被二次包装。

该回归源于 _validate_url 缺少 ``except SeedreamValidationError: raise`` 前置分支，
导致自身抛出的"无效的URL格式"被 ``except Exception`` 包装为"URL验证失败: 无效的URL格式"。
另覆盖 urlparse 的 ValueError 逃逸回归：不闭合 IPv6 括号使 urlparse 抛
ValueError，download 侧须归一为 DownloadError 而非让原生异常上抛。
"""

from pathlib import Path

import pytest

from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.images.image_validation import validate_image_input
from seedream_mcp.utils.io.io_download import DownloadError, DownloadManager


def test_validate_image_input_rejects_missing_host_without_message_rewrap() -> None:
    """缺少 host 的 URL 必须直接报「无效的URL格式」，不得被二次包装。"""
    with pytest.raises(SeedreamValidationError, match="^无效的URL格式$") as exc_info:
        validate_image_input("http://")
    assert exc_info.value.message == "无效的URL格式"


def test_validate_image_input_rejects_empty_input() -> None:
    with pytest.raises(SeedreamValidationError, match="不能为空"):
        validate_image_input("")
    with pytest.raises(SeedreamValidationError, match="不能为空"):
        validate_image_input("   ")


def test_validate_image_input_accepts_valid_http_url() -> None:
    url = "https://example.com/path/image.png"
    assert validate_image_input(url) == url


def test_validate_url_returns_false_for_unclosed_ipv6_brackets() -> None:
    """urlparse 对不闭合 IPv6 括号抛 ValueError，validate_url 须归一为 False 不逃逸。"""
    manager = DownloadManager()
    for url in ("http://[::1", "http://[::1/x.png", "https://[2001:db8::1"):
        assert manager.validate_url(url) is False


async def test_download_image_raises_download_error_for_unclosed_ipv6_url(
    tmp_path: Path,
) -> None:
    """download_image 收到不闭合 IPv6 括号 URL 须抛 DownloadError，而非 ValueError。"""
    async with DownloadManager() as manager:
        with pytest.raises(DownloadError, match="无效的URL") as excinfo:
            await manager.download_image("http://[::1", tmp_path / "out.png")
    assert not isinstance(excinfo.value, ValueError)
    assert not (tmp_path / "out.png").exists()
