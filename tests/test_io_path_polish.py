"""io_path 行为修复回归测试。

覆盖：相似路径建议对空目标名不误报、get_relative_path 对绝对路径回退分支不再重复
resolve、resolve_env_workspace_root 对 expanduser 后为绝对路径的配置值缓存 resolve
结果而相对形态每次现算、resolve_workspace_roots 不再对已 resolve 的根重复 resolve、
工作区根提供者的注册协议与未注册回退、find_images_in_directory 对 UNC 形式目录入参
在 resolve 前拒绝。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import seedream_mcp.utils.io.io_path as io_path_module


@pytest.fixture(autouse=True)
def _clear_env_root_cache() -> Iterator[None]:
    """每个用例前后清空回退根缓存，隔离模块级可变状态。"""
    io_path_module.clear_resolved_env_root_cache()
    yield
    io_path_module.clear_resolved_env_root_cache()


def test_suggest_similar_paths_empty_target_name_returns_no_suggestions(
    tmp_path: Path,
) -> None:
    """目标名为空串时不产生建议，避免空串子串匹配把任意图片误当相近项。

    ``/``、``.``、``..`` 等输入的 Path.name 为空串，旧实现的 ``"" in name`` 恒真，
    会把搜索目录前 5 张任意图片当相近建议返回。
    """
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    for bare_target in ("", ".", "..", "/"):
        assert io_path_module.suggest_similar_paths(bare_target, search_dirs=[str(tmp_path)]) == []

    # 对照：非空目标名仍按子串匹配给出建议
    assert io_path_module.suggest_similar_paths("a", search_dirs=[str(tmp_path)]) == [
        str(tmp_path / "a.png")
    ]


def test_get_relative_path_absolute_fallback_skips_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无法相对化且入参已是绝对路径时直接返回字符串，不再重复 resolve。

    浏览链路传入的路径均已 resolve，回退分支的重复 resolve 属纯冗余 stat。
    """

    def _explode_resolve(self: Path, strict: bool = False) -> Path:
        raise AssertionError("绝对路径回退分支不应再次 resolve")

    monkeypatch.setattr(Path, "resolve", _explode_resolve)
    base = tmp_path / "base"
    target = tmp_path / "x.png"

    assert io_path_module.get_relative_path(target, str(base)) == str(target)


def test_get_relative_path_relative_success_keeps_plain_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """可相对化的入参返回纯相对路径，全程不触发 resolve。"""

    def _explode_resolve(self: Path, strict: bool = False) -> Path:
        raise AssertionError("相对化成功分支不应调用 resolve")

    monkeypatch.setattr(Path, "resolve", _explode_resolve)

    assert io_path_module.get_relative_path("x.png", str(tmp_path)) == "x.png"


def test_resolve_env_workspace_root_caches_resolved_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一配置根重复解析命中缓存，不再触达文件系统；配置值变更产生新键重新解析。"""
    first_root = tmp_path / "first"
    first_root.mkdir()
    second_root = tmp_path / "second"
    second_root.mkdir()
    configured = {"value": str(first_root)}
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_configured_workspace_root", lambda: configured["value"])

    resolve_calls: list[str] = []
    original_resolve = Path.resolve

    def _counting_resolve(self: Path, strict: bool = False) -> Path:
        resolve_calls.append(str(self))
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", _counting_resolve)
    tracked = {str(first_root), str(second_root)}

    first = io_path_module.resolve_env_workspace_root()
    cached_again = io_path_module.resolve_env_workspace_root()
    assert first == cached_again == first_root
    # 同配置两次解析只触发一次该路径的 resolve；期间 cwd 兜底分支未走，无其他 resolve
    assert [p for p in resolve_calls if p in tracked] == [str(first_root)]

    configured["value"] = str(second_root)
    changed = io_path_module.resolve_env_workspace_root()
    assert changed == second_root
    assert [p for p in resolve_calls if p in tracked] == [str(first_root), str(second_root)]


def test_resolve_workspace_roots_skips_redundant_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会话 Roots 与环境变量回退根均已 resolve，归一化不得再次 resolve。"""

    def _explode_resolve(self: Path, strict: bool = False) -> Path:
        raise AssertionError("已 resolve 的工作区根不应再次 resolve")

    monkeypatch.setattr(Path, "resolve", _explode_resolve)
    sub_root = tmp_path / "sub"

    assert io_path_module.resolve_workspace_roots([tmp_path, str(sub_root)]) == [
        tmp_path,
        sub_root,
    ]


