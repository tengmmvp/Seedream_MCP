"""pyproject 运行时依赖单一来源守护，锁定 PEP 735 dependency-groups 迁移。"""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


def test_runtime_dependencies_have_single_source() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    project_deps = data["project"]["dependencies"]
    assert isinstance(project_deps, list)
    assert project_deps

    default_env = data.get("tool", {}).get("hatch", {}).get("envs", {}).get("default")
    if default_env is not None:
        assert "dependencies" not in default_env
