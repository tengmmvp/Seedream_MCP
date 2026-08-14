"""配置构建器测试：多层优先级、别名解析、env 文件合并与不污染 os.environ。"""

import os
from pathlib import Path

import pytest

import seedream_mcp.config as config_module
from seedream_mcp.config import build_config_from_sources
from seedream_mcp.utils.core.errors import SeedreamConfigError


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


def test_build_config_does_not_inject_dotenv_to_os_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 配置构建不从 .env 向 os.environ 注入任何值，避免全局状态污染
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

    config = build_config_from_sources()

    assert config.api_key == "cwd_key"
    assert config.workspace_root == str(workspace_root)
    # .env 的值不应泄漏到 os.environ
    assert os.getenv("SEEDREAM_WORKSPACE_ROOT") is None


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


def test_build_config_does_not_write_back_to_os_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 配置构建读取系统 env 但不回写，.env 值不污染 os.environ
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_MODEL_ID=doubao-seedream-4.0\n")
    monkeypatch.setenv("ARK_API_KEY", "runtime_key")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.api_key == "runtime_key"
    # 系统未设置 SEEDREAM_MODEL_ID，.env 的该值不应写入 os.environ
    assert os.getenv("SEEDREAM_MODEL_ID") is None
    assert config.model_id == "doubao-seedream-4-0-250828"


def test_build_config_loads_http_auth_token_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEEDREAM_HTTP_AUTH_TOKEN 经 .env 配置链加载。"""
    monkeypatch.delenv("SEEDREAM_HTTP_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_ENV_FILE", tmp_path / "missing.env")
    _write_env_file(tmp_path / ".env", "ARK_API_KEY=k\nSEEDREAM_HTTP_AUTH_TOKEN=token123\n")
    monkeypatch.chdir(tmp_path)
    config = build_config_from_sources()
    assert config.http_auth_token == "token123"


def test_to_dict_masks_sensitive_fields() -> None:
    """to_dict 对 api_key 与 http_auth_token 脱敏。"""
    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="k", http_auth_token="secret")
    dumped = config.to_dict()
    assert dumped["api_key"] == "***"
    assert dumped["http_auth_token"] == "***"


def test_workspace_root_non_directory_rejected(tmp_path: Path) -> None:
    """workspace_root 指向文件时拒绝。"""
    from seedream_mcp.config import SeedreamConfig

    file_path = tmp_path / "notdir"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(SeedreamConfigError, match="workspace_root"):
        SeedreamConfig(api_key="k", workspace_root=str(file_path))


def test_build_config_none_overrides_fall_through_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """overrides 中值为 None 时不视为覆盖，穿透到 env/file/default。"""
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)
    monkeypatch.delenv("SEEDREAM_DEFAULT_WATERMARK", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")

    config = build_config_from_sources(
        overrides={"watermark": None, "model": None},
        env_file=str(env_file),
    )

    assert config.api_key == "file_key"
    # None override 不阻断后续链路：watermark/model 均回落到默认值
    assert config.default_watermark is False
    assert config.model_id == "doubao-seedream-5-0-260128"


def test_build_config_loads_image_prepare_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEEDREAM_IMAGE_PREPARE_CONCURRENCY 经 .env 加载到 image_prepare_concurrency 字段。"""
    monkeypatch.delenv("SEEDREAM_IMAGE_PREPARE_CONCURRENCY", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_IMAGE_PREPARE_CONCURRENCY=7\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.image_prepare_concurrency == 7


@pytest.mark.parametrize("invalid_value", ["0", "-1"])
def test_build_config_rejects_non_positive_image_prepare_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_value: str
) -> None:
    """image_prepare_concurrency 必须 > 0，否则抛 SeedreamConfigError。"""
    monkeypatch.delenv("SEEDREAM_IMAGE_PREPARE_CONCURRENCY", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        f"ARK_API_KEY=file_key\nSEEDREAM_IMAGE_PREPARE_CONCURRENCY={invalid_value}\n",
    )

    with pytest.raises(SeedreamConfigError, match="image_prepare_concurrency"):
        build_config_from_sources(env_file=str(env_file))