def test_resolve_env_workspace_root_relative_config_recomputes_after_cwd_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径形态的配置根不进缓存，进程 CWD 变更后按新 CWD 重新解析。

    缓存以配置原始字符串为键，相对路径的 resolve 结果随 CWD 变化；若照常缓存，
    CWD 变更后的访问会命中首个 CWD 下的陈旧 resolve。绝对路径形态仍走缓存，由
    test_resolve_env_workspace_root_caches_resolved_result 锁定。
    """
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_configured_workspace_root", lambda: "images")

    monkeypatch.chdir(first_cwd)
    assert io_path_module.resolve_env_workspace_root() == (first_cwd / "images").resolve()

    monkeypatch.chdir(second_cwd)
    assert io_path_module.resolve_env_workspace_root() == (second_cwd / "images").resolve()
    assert "images" not in io_path_module._RESOLVED_ENV_ROOT_CACHE


def test_resolve_env_workspace_root_tilde_form_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """~ 形态的配置根进入 resolve 缓存，重复访问不再触达文件系统。

    ~ 展开结果只依赖用户主目录环境，与进程 CWD 无关，按展开后的绝对性判定可缓存；
    旧实现以展开前的 is_absolute 排除 ~ 形态，stdio 客户端未声明 roots 时每次文件
    访问都在事件循环同步 expanduser+resolve。同配置两次解析只应触发一次 resolve。
    """
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_configured_workspace_root", lambda: "~")

    # 期望值在 resolve 被 monkeypatch 计数前捕获，避免断言自身的 resolve 混入计数
    expected = Path("~").expanduser().resolve()
    expanded_home = str(Path("~").expanduser())
    resolve_calls: list[str] = []
    original_resolve = Path.resolve

    def _counting_resolve(self: Path, strict: bool = False) -> Path:
        resolve_calls.append(str(self))
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", _counting_resolve)

    first = io_path_module.resolve_env_workspace_root()
    cached_again = io_path_module.resolve_env_workspace_root()

    assert first == cached_again == expected
    assert resolve_calls.count(expanded_home) == 1, "~ 形态配置根的 resolve 应只执行一次"
    assert "~" in io_path_module._RESOLVED_ENV_ROOT_CACHE


def test_workspace_root_provider_registration_drives_configured_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """注册的提供者是配置根的读取入口；提供者未注册时回退环境变量。"""
    target = tmp_path / "provided"
    target.mkdir()
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_workspace_root_provider", lambda: str(target))

    assert io_path_module._configured_workspace_root() == str(target)
    assert io_path_module.resolve_env_workspace_root() == target.resolve()

    monkeypatch.setattr(io_path_module, "_env_workspace_root_provider", None)
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    assert io_path_module._configured_workspace_root() == str(tmp_path)


def test_register_env_workspace_root_provider_replaces_previous() -> None:
    """register_env_workspace_root_provider 为覆盖式替换，后注册者生效。"""
    original = io_path_module._env_workspace_root_provider
    try:
        io_path_module.register_env_workspace_root_provider(lambda: "/registered/root")
        assert io_path_module._configured_workspace_root() == "/registered/root"

        io_path_module.register_env_workspace_root_provider(lambda: "/other/root")
        assert io_path_module._configured_workspace_root() == "/other/root"
    finally:
        io_path_module.register_env_workspace_root_provider(original)


def test_find_images_rejects_unc_directory_before_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNC 形式的目录入参在 resolve 前被拒：返回空列表且不触发任何路径解析。

    UNC 路径在 Windows 的 resolve 会触发 SMB 认证，浏览扫描须与 normalize_path
    等 resolve 站点同口径前置拦截，不因越界校验尚未介入就向远端泄露凭据。正斜杠
    与反斜杠两种 UNC 前缀形态均须覆盖。
    """

    def _explode_resolve(self: Path, strict: bool = False) -> Path:
        raise AssertionError("UNC 形式的目录入参不得进入 resolve")

    monkeypatch.setattr(Path, "resolve", _explode_resolve)

    assert io_path_module.find_images_in_directory("//server/share", recursive=False) == []
    assert io_path_module.find_images_in_directory("\\\\server\\share", recursive=True) == []
