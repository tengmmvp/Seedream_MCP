"""normalize_path 在 win32 对最终分量尾部点与空格的归一测试。

Win32 命名空间打开文件时剥离最终分量尾部的点与空格，未归一的已验证路径字符串
会与实际打开的文件名不一致；归一仅作用于最终分量，中间目录分量保持原样。
"""

import sys
from pathlib import Path

import pytest

from seedream_mcp.utils.io.io_path import normalize_path

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="尾部点与空格归一仅 win32 生效")


def test_normalize_path_strips_trailing_dot_on_existing_file(tmp_path: Path) -> None:
    """已存在文件的尾部点形态归一到同名文件的已验证路径。"""
    image = tmp_path / "foo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert normalize_path(str(tmp_path / "foo.png.")) == image.resolve()


def test_normalize_path_strips_trailing_space_on_existing_file(tmp_path: Path) -> None:
    """已存在文件的尾部空格形态归一到同名文件的已验证路径。"""
    image = tmp_path / "foo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert normalize_path(str(tmp_path / "foo.png ")) == image.resolve()


def test_normalize_path_strips_mixed_trailing_dots_and_spaces(tmp_path: Path) -> None:
    """尾部点与空格交错时全部剥离，与 Win32 打开语义一致。"""
    image = tmp_path / "foo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert normalize_path(str(tmp_path / "foo.png. .")) == image.resolve()


def test_normalize_path_strips_trailing_dot_for_missing_file(tmp_path: Path) -> None:
    """不存在路径同样剥离尾部点，保证校验通过的字符串与将来打开的文件名一致。"""
    assert normalize_path(str(tmp_path / "missing.png.")) == (tmp_path / "missing.png").resolve()


def test_normalize_path_relative_input_strips_trailing_dot(tmp_path: Path) -> None:
    """相对路径经 base_dir 解析前同样对最终分量归一。"""
    image = tmp_path / "foo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert normalize_path("foo.png.", str(tmp_path)) == image.resolve()


def test_normalize_path_keeps_intermediate_trailing_dot_component(tmp_path: Path) -> None:
    """仅归一最终分量，中间目录分量的尾部点保持原样。"""
    result = normalize_path("keep./x.png", str(tmp_path))

    assert result == (tmp_path / "keep." / "x.png").resolve()
