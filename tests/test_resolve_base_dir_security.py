"""_resolve_base_dir 的 save_path 路径穿越守卫测试。"""

from pathlib import Path

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import _resolve_base_dir
from seedream_mcp.utils.errors import SeedreamValidationError


def test_resolve_base_dir_rejects_traversal_save_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))
    config = SeedreamConfig(api_key="k", auto_save_base_dir=str(workspace))

    # 相对路径遍历到 base_dir 之外
    with pytest.raises(SeedreamValidationError, match="超出允许范围"):
        _resolve_base_dir(config, "../../outside")


def test_resolve_base_dir_rejects_absolute_save_path_outside(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))
    config = SeedreamConfig(api_key="k", auto_save_base_dir=str(workspace))

    with pytest.raises(SeedreamValidationError, match="超出允许范围"):
        _resolve_base_dir(config, str(tmp_path / "elsewhere"))


def test_resolve_base_dir_accepts_save_path_within_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))
    config = SeedreamConfig(api_key="k", auto_save_base_dir=str(workspace))

    resolved = _resolve_base_dir(config, "sub/dir")
    assert resolved == (workspace / "sub" / "dir").resolve()


def test_resolve_base_dir_returns_default_when_no_save_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))
    config = SeedreamConfig(api_key="k", auto_save_base_dir=str(workspace))

    resolved = _resolve_base_dir(config, None)
    assert resolved == workspace
