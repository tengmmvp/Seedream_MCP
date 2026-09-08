"""io_path 行为修复回归测试，覆盖相似路径建议、工作区根解析与提供者注册、
目录图片查找。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import seedream_mcp.utils.io.io_path as io_path_module
from _log_fakes import capture_loguru_messages


def test_suggest_similar_paths_empty_target_name_returns_no_suggestions(
    tmp_path: Path,
) -> None:
    """目标名为空串时不产生建议，避免空串子串匹配把任意图片误当相近项。

    ``/``、``.`` 等输入的 Path.name 为空串，旧实现的 ``"" in name`` 恒真。
    """
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n")

    for bare_target in ("", ".", "..", "/"):
        assert io_path_module.suggest_similar_paths(bare_target, search_dirs=[str(tmp_path)]) == []

    # 对照：非空目标名仍按子串匹配给出建议
    assert io_path_module.suggest_similar_paths("a", search_dirs=[str(tmp_path)]) == [
        str(tmp_path / "a.png")
    ]


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
    monkeypatch.setattr(
        io_path_module, "_configured_env_value", lambda env_var: configured["value"]
    )

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
    # 同配置两次解析仅触发一次该路径的 resolve，cwd 兜底分支未走
    assert [p for p in resolve_calls if p in tracked] == [str(first_root)]

    configured["value"] = str(second_root)
    changed = io_path_module.resolve_env_workspace_root()
    assert changed == second_root
    assert [p for p in resolve_calls if p in tracked] == [str(first_root), str(second_root)]


def test_resolve_env_workspace_root_relative_config_recomputes_after_cwd_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径形态的配置根不进缓存，进程 CWD 变更后按新 CWD 重新解析。

    相对路径的 resolve 结果随 CWD 变化，缓存会命中陈旧值；绝对路径形态仍走缓存，
    由 test_resolve_env_workspace_root_caches_resolved_result 锁定。
    """
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_configured_env_value", lambda env_var: "images")

    monkeypatch.chdir(first_cwd)
    assert io_path_module.resolve_env_workspace_root() == (first_cwd / "images").resolve()

    monkeypatch.chdir(second_cwd)
    assert io_path_module.resolve_env_workspace_root() == (second_cwd / "images").resolve()
    assert "images" not in io_path_module._RESOLVED_ENV_ROOT_CACHE


def test_resolve_env_workspace_root_tilde_form_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """~ 形态的配置根进入 resolve 缓存，重复访问不再触达文件系统。

    ~ 展开结果只依赖用户主目录而与 CWD 无关，按展开后绝对性判定可缓存；旧实现
    以展开前的 is_absolute 排除该形态。
    """
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_configured_env_value", lambda env_var: "~")

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
    """注册的提供者是声明值的读取入口；提供者未注册时回退同名环境变量。"""
    target = tmp_path / "provided"
    target.mkdir()
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setitem(
        io_path_module._env_value_providers, "SEEDREAM_WORKSPACE_ROOT", lambda: str(target)
    )

    assert io_path_module._configured_env_value("SEEDREAM_WORKSPACE_ROOT") == str(target)
    assert io_path_module.resolve_env_workspace_root() == target.resolve()

    monkeypatch.setattr(io_path_module, "_env_value_providers", {})
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))
    assert io_path_module._configured_env_value("SEEDREAM_WORKSPACE_ROOT") == str(tmp_path)


def test_register_env_workspace_root_provider_replaces_previous() -> None:
    """register_env_workspace_root_provider 为覆盖式替换，后注册者生效。"""
    original = dict(io_path_module._env_value_providers)
    try:
        io_path_module.register_env_workspace_root_provider(lambda: "/registered/root")
        assert io_path_module._configured_env_value("SEEDREAM_WORKSPACE_ROOT") == "/registered/root"

        io_path_module.register_env_workspace_root_provider(lambda: "/other/root")
        assert io_path_module._configured_env_value("SEEDREAM_WORKSPACE_ROOT") == "/other/root"
    finally:
        io_path_module._env_value_providers.clear()
        io_path_module._env_value_providers.update(original)


