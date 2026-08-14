"""_resolve_base_dir 的 save_path 路径穿越守卫测试。"""

from pathlib import Path

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core.common import _resolve_base_dir
from seedream_mcp.utils.core.errors import SeedreamValidationError


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


def test_validate_image_path_none_base_dir_falls_back_and_enforces_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """base_dir 为 None 时回退 get_workspace_root() 并始终执行越界校验。

    越界路径（含 .. 穿越）即使不传 base_dir 也须被判无效，不再静默放行；边界内真实小图
    返回有效。monkeypatch get_workspace_root 返回独立 workspace，隔离环境变量与配置。
    """
    import seedream_mcp.utils.io.io_path as path_utils_module
    from PIL import Image

    from seedream_mcp.utils.io.io_path import validate_image_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(path_utils_module, "get_workspace_root", lambda: workspace)

    # 边界内真实小图：返回有效，证明 base_dir=None 回退后正常放行合法路径
    img = workspace / "ok.png"
    Image.new("RGB", (32, 32), color=(0, 0, 255)).save(img)
    is_valid, err, normalized = validate_image_path(str(img), base_dir=None)
    assert is_valid is True
    assert err == ""
    assert normalized is not None

    # 越界穿越路径：不传 base_dir 时仍须判无效（回归：此前 base_dir=None 会静默放行）
    escape = str(workspace / ".." / "escape.png")
    is_valid_escape, err_escape, _ = validate_image_path(escape, base_dir=None)
    assert is_valid_escape is False
    assert "超出允许的工作区目录范围" in err_escape
