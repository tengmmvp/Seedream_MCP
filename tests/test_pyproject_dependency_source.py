"""pyproject 依赖守护：运行时依赖单一来源锁定 PEP 735 迁移，构建后端钉版本与依赖锁联动。"""

from pathlib import Path

import tomllib


def test_runtime_dependencies_have_single_source() -> None:
    """运行时依赖只存在于 project.dependencies，hatch default env 不得另设依赖清单。"""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    project_deps = data["project"]["dependencies"]
    assert isinstance(project_deps, list)
    assert project_deps

    default_env = data.get("tool", {}).get("hatch", {}).get("envs", {}).get("default")
    if default_env is not None:
        assert "dependencies" not in default_env


def test_build_backend_hatchling_pinned_to_locked_version() -> None:
    """构建后端钉在 uv.lock 锁定的 hatchling 版本，升级后端须两处同步修改。

    build isolation 从 PyPI 实时解析构建后端且不参考依赖锁，未钉版本时
    Docker 镜像与 PyPI 产物由浮动解析的最新 hatchling 构建，供应链可复现性留缺口。
    """
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((repo_root / "uv.lock").read_text(encoding="utf-8"))
    locked = next(
        package["version"] for package in lock["package"] if package["name"] == "hatchling"
    )

    assert pyproject["build-system"]["requires"] == [f"hatchling=={locked}"]