def test_build_config_loads_prepare_cache_max(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEEDREAM_PREPARE_CACHE_MAX 经 .env 加载到 prepare_cache_max 字段。"""
    monkeypatch.delenv("SEEDREAM_PREPARE_CACHE_MAX", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_PREPARE_CACHE_MAX=16\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.prepare_cache_max == 16


@pytest.mark.parametrize("invalid_value", ["0", "-1"])
def test_build_config_rejects_prepare_cache_max_below_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_value: str
) -> None:
    """prepare_cache_max 必须 >= 1，否则抛 SeedreamConfigError。"""
    monkeypatch.delenv("SEEDREAM_PREPARE_CACHE_MAX", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        f"ARK_API_KEY=file_key\nSEEDREAM_PREPARE_CACHE_MAX={invalid_value}\n",
    )

    with pytest.raises(SeedreamConfigError, match="prepare_cache_max"):
        build_config_from_sources(env_file=str(env_file))


def test_field_env_map_covers_all_optional_config_fields() -> None:
    """_FIELD_ENV_MAP 须覆盖除 api_key 外的全部配置字段。

    新增配置字段若遗漏登记，会在首次构建配置取默认值时抛 KeyError。本测试守护该同步点。
    """
    from dataclasses import fields as dataclass_fields

    optional_field_names = {
        f.name for f in dataclass_fields(config_module.SeedreamConfig) if f.name != "api_key"
    }
    assert set(config_module._FIELD_ENV_MAP) == optional_field_names


# ==================== __post_init__ 字段边界校验 ====================


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"prepare_cache_max_bytes": 0}, "prepare_cache_max_bytes"),
        ({"auto_save_max_file_size": 0}, "auto_save_max_file_size"),
        ({"auto_save_max_file_size": -1}, "auto_save_max_file_size"),
        ({"auto_save_download_timeout": 0}, "auto_save_download_timeout"),
        ({"auto_save_download_timeout": -5}, "auto_save_download_timeout"),
        ({"auto_save_max_concurrent": 0}, "auto_save_max_concurrent"),
        ({"auto_save_cleanup_days": -1}, "auto_save_cleanup_days"),
        ({"stream_buffer_max_size": 0}, "stream_buffer_max_size"),
        ({"stream_chunk_size": 0}, "stream_chunk_size"),
        ({"stream_chunk_size": -1}, "stream_chunk_size"),
    ],
)
def test_seedream_config_rejects_invalid_positive_or_non_negative_field(
    kwargs: dict, match: str
) -> None:
    """各数值字段越界时 __post_init__ 经 validate 抛 SeedreamConfigError。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match=match):
        SeedreamConfig(api_key="k", **kwargs)


def test_seedream_config_rejects_chunk_size_greater_than_buffer() -> None:
    """stream_chunk_size 大于 stream_buffer_max_size 须被拒绝。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(
        SeedreamConfigError, match="stream_chunk_size不能大于stream_buffer_max_size"
    ):
        SeedreamConfig(api_key="k", stream_chunk_size=2048, stream_buffer_max_size=1024)


def test_seedream_config_accepts_zero_cleanup_days() -> None:
    """cleanup_days 下界为 0（含），表示不清理；不得被当成负数拒绝。"""
    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="k", auto_save_cleanup_days=0)
    assert config.auto_save_cleanup_days == 0


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"api_key": "your_api_key_here"}, "占位符"),
        ({"base_url": "ftp://bad.example.com"}, "base_url"),
        ({"base_url": ""}, "base_url"),
        ({"model_id": ""}, "model_id"),
        ({"model_id": "   "}, "model_id"),
        ({"timeout": 0}, "timeout"),
        ({"timeout": -1}, "timeout"),
        ({"api_timeout": 0}, "api_timeout"),
        ({"api_timeout": -10}, "api_timeout"),
        ({"max_retries": 0}, "max_retries"),
        ({"max_retries": -1}, "max_retries"),
        ({"log_level": "VERBOSE"}, "log_level"),
        ({"auto_save_max_retries": -1}, "auto_save_max_retries"),
    ],
)
def test_seedream_config_rejects_invalid_validate_branches(kwargs: dict, match: str) -> None:
    """validate() 各拒绝分支覆蓋：占位符密钥、非法协议 base_url、空 model_id、
    非正 timeout/api_timeout、max_retries<1、非法 log_level、负 auto_save_max_retries。"""
    from seedream_mcp.config import SeedreamConfig

    # api_key 未在 kwargs 中时补充合法值，已在 kwargs 中时（占位符用例）不覆盖
    full_kwargs = {"api_key": "k", **kwargs} if "api_key" not in kwargs else kwargs
    with pytest.raises(SeedreamConfigError, match=match):
        SeedreamConfig(**full_kwargs)


# ==================== http:// base_url 默认拒绝与显式豁免 ====================


def test_seedream_config_rejects_http_base_url_by_default() -> None:
    """http:// 的 base_url 未豁免时默认拒绝构建，防止 API 密钥明文传输。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match="SEEDREAM_ALLOW_HTTP_BASE_URL"):
        SeedreamConfig(api_key="k", base_url="http://internal.example.com/api/v3")


def test_seedream_config_allows_http_base_url_with_explicit_exemption() -> None:
    """显式设置 allow_http_base_url 后接受 http:// base_url。"""
    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(
        api_key="k",
        base_url="http://internal.example.com/api/v3",
        allow_http_base_url=True,
    )

    assert config.base_url == "http://internal.example.com/api/v3"
    assert config.allow_http_base_url is True


def test_build_config_loads_allow_http_base_url_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEEDREAM_ALLOW_HTTP_BASE_URL 经 .env 豁免 http:// 的 ARK_BASE_URL。"""
    monkeypatch.delenv("ARK_BASE_URL", raising=False)
    monkeypatch.delenv("SEEDREAM_ALLOW_HTTP_BASE_URL", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        "ARK_API_KEY=file_key\n"
        "ARK_BASE_URL=http://internal.example.com/api/v3\n"
        "SEEDREAM_ALLOW_HTTP_BASE_URL=true\n",
    )

    config = build_config_from_sources(env_file=str(env_file))

    assert config.allow_http_base_url is True
    assert config.base_url == "http://internal.example.com/api/v3"
