"""_resolve_base_dir 的正向放行与路径穿越拒绝测试。

直接针对 ``tools/core/_helpers._resolve_base_dir`` 的当前契约：用户 save_path
经规范化后必须落在配置的 auto_save_base_dir 之内，含 ``..`` 逃逸或绝对路径越界
均抛 SeedreamValidationError。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core._helpers import _resolve_base_dir
from seedream_mcp.utils.core.errors import SeedreamValidationError


def _make_config(base_dir: Path) -> SeedreamConfig:
    return SeedreamConfig(api_key="test_key", auto_save_base_dir=str(base_dir))


def test_resolve_base_dir_accepts_nested_save_path_within_base(
    tmp_path: Path,
) -> None:
    """base_dir 内的相对子目录路径规范化后仍位于 base_dir 内，放行。"""
    base = tmp_path / "save_root"
    base.mkdir()
    config = _make_config(base)

    resolved = _resolve_base_dir(config, "sub/dir")

    assert resolved == (base / "sub" / "dir").resolve()


def test_resolve_base_dir_accepts_absolute_save_path_within_base(
    tmp_path: Path,
) -> None:
    """base_dir 内的绝对路径放行。"""
    base = tmp_path / "save_root"
    nested = base / "inside"
    nested.mkdir(parents=True)
    config = _make_config(base)

    resolved = _resolve_base_dir(config, str(nested))

    assert resolved == nested.resolve()


def test_resolve_base_dir_rejects_traversal_escape(tmp_path: Path) -> None:
    """含 ``..`` 逃逸到 base_dir 之外的相对路径被拒绝。"""
    base = tmp_path / "save_root"
    base.mkdir()
    config = _make_config(base)

    with pytest.raises(SeedreamValidationError, match="超出允许范围"):
        _resolve_base_dir(config, "../../outside")


def test_resolve_base_dir_rejects_absolute_path_outside_base(
    tmp_path: Path,
) -> None:
    """指向 base_dir 之外的绝对路径被拒绝。"""
    base = tmp_path / "save_root"
    base.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config = _make_config(base)

    with pytest.raises(SeedreamValidationError, match="超出允许范围"):
        _resolve_base_dir(config, str(elsewhere))


def test_resolve_base_dir_returns_default_when_save_path_missing(
    tmp_path: Path,
) -> None:
    """未提供 save_path 时返回配置的默认基础目录。"""
    base = tmp_path / "save_root"
    base.mkdir()
    config = _make_config(base)

    resolved = _resolve_base_dir(config, None)

    assert resolved == base.resolve()


def test_resolve_base_dir_falls_back_to_workspace_images_when_base_dir_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto_save_base_dir 为 None 时回退到 get_workspace_root()/images。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))
    config = SeedreamConfig(api_key="test_key")  # auto_save_base_dir 默认 None

    resolved = _resolve_base_dir(config, None)

    assert resolved == (workspace / "images").resolve()
