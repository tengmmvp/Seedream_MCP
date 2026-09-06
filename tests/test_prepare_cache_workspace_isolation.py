"""守护测试：预处理缓存键的 workspace 隔离、签名 strip 一致性与摘要键容错。

workspace_roots 缺席会使不同工作区跨租户命中；签名与读取路径 strip 不一致会架空
mtime+size 失效保护；摘要键不容错未配对代理字符会中断整批预处理。
"""

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig
from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.images import image_input, image_prepare
from seedream_mcp.utils.images.image_prepare import ImagePreparer


async def test_prepare_image_input_cache_isolated_by_workspace_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspace_roots 变化时同一 image 输入缓存不命中，底层 prepare 被重新调用。"""
    config = SeedreamConfig(api_key="test_key", max_retries=1)
    client = SeedreamClient(config)

    call_count = 0

    async def fake_prepare_image_input(image: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"prepared:{image}"

    # 对象式 monkeypatch：直接作用于模块对象，规避 utils __getattr__ 延迟加载
    monkeypatch.setattr(image_prepare, "prepare_image_input", fake_prepare_image_input)

    # 两次调用返回不同 roots，模拟不同请求上下文 / 租户
    roots_sequence: list[list[Path]] = [
        [Path("/workspace/tenant-a")],
        [Path("/workspace/tenant-b")],
    ]
    call_index: dict[str, int] = {"i": 0}

    def fake_get_workspace_roots() -> list[Path]:
        idx = call_index["i"]
        call_index["i"] += 1
        return list(roots_sequence[idx % len(roots_sequence)])

    monkeypatch.setattr(image_prepare, "get_workspace_roots", fake_get_workspace_roots)

    first = await client._image_preparer.prepare_image_input("same-image.png")
    second = await client._image_preparer.prepare_image_input("same-image.png")

    assert first == "prepared:same-image.png"
    assert second == "prepared:same-image.png"
    # workspace_roots 不同则 cache_key 不同，缓存未命中，底层被调用两次
    assert call_count == 2


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

    monkeypatch.setattr(image_prepare, "prepare_image_input", fake_prepare_image_input)
    monkeypatch.setattr(image_prepare, "get_workspace_roots", lambda: [Path("/workspace/same")])

    await client._image_preparer.prepare_image_input("img.png")
    await client._image_preparer.prepare_image_input("img.png")

    assert call_count == 1


# ==================== 签名路径与读取路径的 strip 一致性 ====================


async def test_prepare_signature_strips_whitespace_like_read_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """带首尾空白路径的签名与读取定位同一物理文件，文件替换后缓存按 mtime+size 失效。

    签名路径不 strip 时带空白输入恒得 (0.0, 0) 签名，失效保护被架空。
    """
    image_path = tmp_path / "ref.png"
    Image.new("RGB", (32, 32), color="white").save(image_path, format="PNG")

    # 缓存键与签名路径经 image_prepare 的 from-import 绑定，读取路径经 image_input
    # 的 from-import 绑定，两个名字须分别替换，否则读取路径落到真实回退根，结果
    # 取决于 basetemp 位置。
    monkeypatch.setattr(image_prepare, "get_workspace_roots", lambda: [tmp_path])
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


async def test_prepare_large_data_uri_with_surrogate_raises_validation_error() -> None:
    """超大含代理字符 data URI 报参数级校验错误，不抛 UnicodeEncodeError 中断整批。

    摘要键容错后非法输入在 base64 解码处失败，文案与 image_validation 口径一致。
    """
    hostile = "data:image/png;base64," + "\ud800" + "a" * (1024 * 1024 + 32)
    preparer = ImagePreparer(
        prepare_cache_max=2, prepare_cache_max_bytes=1024 * 1024, prepare_concurrency=1
    )

    with pytest.raises(SeedreamValidationError, match="Base64 解码失败"):
        await preparer.prepare_image_input(hostile)