def test_fallback_all_unavailable_raises_config_error_with_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工作目录与主目录均不可用属部署环境缺陷，报携带配置指引的配置错误而非 RuntimeError 逃出。"""
    from seedream_mcp.utils.core.errors import SeedreamConfigError

    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_value_providers", {})
    monkeypatch.setattr(io_path_module, "_fallback_root", None)

    def _no_cwd() -> Path:
        raise FileNotFoundError("cwd deleted")

    def _no_home() -> Path:
        raise RuntimeError("Could not resolve home directory")

    monkeypatch.setattr(Path, "cwd", _no_cwd)
    monkeypatch.setattr(Path, "home", _no_home)

    with pytest.raises(SeedreamConfigError, match="SEEDREAM_WORKSPACE_ROOT"):
        io_path_module.resolve_env_workspace_root()


def test_fallback_prefers_writable_cwd_without_touching_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保底基准取可写的进程启动目录，主目录不参与求值，探测不留目录副作用。"""
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_value_providers", {})
    monkeypatch.setattr(io_path_module, "_fallback_root", None)
    monkeypatch.chdir(tmp_path)

    def _fail_home() -> Path:
        raise AssertionError("工作目录可写时保底不应触达主目录")

    monkeypatch.setattr(Path, "home", _fail_home)
    records: list[str] = []
    with capture_loguru_messages(records, level="INFO"):
        assert io_path_module.resolve_env_workspace_root() == tmp_path.resolve()

    # 临时探测文件用后即删，目录零残留
    assert list(tmp_path.iterdir()) == []
    assert any("工作位置保底为进程启动目录" in record for record in records)


def test_usable_cwd_root_rejects_unc_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """UNC 形态的进程启动目录不作为保底基准，不触发探测与路径解析。"""

    def _unc_cwd() -> Path:
        return Path("//server/share")

    def _explode_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        raise AssertionError("UNC 形态的启动目录不得进入写探测")

    monkeypatch.setattr(Path, "cwd", _unc_cwd)
    monkeypatch.setattr(io_path_module.tempfile, "mkstemp", _explode_mkstemp)

    assert io_path_module._usable_cwd_root() is None


def test_usable_cwd_root_returns_none_when_resolve_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写探测通过但 resolve 失败时启动目录不可用作保底基准，返回 None。"""

    def _tmp_cwd() -> Path:
        return tmp_path

    def _fail_resolve(self: Path) -> Path:
        del self
        raise OSError("resolve failed")

    monkeypatch.setattr(Path, "cwd", _tmp_cwd)
    monkeypatch.setattr(Path, "resolve", _fail_resolve)

    assert io_path_module._usable_cwd_root() is None
    # 临时探测文件用后即删，目录零残留
    assert list(tmp_path.iterdir()) == []


def test_fallback_caches_home_when_cwd_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工作目录写探测失败回退主目录，回退结果进程级缓存，重复求值不再触达 Path.home。"""
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_value_providers", {})
    monkeypatch.setattr(io_path_module, "_fallback_root", None)
    monkeypatch.chdir(tmp_path)

    def _denied(*args: object, **kwargs: object) -> tuple[int, str]:
        raise PermissionError("denied")

    monkeypatch.setattr(io_path_module.tempfile, "mkstemp", _denied)
    home = tmp_path / "home"
    home.mkdir()
    calls = {"count": 0}

    def _fake_home() -> Path:
        calls["count"] += 1
        return home

    monkeypatch.setattr(Path, "home", _fake_home)

    first = io_path_module.resolve_env_workspace_root()
    second = io_path_module.resolve_env_workspace_root()

    assert first == second == home.resolve()
    assert calls["count"] == 1


