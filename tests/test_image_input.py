"""image_input 预处理测试。

覆盖读取路径符号链接拒绝（O_NOFOLLOW / Windows is_symlink 兜底）与本地图片
单次读取后的内存维度校验路径。
"""

import base64
import io
import os
from pathlib import Path

import pytest
from PIL import Image

from seedream_mcp.utils.errors import SeedreamAPIError, SeedreamValidationError
from seedream_mcp.utils.image_input import prepare_image_input


async def test_prepare_image_input_rejects_symlink(workspace_root: Path, tmp_path: Path) -> None:
    """读取路径符号链接须拒绝，防止 O_NOFOLLOW / TOCTOU 绕过。"""
    target = tmp_path / "real.png"
    Image.new("RGB", (32, 32), color="white").save(target)
    link = tmp_path / "link.png"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("当前环境不支持创建符号链接")

    with pytest.raises(SeedreamAPIError):
        await prepare_image_input(str(link))


async def test_prepare_image_input_reads_local_file(workspace_root: Path, tmp_path: Path) -> None:
    """本地图片读取并编码为 data URI，维度校验在内存单次读取完成。"""
    image_path = tmp_path / "local.png"
    Image.new("RGB", (64, 64), color="black").save(image_path)

    result = await prepare_image_input(str(image_path))
    assert result.startswith("data:image/")


# ---- URL 与 Data URI 主干路径 ----


async def test_prepare_image_input_returns_https_url_unchanged() -> None:
    """HTTP/HTTPS URL 经主机校验后原样返回，不触网也不改写。"""
    url = "https://example.com/path/img.png"
    assert await prepare_image_input(url) == url


async def test_prepare_image_input_returns_http_url_unchanged() -> None:
    """http 协议同样原样返回。"""
    url = "http://example.com/img.png"
    assert await prepare_image_input(url) == url


async def test_prepare_image_input_validates_and_returns_data_uri() -> None:
    """合法 Data URI 经格式与维度校验后原样返回。"""
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color="white").save(buffer, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    assert await prepare_image_input(data_uri) == data_uri


async def test_prepare_image_input_rejects_url_without_netloc() -> None:
    """以 http:// 开头但缺少主机名的 URL 须在入参校验即拒绝。"""
    with pytest.raises(SeedreamAPIError, match="无效的图像 URL"):
        await prepare_image_input("http://")


async def test_prepare_image_input_rejects_invalid_data_uri() -> None:
    """非法 base64 负载的 Data URI 须被校验透传拒绝，校验错误原样上抛不被吞掉。"""
    with pytest.raises(SeedreamValidationError, match="Base64 解码失败|Data URI"):
        await prepare_image_input("data:image/png;base64,@@not_base64@@")
