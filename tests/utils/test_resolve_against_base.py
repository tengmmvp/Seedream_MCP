"""resolve_against_base 组合原语测试：基准解析与界内判定的共用契约。

形态拒绝语义随 normalize_path 单源演化，此处锁定组合契约本身——解析产物与
界内性判定，供 Web 图库、Web 文件端点与 browse 核心共同依赖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedream_mcp.utils.io.path import resolve_against_base


def test_resolves_relative_inside_base(tmp_path: Path) -> None:
    """相对路径以基准目录解析，基准内路径（含基准自身）判界内。"""
    inside = tmp_path / "day"
    inside.mkdir()

    resolution = resolve_against_base("day", tmp_path)
    base_itself = resolve_against_base(".", tmp_path)

    assert resolution.resolved == inside.resolve()
    assert resolution.within_base is True
    assert base_itself.resolved == tmp_path.resolve()
    assert base_itself.within_base is True


def test_flags_outside_paths_absolute_and_relative(tmp_path: Path) -> None:
    """越出基准的相对路径与绝对路径都解析成功并判界外。"""
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    escaped = resolve_against_base("../outside", base)
    absolute = resolve_against_base(str(outside), base)

    assert escaped.resolved == outside.resolve()
    assert escaped.within_base is False
    assert absolute.resolved == outside.resolve()
    assert absolute.within_base is False


def test_raises_on_invalid_form(tmp_path: Path) -> None:
    """形态非法路径抛 ValueError，拒绝语义随 normalize_path 单源。"""
    with pytest.raises(ValueError):
        resolve_against_base("//server/share/x", tmp_path)
