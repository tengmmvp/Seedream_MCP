"""pyproject 运行时依赖单一来源守护，锁定 PEP 735 dependency-groups 迁移。"""

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
