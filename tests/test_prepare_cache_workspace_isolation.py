"""守护测试：预处理缓存键的 workspace 隔离、签名 strip 一致性与摘要键容错。

workspace_roots 缺失缓存键维度会使不同工作区的请求跨租户命中，本地图片被错误地
按另一工作区的缓存结果返回；签名路径与读取路径的 strip 不一致会架空 mtime+size
失效保护；摘要键对未配对代理字符不容错会使批量预处理整批中断。
"""

import io
import os
from pathlib import Path
from typing import Dict, List

import pytest
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.images import image_input
from seedream_mcp.utils.images.image_prepare import ImagePreparer
from seedream_mcp.utils.io import io_path as path_utils


@pytest.mark.asyncio
async def test_prepare_image_input_cache_isolated_by_workspace_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspace_roots 变化时同一 image 输入不应命中缓存，底层 prepare 被重新调用。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)

    call_count = 0

    async def fake_prepare_image_input(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}"

    # 用对象式 monkeypatch 直接作用于模块对象，避免字符串路径解析在
    # seedream_mcp 顶层 __getattr__ lazy export 下受测试顺序影响
    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare_image_input)

    # 两次调用返回不同 roots，模拟不同请求上下文 / 租户
    roots_sequence: List[List[Path]] = [
        [Path("/workspace/tenant-a")],
        [Path("/workspace/tenant-b")],
    ]
    call_index: Dict[str, int] = {"i": 0}

    def fake_get_workspace_roots() -> List[Path]:
        idx = call_index["i"]
        call_index["i"] += 1
        return list(roots_sequence[idx % len(roots_sequence)])

    monkeypatch.setattr(path_utils, "get_workspace_roots", fake_get_workspace_roots)

    first = await client._image_preparer.prepare_image_input("same-image.png")
    second = await client._image_preparer.prepare_image_input("same-image.png")

    assert first == "prepared:same-image.png"
    assert second == "prepared:same-image.png"
    # workspace_roots 不同 → cache_key 不同 → 缓存未命中 → 底层被调用两次
    assert call_count == 2


@pytest.mark.asyncio
async def test_prepare_image_input_cache_hit_when_workspace_roots_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspace_roots 相同时，同一 image 第二次调用走缓存，底层仅调用一次。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)

    call_count = 0

    async def fake_prepare_image_input(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}"

    monkeypatch.setattr(image_input, "prepare_image_input", fake_prepare_image_input)
    monkeypatch.setattr(path_utils, "get_workspace_roots", lambda: [Path("/workspace/same")])

    await client._image_preparer.prepare_image_input("img.png")
    await client._image_preparer.prepare_image_input("img.png")

    assert call_count == 1


# ==================== 签名路径与读取路径的 strip 一致性 ====================


@pytest.mark.asyncio
async def test_prepare_signature_strips_whitespace_like_read_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """带首尾空白路径的签名与读取定位同一物理文件，文件替换后缓存按 mtime+size 失效。

    签名路径不 strip 时对带空白输入恒得 (0.0, 0) 签名，mtime+size 失效保护被架空，
    编辑替换图片后持续返回旧编码；strip 后两条路径对同一物理文件求签名。
    """
    image_path = tmp_path / "ref.png"
    Image.new("RGB", (32, 32), color="white").save(image_path, format="PNG")

    # 读取路径经 image_input 顶层 from-import 绑定 get_workspace_roots，签名与缓存键
    # 路径在函数内延迟导入后解析 io_path 模块属性，两个名字必须同时替换，否则读取
    # 路径落到真实回退根，测试结果取决于 basetemp 是否恰在回退根之内。
    monkeypatch.setattr(path_utils, "get_workspace_roots", lambda: [tmp_path])
    monkeypatch.setattr(image_input, "get_workspace_roots", lambda: [tmp_path])
    preparer = ImagePreparer(
        prepare_cache_max=8, prepare_cache_max_bytes=64 * 1024 * 1024, prepare_concurrency=2
    )

    padded = f"  {image_path} "
    first_result = await preparer.prepare_image_input(padded)
    assert first_result.startswith("data:image/png;base64,")

    # 替换为不同内容并显式推进 mtime，规避文件系统时间精度差异
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color="black").save(buffer, format="PNG")
    image_path.write_bytes(buffer.getvalue())
    stat = image_path.stat()
    os.utime(image_path, (stat.st_atime + 10, stat.st_mtime + 10))

    second_result = await preparer.prepare_image_input(padded)

    assert second_result != first_result
    assert second_result.startswith("data:image/png;base64,")


# ==================== 超大输入摘要键的代理字符容错 ====================


def test_data_uri_digest_tolerates_unpaired_surrogate() -> None:
    """未配对代理字符经 replace 编码进摘要，不在此抛 UnicodeEncodeError。"""
    digest = ImagePreparer._data_uri_digest("data:image/png;base64,\ud800abc")

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 32


@pytest.mark.asyncio
async def test_prepare_large_data_uri_with_surrogate_raises_validation_error() -> None:
    """超大含代理字符 data URI 报参数级校验错误，不抛 UnicodeEncodeError 中断整批。

    摘要键容错后，非法输入在 validate_image_input 的 base64 解码处失败，与
    image_validation 的 Base64 解码失败文案口径一致。
    """
    hostile = "data:image/png;base64," + "\ud800" + "a" * (1024 * 1024 + 32)
    preparer = ImagePreparer(
        prepare_cache_max=2, prepare_cache_max_bytes=1024 * 1024, prepare_concurrency=1
    )

    with pytest.raises(SeedreamValidationError, match="Base64 解码失败"):
        await preparer.prepare_image_input(hostile)
