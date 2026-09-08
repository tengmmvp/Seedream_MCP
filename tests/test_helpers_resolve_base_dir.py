"""图片目录求值与 save_path 调用级声明的解析测试。

resolve_images_root 按显式数据根目录 > 工作根目录派生求值，两级分支的缓存随配置写入失效；
save_path 为调用级保存声明，位置不受限，相对形态以图片目录为基准，仅做输入清洗。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.tools.core._helpers import _resolve_base_dir
from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.utils.io.io_path import (
    _RESOLVED_DATA_ROOT_CACHE,
    clear_resolved_env_root_cache,
    resolve_images_root,
)


def _use_config(config: SeedreamConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 config 设为活动配置，conftest 的配置重置基线负责还原。"""
    del monkeypatch
    set_active_config(config)


def test_resolve_base_dir_returns_images_root_when_save_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未提供 save_path 时返回图片目录，数据根目录的显式声明同样派生 .seedream/images。"""
    base = tmp_path / "images_root"
    base.mkdir()
    _use_config(SeedreamConfig(api_key="test_key", data_root=str(base)), monkeypatch)

    assert _resolve_base_dir(None) == (base / ".seedream" / "images").resolve()


def test_resolve_base_dir_resolves_relative_save_path_against_images_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_path 相对形态以图片目录为基准解析。"""
    base = tmp_path / "images_root"
    base.mkdir()
    _use_config(SeedreamConfig(api_key="test_key", data_root=str(base)), monkeypatch)

    assert _resolve_base_dir("sub/dir") == (base / ".seedream" / "images" / "sub" / "dir").resolve()


def test_resolve_base_dir_accepts_save_path_outside_images_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_path 为调用级保存声明，指向图片目录之外的绝对路径放行。"""
    base = tmp_path / "images_root"
    base.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _use_config(SeedreamConfig(api_key="test_key", data_root=str(base)), monkeypatch)

    assert _resolve_base_dir(str(elsewhere)) == elsewhere.resolve()
    assert _resolve_base_dir("../../outside") == (base / "outside").resolve()


def test_resolve_base_dir_rejects_invalid_save_path_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_path 的 UNC 形态在 resolve 前被输入级拒绝。"""
    base = tmp_path / "images_root"
    base.mkdir()
    _use_config(SeedreamConfig(api_key="test_key", data_root=str(base)), monkeypatch)

    with pytest.raises(SeedreamValidationError, match="保存路径无效"):
        _resolve_base_dir("\\\\host\\share\\img")


def test_resolve_images_root_derives_from_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未配置数据根目录时图片目录由工作根目录派生：<工作区根>/.seedream/images。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _use_config(SeedreamConfig(api_key="test_key", workspace_root=str(workspace)), monkeypatch)

    assert resolve_images_root() == (workspace / ".seedream" / "images").resolve()


def test_resolve_images_root_caches_resolved_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式数据根目录的 resolve 结果经进程级缓存，同一配置串仅首次触发 resolve。

    缓存随 clear_resolved_env_root_cache 失效，失效后再次调用按配置重新解析。
    """
    base = tmp_path / "images_root"
    base.mkdir()
    _use_config(SeedreamConfig(api_key="test_key", data_root=str(base)), monkeypatch)

    resolve_calls = 0
    real_resolve = Path.resolve

    def counting_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        return real_resolve(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "resolve", counting_resolve)

    first = resolve_images_root()
    # 数据根目录与 .seedream 尾部各一次 resolve，整条路径进缓存
    assert resolve_calls == 2
    again = resolve_images_root()
    assert resolve_calls == 2
    assert again == first

    clear_resolved_env_root_cache()
    resolve_images_root()
    assert resolve_calls == 4


def test_resolve_images_root_caches_workspace_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未配置数据根目录时默认图片目录经进程级缓存，二次调用整链零 resolve。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _use_config(SeedreamConfig(api_key="test_key", workspace_root=str(workspace)), monkeypatch)

    first = resolve_images_root()
    expected = (workspace / ".seedream" / "images").resolve()

    resolve_calls = 0
    real_resolve = Path.resolve

    def counting_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        return real_resolve(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "resolve", counting_resolve)

    again = resolve_images_root()
    assert resolve_calls == 0
    assert again == first == expected


def test_resolve_images_root_cache_invalidated_by_active_config_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_active_config 写入新配置后默认分支缓存一并失效，按新工作根目录重新解析。"""
    workspace_a = tmp_path / "ws_a"
    workspace_a.mkdir()
    _use_config(SeedreamConfig(api_key="test_key", workspace_root=str(workspace_a)), monkeypatch)
    first = resolve_images_root()
    assert first == (workspace_a / ".seedream" / "images").resolve()
    assert f"default-images:{workspace_a.resolve()}" in _RESOLVED_DATA_ROOT_CACHE

    workspace_b = tmp_path / "ws_b"
    workspace_b.mkdir()
    set_active_config(SeedreamConfig(api_key="test_key", workspace_root=str(workspace_b)))
    assert _RESOLVED_DATA_ROOT_CACHE == {}

    second = resolve_images_root()
    assert second == (workspace_b / ".seedream" / "images").resolve()


def test_resolve_images_root_cache_keys_isolate_explicit_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式与默认分支缓存键分别带 explicit-images:/default-images: 前缀，同串不串键。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    _use_config(SeedreamConfig(api_key="test_key", workspace_root=str(workspace)), monkeypatch)
    default_dir = resolve_images_root()
    assert default_dir == (workspace / ".seedream" / "images").resolve()

    # 同一字符串两分支并存：显式分支以显式配置串为键，与前缀化的默认键互不覆盖。
    _RESOLVED_DATA_ROOT_CACHE[f"explicit-images:{workspace}"] = workspace.resolve()
    assert _RESOLVED_DATA_ROOT_CACHE[f"default-images:{workspace.resolve()}"] == default_dir
    assert _RESOLVED_DATA_ROOT_CACHE[f"explicit-images:{workspace}"] == workspace.resolve()


def test_resolve_base_dir_absolute_save_path_skips_unresolvable_images_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """绝对 save_path 不依赖基准，数据根目录声明不可解析时不被计费前预检拒绝。

    与纯远端参考图不依赖数据根目录声明可解析性的解耦原则同口径。
    """
    import seedream_mcp.utils.io.io_path as io_path_module
    from seedream_mcp.tools.core._helpers import prevalidate_save_path, _resolve_base_dir

    monkeypatch.setenv("SEEDREAM_DATA_ROOT", str(tmp_path / "pics"))

    def _unresolvable(configured_dir: str) -> Path:
        raise OSError("simulated unresolvable path")

    monkeypatch.setattr(io_path_module, "resolve_cached_data_root", _unresolvable)

    absolute = tmp_path / "export" / "batch"
    prevalidate_save_path(str(absolute))
    assert _resolve_base_dir(str(absolute)) == absolute.resolve()