def test_suggest_similar_paths_reuses_scan_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重复的校验失败命中 io_scan 扫描缓存，同目录不重复全量扫描。

    suggest_similar_paths 显式注入 io_path.find_images_in_directory 为扫描器，
    计数补丁挂在 io_path 命名空间即可覆盖注入点。
    """
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = {"count": 0}
    original_scan = io_path_module.find_images_in_directory

    def _counting_scan(*args: object, **kwargs: object) -> list[Path]:
        calls["count"] += 1
        return original_scan(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(io_path_module, "find_images_in_directory", _counting_scan)

    first = io_path_module.suggest_similar_paths("a", search_dirs=[str(tmp_path)])
    second = io_path_module.suggest_similar_paths("a", search_dirs=[str(tmp_path)])

    assert first == second == [str((tmp_path / "a.png").resolve())]
    assert calls["count"] == 1


def test_find_images_rejects_unc_directory_before_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNC 形式的目录入参在 resolve 前被拒：记录告警、返回空列表且不触发路径解析。

    Windows 的 resolve 会触发 SMB 认证。前置拦截经告警文案锁定：find_images_in_directory
    的兜底 except 会吞掉 resolve 异常并同样返回空列表，仅断言空返回无法区分两条路径。
    """

    def _explode_resolve(self: Path, strict: bool = False) -> Path:
        raise AssertionError("UNC 形式的目录入参不得进入 resolve")

    monkeypatch.setattr(Path, "resolve", _explode_resolve)

    warnings: list[str] = []
    with capture_loguru_messages(warnings):
        assert io_path_module.find_images_in_directory("//server/share", recursive=False) == []
        assert io_path_module.find_images_in_directory("\\\\server\\share", recursive=True) == []

    assert any("拒绝 UNC 形式的目录扫描入参" in message for message in warnings)


def test_read_context_degrades_to_save_root_when_home_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保底链整体不可用且显式存储声明可用时，读权限退化为仅存储区不整体失败。

    显式 BASE_DIR 在场时基准链不参与存储区求值，工作区链的失败不应拖垮
    读取与浏览。
    """
    from seedream_mcp.utils.core.errors import SeedreamConfigError

    save_root = tmp_path / "pics"
    save_root.mkdir()
    monkeypatch.setenv("SEEDREAM_AUTO_SAVE_BASE_DIR", str(save_root))
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(io_path_module, "_env_value_providers", {})
    monkeypatch.setattr(io_path_module, "_fallback_root", None)

    def _no_cwd() -> Path:
        raise FileNotFoundError("cwd deleted")

    def _no_home() -> Path:
        raise RuntimeError("Could not resolve home directory")

    monkeypatch.setattr(Path, "cwd", _no_cwd)
    monkeypatch.setattr(Path, "home", _no_home)

    # 工作区链单点仍如实报配置指引
    with pytest.raises(SeedreamConfigError, match="SEEDREAM_WORKSPACE_ROOT"):
        io_path_module.get_workspace_roots()
    # 读权限退化为仅存储区，存储区派生自显式声明的 .seedream/images
    assert io_path_module.get_read_scope() == [(save_root / ".seedream" / "images").resolve()]


def test_resolve_save_root_wraps_runtime_error_with_configured_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式存储声明的 expanduser RuntimeError 也归一为配置错误，消息不嵌展开路径。

    用户消息只回显其自行配置的原始值，异常原文留在日志。
    """
    from seedream_mcp.utils.core.errors import SeedreamConfigError

    monkeypatch.setenv("SEEDREAM_AUTO_SAVE_BASE_DIR", "~/pics")

    def _runtime_error(configured_dir: str) -> Path:
        raise RuntimeError(r"Could not resolve home directory for ~/pics -> C:\Users\srv\pics")

    monkeypatch.setattr(io_path_module, "resolve_cached_save_base_dir", _runtime_error)

    with pytest.raises(SeedreamConfigError) as exc_info:
        io_path_module.resolve_save_root()

    message = exc_info.value.message
    assert message == "存储区配置无法解析: ~/pics"
    assert "C:" not in message and "Users" not in message


def test_clear_resolved_env_root_cache_resets_fallback_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保底基准缓存随配置路径缓存一并复位。

    隔离用例经 monkeypatch 改写保底解析后，复位协议使后续用例重新解析，
    不再读到先前用例缓存的陈旧基准。
    """
    monkeypatch.setattr(io_path_module, "_fallback_root", Path("D:/stale"))

    io_path_module.clear_resolved_env_root_cache()

    assert io_path_module._fallback_root is None


@pytest.mark.parametrize("unc_dir", [r"\\nas\pics", "//nas/pics"])
def test_suggest_similar_paths_skips_unc_search_dirs(tmp_path: Path, unc_dir: str) -> None:
    """UNC 形态的搜索目录在 resolve 前跳过，不触发 SMB 连接，返回空建议。"""
    (tmp_path / "a_portrait.png").write_bytes(b"x")

    assert io_path_module.suggest_similar_paths("portrait", search_dirs=[unc_dir]) == []
