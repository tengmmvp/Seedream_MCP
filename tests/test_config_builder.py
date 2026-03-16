import os
from pathlib import Path

import pytest

import seedream_mcp.config as config_module
from seedream_mcp.config import build_config_from_sources
from seedream_mcp.utils.errors import SeedreamConfigError


def _write_env_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_build_config_priority_prefers_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        "\n".join(
            [
                "ARK_API_KEY=file_key",
                "SEEDREAM_MODEL_ID=doubao-seedream-4.5",
            ]
        ),
    )
    monkeypatch.setenv("ARK_API_KEY", "env_key")

    config = build_config_from_sources(
        overrides={"api_key": "override_key", "model": "doubao-seedream-4.0"},
        env_file=str(env_file),
    )

    assert config.api_key == "override_key"
    assert config.model_id == "doubao-seedream-4-0-250828"


def test_build_config_priority_prefers_system_env_over_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")
    monkeypatch.setenv("ARK_API_KEY", "env_key")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.api_key == "env_key"


def test_build_config_resolves_seedream_50_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_MODEL_ID=doubao-seedream-5.0\n")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)

    config = build_config_from_sources(env_file=str(env_file))

    assert config.model_id == "doubao-seedream-5-0-260128"


def test_build_config_uses_seedream_50_as_default_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)

    config = build_config_from_sources(env_file=str(env_file))

    assert config.model_id == "doubao-seedream-5-0-260128"


def test_build_config_raises_when_explicit_env_file_missing(tmp_path: Path) -> None:
    missing_env = tmp_path / "missing.env"

    with pytest.raises(SeedreamConfigError, match="配置文件不存在"):
        build_config_from_sources(env_file=str(missing_env))


def test_build_config_reads_cwd_env_when_env_file_not_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_ENV_FILE", tmp_path / "missing.env")

    _write_env_file(
        tmp_path / ".env", "ARK_API_KEY=cwd_key\nSEEDREAM_MODEL_ID=doubao-seedream-4.0\n"
    )

    config = build_config_from_sources()

    assert config.api_key == "cwd_key"
    assert config.model_id == "doubao-seedream-4-0-250828"


def test_build_config_falls_back_to_default_env_when_cwd_env_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.chdir(runtime_dir)
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    default_env = tmp_path / "default.env"
    _write_env_file(default_env, "ARK_API_KEY=default_key\n")
    monkeypatch.setattr(config_module, "DEFAULT_ENV_FILE", default_env)

    config = build_config_from_sources()

    assert config.api_key == "default_key"


def test_build_config_merges_default_and_cwd_env_when_cwd_missing_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.chdir(runtime_dir)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)

    default_env = tmp_path / "default.env"
    _write_env_file(default_env, "ARK_API_KEY=default_key\nSEEDREAM_MODEL_ID=doubao-seedream-4.5\n")
    monkeypatch.setattr(config_module, "DEFAULT_ENV_FILE", default_env)

    _write_env_file(runtime_dir / ".env", "SEEDREAM_MODEL_ID=doubao-seedream-4.0\n")

    config = build_config_from_sources()

    assert config.api_key == "default_key"
    assert config.model_id == "doubao-seedream-4-0-250828"


def test_build_config_injects_non_config_env_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    workspace_root = runtime_dir / "workspace"
    workspace_root.mkdir()

    monkeypatch.chdir(runtime_dir)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_ENV_FILE", tmp_path / "missing.env")

    _write_env_file(
        runtime_dir / ".env",
        "\n".join(
            [
                "ARK_API_KEY=cwd_key",
                f"SEEDREAM_WORKSPACE_ROOT={workspace_root}",
            ]
        ),
    )

    _ = build_config_from_sources()

    assert os.getenv("SEEDREAM_WORKSPACE_ROOT") == str(workspace_root)


def test_build_config_explicit_env_file_is_not_polluted_by_previous_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)

    env_file_a = tmp_path / "a.env"
    env_file_b = tmp_path / "b.env"
    _write_env_file(env_file_a, "ARK_API_KEY=key_a\nSEEDREAM_MODEL_ID=doubao-seedream-4.5\n")
    _write_env_file(env_file_b, "ARK_API_KEY=key_b\nSEEDREAM_MODEL_ID=doubao-seedream-4.0\n")

    config_a = build_config_from_sources(env_file=str(env_file_a))
    config_b = build_config_from_sources(env_file=str(env_file_b))

    assert config_a.api_key == "key_a"
    assert config_b.api_key == "key_b"
    assert config_b.model_id == "doubao-seedream-4-0-250828"


def test_build_config_preserves_runtime_env_set_after_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 模拟模块导入后再 setenv：_BASE_ENV_KEYS 未包含该键
    monkeypatch.setattr(config_module, "_BASE_ENV_KEYS", set())
    monkeypatch.setattr(config_module, "_INJECTED_ENV_VALUES", {})

    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")
    monkeypatch.setenv("ARK_API_KEY", "runtime_key")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.api_key == "runtime_key"
    assert os.getenv("ARK_API_KEY") == "runtime_key"
    assert "ARK_API_KEY" not in config_module._INJECTED_ENV_VALUES
