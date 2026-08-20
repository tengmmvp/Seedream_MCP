"""保存路径扩展名推断的 query 与 fragment 隔离测试。

io_url.get_file_extension_from_url 仅从 URL 路径段提取扩展名，签名 query 与
fragment 不参与推断，典型形态为 ?X-Tos-Signature=... 签名串。本文件经
FileManager.create_save_path 端到端锁定该行为，防止退化为对整串 URL 取后缀。
"""

from __future__ import annotations

from pathlib import Path

from seedream_mcp.utils.io.io_storage import FileManager


def test_create_save_path_ignores_query_signature(tmp_path: Path) -> None:
    """带签名 query 的 .png URL 生成的保存路径仍为 .png 扩展名。"""
    manager = FileManager(base_dir=tmp_path)
    save_path = manager.create_save_path(
        prompt="测试提示词",
        url="https://x/a.png?sig=abc",
        tool_name="seedream",
        date_folder=False,
    )

    assert save_path.suffix == ".png"


def test_create_save_path_ignores_fragment(tmp_path: Path) -> None:
    """fragment 不参与扩展名推断，.jpg 路径段仍推断为 .jpg。"""
    manager = FileManager(base_dir=tmp_path)
    save_path = manager.create_save_path(
        prompt="测试提示词",
        url="https://x/b.jpg#section",
        tool_name="seedream",
        date_folder=False,
    )

    assert save_path.suffix == ".jpg"


def test_query_value_with_dot_does_not_leak_into_extension(tmp_path: Path) -> None:
    """query 值含点号与扩展名样文本时不串染路径段扩展名。"""
    manager = FileManager(base_dir=tmp_path)
    save_path = manager.create_save_path(
        prompt="测试提示词",
        url="https://x/c.png?X-Tos-Signature=abc.jpeg",
        tool_name="seedream",
        date_folder=False,
    )

    assert save_path.suffix == ".png"


def test_fragment_with_dot_does_not_leak_into_extension(tmp_path: Path) -> None:
    """fragment 含点号与扩展名样文本时同样不串染路径段扩展名。"""
    manager = FileManager(base_dir=tmp_path)
    save_path = manager.create_save_path(
        prompt="测试提示词",
        url="https://x/d.webp#anchor.jpeg",
        tool_name="seedream",
        date_folder=False,
    )

    assert save_path.suffix == ".webp"
