"""image_input 预处理测试。

覆盖读取路径符号链接拒绝（O_NOFOLLOW / Windows is_symlink 兜底）与本地图片
单次读取后的内存维度校验路径。
"""

import os
from pathlib import Path

import pytest
from PIL import Image

from seedream_mcp.utils.errors import SeedreamAPIError
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
    """本地图片读取并编码为 data URI，维度校验在内存完成（单次读取）。"""
    image_path = tmp_path / "local.png"
    Image.new("RGB", (64, 64), color="black").save(image_path)

    result = await prepare_image_input(str(image_path))
    assert result.startswith("data:image/")
