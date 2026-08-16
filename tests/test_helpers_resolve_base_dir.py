"""_resolve_base_dir 与 validate_image_path 的基础目录边界安全测试。

直接针对 ``tools/core/_helpers._resolve_base_dir`` 的当前契约：用户 save_path
经规范化后必须落在配置的 auto_save_base_dir 之内，含 ``..`` 逃逸或绝对路径越界
均抛 SeedreamValidationError；另覆盖 validate_image_path 在 base_dir 缺省回退
工作区根时的越界强制。
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
    """auto_save_base_dir 为 None 时回退到 get_workspace_root()/.seedream/images。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(workspace))
    config = SeedreamConfig(api_key="test_key")  # auto_save_base_dir 默认 None

    resolved = _resolve_base_dir(config, None)

    assert resolved == (workspace / ".seedream" / "images").resolve()


def test_validate_image_path_none_base_dir_falls_back_and_enforces_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """base_dir 为 None 时回退 get_workspace_root() 并始终执行越界校验。

    越界路径（含 .. 穿越）即使不传 base_dir 也须被判无效，不再静默放行；边界内真实小图
    返回有效。monkeypatch get_workspace_root 返回独立 workspace，隔离环境变量与配置。
    """
    import seedream_mcp.utils.images.image_validation as image_validation_module
    from PIL import Image

    from seedream_mcp.utils.images.image_validation import validate_image_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(image_validation_module, "get_workspace_root", lambda: workspace)

    # 边界内真实小图：返回有效，证明 base_dir=None 回退后正常放行合法路径
    img = workspace / "ok.png"
    Image.new("RGB", (32, 32), color=(0, 0, 255)).save(img)
    is_valid, err, normalized = validate_image_path(str(img), base_dir=None)
    assert is_valid is True
    assert err == ""
    assert normalized is not None

    # 越界穿越路径：不传 base_dir 时仍须判无效；回归背景为此前 base_dir=None 会静默放行
    escape = str(workspace / ".." / "escape.png")
    is_valid_escape, err_escape, _ = validate_image_path(escape, base_dir=None)
    assert is_valid_escape is False
    assert "超出允许的工作区目录范围" in err_escape
