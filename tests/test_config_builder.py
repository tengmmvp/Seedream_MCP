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
    """运行时覆盖优先于系统环境变量与 env 文件。"""
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
    """系统环境变量优先于 env 文件。"""
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")
    monkeypatch.setenv("ARK_API_KEY", "env_key")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.api_key == "env_key"


def test_build_config_resolves_seedream_50_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env 文件中的 5.0 别名解析为完整模型 ID。"""
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_MODEL_ID=doubao-seedream-5.0\n")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)

    config = build_config_from_sources(env_file=str(env_file))

    assert config.model_id == "doubao-seedream-5-0-260128"


def test_build_config_uses_seedream_50_as_default_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未指定模型时默认取 5.0 并解析为完整模型 ID。"""
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDREAM_MODEL_ID", raising=False)

    config = build_config_from_sources(env_file=str(env_file))

    assert config.model_id == "doubao-seedream-5-0-260128"


def test_build_config_raises_when_explicit_env_file_missing(tmp_path: Path) -> None:
    """显式指定的 env 文件缺失时抛 SeedreamConfigError。"""
    missing_env = tmp_path / "missing.env"

    with pytest.raises(SeedreamConfigError, match="配置文件不存在"):
        build_config_from_sources(env_file=str(missing_env))


def test_build_config_reads_cwd_env_when_env_file_not_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未显式指定 env 文件时读取工作目录下的 .env。"""
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
    """工作目录无 .env 时回退默认 env 文件。"""
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
    """cwd .env 缺键时由默认 env 文件补齐，合并后生效。"""
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
    """.env 值进入配置对象但不注入 os.environ，避免全局状态污染。"""
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
    """显式 env 文件的构建结果不受此前其他 env 文件影响。"""
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
    """构建读取系统 env 但不回写，.env 值不污染 os.environ。"""
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


# ==================== SEEDREAM_HTTP_ALLOWED_HOSTS Host 允许列表 ====================


def test_build_config_loads_http_allowed_hosts_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEEDREAM_HTTP_ALLOWED_HOSTS 按逗号拆分为条目元组，host:port 与 :* 通配原样保留。"""
    monkeypatch.delenv("SEEDREAM_HTTP_ALLOWED_HOSTS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        "ARK_API_KEY=file_key\n" "SEEDREAM_HTTP_ALLOWED_HOSTS=mcp.example.com,mcp.example.com:*\n",
    )

    config = build_config_from_sources(env_file=str(env_file))

    assert config.http_allowed_hosts == ("mcp.example.com", "mcp.example.com:*")


def test_build_config_http_allowed_hosts_strips_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """条目前后空白被去除，空条目被丢弃。"""
    monkeypatch.delenv("SEEDREAM_HTTP_ALLOWED_HOSTS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        "ARK_API_KEY=file_key\n"
        "SEEDREAM_HTTP_ALLOWED_HOSTS= mcp.example.com , , api.example.com:8443 \n",
    )

    config = build_config_from_sources(env_file=str(env_file))

    assert config.http_allowed_hosts == ("mcp.example.com", "api.example.com:8443")


@pytest.mark.parametrize("raw_value", ["", "   ", ","])
def test_build_config_blank_http_allowed_hosts_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    """空串、纯空白与全空条目均归 None，等价于未配置。"""
    monkeypatch.delenv("SEEDREAM_HTTP_ALLOWED_HOSTS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, f"ARK_API_KEY=file_key\nSEEDREAM_HTTP_ALLOWED_HOSTS={raw_value}\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.http_allowed_hosts is None


def test_build_config_http_allowed_hosts_defaults_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置时 http_allowed_hosts 为 None，非回环绑定整体关闭 SDK Host 校验。"""
    monkeypatch.delenv("SEEDREAM_HTTP_ALLOWED_HOSTS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.http_allowed_hosts is None


def test_build_config_accepts_all_valid_http_allowed_hosts_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """host、host:port、host:* 与方括号 IPv6 形态均通过构建期条目校验。"""
    monkeypatch.delenv("SEEDREAM_HTTP_ALLOWED_HOSTS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        "ARK_API_KEY=file_key\n"
        "SEEDREAM_HTTP_ALLOWED_HOSTS="
        "api.example.com,api.example.com:8443,api.example.com:*,[2001:db8::1]:*\n",
    )

    config = build_config_from_sources(env_file=str(env_file))

    assert config.http_allowed_hosts == (
        "api.example.com",
        "api.example.com:8443",
        "api.example.com:*",
        "[2001:db8::1]:*",
    )


@pytest.mark.parametrize(
    "raw_value,match",
    [
        ("https://api.example.com", "scheme 或斜杠"),
        ("api.example.com/path", "scheme 或斜杠"),
        ("*:8080", "host、host:port、host:\\*"),
        ("*.example.com", "host、host:port、host:\\*"),
        ("*", "host、host:port、host:\\*"),
        ("api.example.com:https", "host、host:port、host:\\*"),
        ("api.example.com:", "host、host:port、host:\\*"),
        ("[2001:db8::1", "host、host:port、host:\\*"),
        ("api.example.com:0", "host、host:port、host:\\*"),
        ("api.example.com:99999", "host、host:port、host:\\*"),
        ("api.example.com:８０", "host、host:port、host:\\*"),
        ("api.example.com.", "host、host:port、host:\\*"),
        (".api.example.com", "host、host:port、host:\\*"),
    ],
)
def test_build_config_rejects_malformed_http_allowed_hosts_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_value: str, match: str
) -> None:
    """含 scheme/斜杠、非尾部通配、端口非数字或超范围、首尾点号的条目构建期拒绝。"""
    monkeypatch.delenv("SEEDREAM_HTTP_ALLOWED_HOSTS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, f"ARK_API_KEY=file_key\nSEEDREAM_HTTP_ALLOWED_HOSTS={raw_value}\n")

    with pytest.raises(SeedreamConfigError, match=match) as excinfo:
        build_config_from_sources(env_file=str(env_file))

    assert raw_value in excinfo.value.message
    assert "环境变量 SEEDREAM_HTTP_ALLOWED_HOSTS" in excinfo.value.message


def test_build_config_http_allowed_hosts_wildcard_without_bare_host_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仅列端口通配未列裸 host 时构建成功并告警，配套裸 host 后不再告警。

    SDK 的 host:* 通配仅匹配带端口的 Host 头，无端口 Host 会被 421 拒绝，
    构建期告警提示补配裸 host；告警不构成拒绝。
    """
    from loguru import logger as loguru_logger

    monkeypatch.delenv("SEEDREAM_HTTP_ALLOWED_HOSTS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file, "ARK_API_KEY=file_key\nSEEDREAM_HTTP_ALLOWED_HOSTS=api.example.com:*\n"
    )

    records: list[object] = []
    handler_id = loguru_logger.add(lambda message: records.append(message), level="WARNING")
    try:
        config = build_config_from_sources(env_file=str(env_file))
    finally:
        loguru_logger.remove(handler_id)

    assert config.http_allowed_hosts == ("api.example.com:*",)
    assert any("api.example.com" in str(record) for record in records)

    paired_env_file = tmp_path / "paired.env"
    _write_env_file(
        paired_env_file,
        "ARK_API_KEY=file_key\nSEEDREAM_HTTP_ALLOWED_HOSTS=api.example.com,api.example.com:*\n",
    )
    quiet_records: list[object] = []
    handler_id = loguru_logger.add(lambda message: quiet_records.append(message), level="WARNING")
    try:
        paired_config = build_config_from_sources(env_file=str(paired_env_file))
    finally:
        loguru_logger.remove(handler_id)

    assert paired_config.http_allowed_hosts == ("api.example.com", "api.example.com:*")
    assert not any("http_allowed_hosts" in str(record) for record in quiet_records)


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


def test_build_config_missing_picker_registration_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env metadata 字段漏登记 _FIELD_PICKERS 取值表时，构建期以 KeyError 暴露。

    取值表驱动构建依赖该 fail-loud 语义，防止新增字段被静默跳过而取默认值。
    """
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")
    monkeypatch.delitem(config_module._FIELD_PICKERS, "timeout")

    with pytest.raises(KeyError):
        build_config_from_sources(env_file=str(env_file))


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
    """cleanup_days 下界含 0，表示不清理，不得被当成负数拒绝。"""
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
    """validate() 各拒绝分支经构造期校验拒绝非法配置。

    覆盖占位符密钥、非法协议 base_url、空 model_id、非正 timeout/api_timeout、
    max_retries<1、非法 log_level、负 auto_save_max_retries。
    """
    from seedream_mcp.config import SeedreamConfig

    # api_key 未在 kwargs 中时补充合法值，占位符用例已在 kwargs 中时不覆盖
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


# ==================== base_url scheme 大小写不敏感 ====================


def test_seedream_config_accepts_uppercase_https_scheme() -> None:
    """RFC 3986 scheme 大小写不敏感：HTTPS:// 大写形态应被接受。"""
    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="k", base_url="HTTPS://ark.example.com/api/v3")

    assert config.base_url == "HTTPS://ark.example.com/api/v3"


def test_seedream_config_uppercase_http_scheme_requires_exemption() -> None:
    """HTTP:// 大写形态按明文端点处理：未豁免时拒绝，豁免后接受。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match="SEEDREAM_ALLOW_HTTP_BASE_URL"):
        SeedreamConfig(api_key="k", base_url="HTTP://internal.example.com/api/v3")

    config = SeedreamConfig(
        api_key="k",
        base_url="HTTP://internal.example.com/api/v3",
        allow_http_base_url=True,
    )
    assert config.base_url == "HTTP://internal.example.com/api/v3"


# ==================== base_url netloc 主机名校验 ====================


@pytest.mark.parametrize("invalid_base_url", ["https://", "https:foo", "https:///path", "http://"])
def test_seedream_config_rejects_base_url_without_netloc(invalid_base_url: str) -> None:
    """scheme 合法但 netloc 缺失的 base_url 在构造期拒绝。

    此类畸形 URL 若放行到运行时，会在 httpx 拼请求时抛 UnsupportedProtocol 落入
    网络错误重试，错误归约档错误地变为 network_error 而非 config_error。
    """
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match="主机名"):
        SeedreamConfig(api_key="k", base_url=invalid_base_url, allow_http_base_url=True)


def test_seedream_config_rejects_whitespace_only_netloc() -> None:
    """netloc 仅含空白的 base_url 同样视为缺主机名，strip 后判定。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match="环境变量 ARK_BASE_URL"):
        SeedreamConfig(api_key="k", base_url="https:// ")


@pytest.mark.parametrize(
    "valid_base_url",
    [
        "https://ark.cn-beijing.volces.com/api/v3",
        "https://ark.example.com:8443/api/v3",
        "HTTPS://ark.example.com/api/v3",
        "http://internal.example.com",
    ],
)
def test_seedream_config_accepts_base_url_with_netloc(valid_base_url: str) -> None:
    """带主机名的合法 URL 不被 netloc 校验误伤，含带端口、路径与大写 scheme 形态。"""
    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="k", base_url=valid_base_url, allow_http_base_url=True)

    assert config.base_url == valid_base_url


def test_build_config_rejects_base_url_without_netloc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build_config_from_sources 构建路径同样经 validate 拒绝缺主机名的 ARK_BASE_URL。"""
    monkeypatch.delenv("ARK_BASE_URL", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nARK_BASE_URL=https://\n")

    with pytest.raises(SeedreamConfigError, match="环境变量 ARK_BASE_URL"):
        build_config_from_sources(env_file=str(env_file))


# ==================== 校验错误消息附带环境变量名 ====================


@pytest.mark.parametrize(
    "kwargs,env_name",
    [
        ({"timeout": 0}, "SEEDREAM_TIMEOUT"),
        ({"api_timeout": -1}, "SEEDREAM_API_TIMEOUT"),
        ({"max_retries": 0}, "SEEDREAM_MAX_RETRIES"),
        ({"log_level": "VERBOSE"}, "LOG_LEVEL"),
        ({"auto_save_download_timeout": 0}, "SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT"),
        ({"stream_chunk_size": 0}, "SEEDREAM_STREAM_CHUNK_SIZE"),
        ({"prepare_cache_max": 0}, "SEEDREAM_PREPARE_CACHE_MAX"),
        ({"http_max_body_size": 1024}, "SEEDREAM_HTTP_MAX_BODY_SIZE"),
        ({"base_url": "ftp://bad.example.com"}, "ARK_BASE_URL"),
        ({"base_url": "https://"}, "ARK_BASE_URL"),
        ({"base_url": "https:foo"}, "ARK_BASE_URL"),
        ({"base_url": "https:///path"}, "ARK_BASE_URL"),
        ({"model_id": "doubao-seededit-3-0-250828"}, "SEEDREAM_MODEL_ID"),
    ],
)
def test_seedream_config_validation_errors_mention_env_var(kwargs: dict, env_name: str) -> None:
    """校验失败消息附带对应环境变量名，用户可直接定位配置来源。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match=f"环境变量 {env_name}"):
        SeedreamConfig(api_key="k", **kwargs)


def test_seedream_config_empty_api_key_error_mentions_env_var() -> None:
    """api_key 为空的校验消息附带 ARK_API_KEY，虽该字段无 env metadata。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match="环境变量 ARK_API_KEY"):
        SeedreamConfig(api_key=" ")


def test_seedream_config_chunk_size_error_mentions_both_env_vars() -> None:
    """跨字段约束的校验消息同时附带两个字段的环境变量名。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(
        SeedreamConfigError,
        match="环境变量 SEEDREAM_STREAM_CHUNK_SIZE/SEEDREAM_STREAM_BUFFER_MAX_SIZE",
    ):
        SeedreamConfig(api_key="k", stream_chunk_size=2048, stream_buffer_max_size=1024)


def test_build_config_unparsable_int_error_mentions_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整数字段值解析失败的消息附带环境变量名，用户可直接定位写坏的变量。"""
    monkeypatch.delenv("SEEDREAM_TIMEOUT", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_TIMEOUT=abc\n")

    with pytest.raises(SeedreamConfigError, match="环境变量 SEEDREAM_TIMEOUT") as excinfo:
        build_config_from_sources(env_file=str(env_file))

    assert "无法解析整数值" in excinfo.value.message


# ==================== response_body_limit 响应体读取上限 ====================


def test_build_config_loads_response_body_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEEDREAM_RESPONSE_BODY_LIMIT 显式设置时作为字节值直接生效。"""
    monkeypatch.delenv("SEEDREAM_RESPONSE_BODY_LIMIT", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_RESPONSE_BODY_LIMIT=1048576\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.response_body_limit == 1048576


def test_build_config_response_body_limit_defaults_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置时 response_body_limit 为 None。

    由 client 按 auto_save_max_file_size × 20 推导。
    """
    monkeypatch.delenv("SEEDREAM_RESPONSE_BODY_LIMIT", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.response_body_limit is None


@pytest.mark.parametrize("invalid_value", ["0", "-1"])
def test_build_config_rejects_non_positive_response_body_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_value: str
) -> None:
    """response_body_limit 必须 > 0，否则抛 SeedreamConfigError。"""
    monkeypatch.delenv("SEEDREAM_RESPONSE_BODY_LIMIT", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        f"ARK_API_KEY=file_key\nSEEDREAM_RESPONSE_BODY_LIMIT={invalid_value}\n",
    )

    with pytest.raises(SeedreamConfigError, match="response_body_limit"):
        build_config_from_sources(env_file=str(env_file))


# ==================== auto_save_fsync 落盘刷盘开关 ====================


def test_build_config_loads_auto_save_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEEDREAM_AUTO_SAVE_FSYNC=true 经 .env 加载为 True。"""
    monkeypatch.delenv("SEEDREAM_AUTO_SAVE_FSYNC", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_AUTO_SAVE_FSYNC=true\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.auto_save_fsync is True


def test_build_config_auto_save_fsync_defaults_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置时 auto_save_fsync 默认 False，落盘不做同步刷盘。"""
    monkeypatch.delenv("SEEDREAM_AUTO_SAVE_FSYNC", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.auto_save_fsync is False


# ==================== auto_save_max_total_bytes 0 哨兵表示不限制 ====================


def test_build_config_zero_max_total_bytes_means_unlimited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES=0 归一为 None，显式关闭总量上限。"""
    monkeypatch.delenv("SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\nSEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES=0\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.auto_save_max_total_bytes is None


def test_build_config_max_total_bytes_defaults_to_10gb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置时 auto_save_max_total_bytes 取默认 10GB 上限。"""
    monkeypatch.delenv("SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.auto_save_max_total_bytes == 10 * 1024 * 1024 * 1024


@pytest.mark.parametrize("invalid_value", ["-1", "-10737418240"])
def test_build_config_rejects_negative_max_total_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_value: str
) -> None:
    """负数不被 0 哨兵路径吸收，经 validate 下界校验拒绝。"""
    monkeypatch.delenv("SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        f"ARK_API_KEY=file_key\nSEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES={invalid_value}\n",
    )

    with pytest.raises(SeedreamConfigError, match="auto_save_max_total_bytes"):
        build_config_from_sources(env_file=str(env_file))


def test_seedream_config_rejects_programmatic_zero_max_total_bytes() -> None:
    """程序构造直接传 0 不经 env 哨兵归一，仍由 validate 下界校验拒绝。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match="auto_save_max_total_bytes"):
        SeedreamConfig(api_key="k", auto_save_max_total_bytes=0)


# ==================== SEEDREAM_REQUEST_STATE_KEYS requestState 密钥环 ====================


def test_build_config_loads_single_request_state_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单键十六进制串解码为 32 字节密钥并以字节元组持有。"""
    monkeypatch.delenv("SEEDREAM_REQUEST_STATE_KEYS", raising=False)
    key_hex = "ab" * 32
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, f"ARK_API_KEY=file_key\nSEEDREAM_REQUEST_STATE_KEYS={key_hex}\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.request_state_secret_keys == (bytes.fromhex(key_hex),)


def test_build_config_loads_rotation_request_state_key_ring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多键逗号分隔按序解码，首键密封全键解封的轮换顺序原样保留。"""
    monkeypatch.delenv("SEEDREAM_REQUEST_STATE_KEYS", raising=False)
    first_hex, second_hex = "ab" * 32, "cd" * 32
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        f"ARK_API_KEY=file_key\nSEEDREAM_REQUEST_STATE_KEYS={first_hex},{second_hex}\n",
    )

    config = build_config_from_sources(env_file=str(env_file))

    assert config.request_state_secret_keys == (bytes.fromhex(first_hex), bytes.fromhex(second_hex))


def test_build_config_request_state_keys_strip_blank_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """条目前后空白被去除，空条目被丢弃。"""
    monkeypatch.delenv("SEEDREAM_REQUEST_STATE_KEYS", raising=False)
    first_hex, second_hex = "ab" * 32, "cd" * 32
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file,
        f"ARK_API_KEY=file_key\nSEEDREAM_REQUEST_STATE_KEYS= {first_hex} , , {second_hex} \n",
    )

    config = build_config_from_sources(env_file=str(env_file))

    assert config.request_state_secret_keys == (bytes.fromhex(first_hex), bytes.fromhex(second_hex))


@pytest.mark.parametrize("raw_value", ["", "   ", ","])
def test_build_config_blank_request_state_keys_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    """空串、纯空白与全空条目均归 None，等价于未启用密钥环。"""
    monkeypatch.delenv("SEEDREAM_REQUEST_STATE_KEYS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, f"ARK_API_KEY=file_key\nSEEDREAM_REQUEST_STATE_KEYS={raw_value}\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.request_state_secret_keys is None


def test_build_config_request_state_keys_default_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置时不启用密钥环，保持 SDK 默认进程临时密钥。"""
    monkeypatch.delenv("SEEDREAM_REQUEST_STATE_KEYS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, "ARK_API_KEY=file_key\n")

    config = build_config_from_sources(env_file=str(env_file))

    assert config.request_state_secret_keys is None


@pytest.mark.parametrize("bad_hex", ["zz" * 32, "0x" + "ab" * 32, "abc"])
def test_build_config_rejects_non_hex_request_state_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_hex: str
) -> None:
    """非十六进制条目构建期拒绝，消息含格式要求与生成命令且不回显密钥内容。"""
    monkeypatch.delenv("SEEDREAM_REQUEST_STATE_KEYS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, f"ARK_API_KEY=file_key\nSEEDREAM_REQUEST_STATE_KEYS={bad_hex}\n")

    with pytest.raises(SeedreamConfigError, match="十六进制") as excinfo:
        build_config_from_sources(env_file=str(env_file))

    assert "secrets.token_hex(32)" in excinfo.value.message
    assert bad_hex not in excinfo.value.message
    assert "环境变量 SEEDREAM_REQUEST_STATE_KEYS" in excinfo.value.message


@pytest.mark.parametrize("short_hex", ["ab" * 31, "ab" * 16])
def test_build_config_rejects_short_request_state_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_hex: str
) -> None:
    """解码后不足 32 字节的密钥构建期拒绝，消息含生成命令提示。"""
    monkeypatch.delenv("SEEDREAM_REQUEST_STATE_KEYS", raising=False)
    env_file = tmp_path / "config.env"
    _write_env_file(env_file, f"ARK_API_KEY=file_key\nSEEDREAM_REQUEST_STATE_KEYS={short_hex}\n")

    with pytest.raises(SeedreamConfigError, match="32 字节") as excinfo:
        build_config_from_sources(env_file=str(env_file))

    assert "secrets.token_hex(32)" in excinfo.value.message


def test_build_config_rejects_duplicate_request_state_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重复密钥在轮换环内只允许登记一次，重复登记构建期拒绝。"""
    monkeypatch.delenv("SEEDREAM_REQUEST_STATE_KEYS", raising=False)
    key_hex = "ab" * 32
    env_file = tmp_path / "config.env"
    _write_env_file(
        env_file, f"ARK_API_KEY=file_key\nSEEDREAM_REQUEST_STATE_KEYS={key_hex},{key_hex}\n"
    )

    with pytest.raises(SeedreamConfigError, match="重复"):
        build_config_from_sources(env_file=str(env_file))


def test_seedream_config_accepts_programmatic_request_state_key_ring() -> None:
    """程序构造直接传解码后的字节密钥，经 validate 下界校验后原样持有。"""
    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="k", request_state_secret_keys=(b"\x01" * 32,))

    assert config.request_state_secret_keys == (b"\x01" * 32,)


def test_seedream_config_rejects_programmatic_short_request_state_key() -> None:
    """程序构造的短密钥不经 env 解码路径，仍由 validate 下界校验拒绝。"""
    from seedream_mcp.config import SeedreamConfig

    with pytest.raises(SeedreamConfigError, match="32 字节"):
        SeedreamConfig(api_key="k", request_state_secret_keys=(b"\x01" * 31,))


def test_to_dict_masks_request_state_secret_keys() -> None:
    """to_dict 对 request_state_secret_keys 脱敏，密钥字节不进入导出字典。"""
    from seedream_mcp.config import SeedreamConfig

    config = SeedreamConfig(api_key="k", request_state_secret_keys=(b"\x01" * 32,))
    dumped = config.to_dict()

    assert dumped["request_state_secret_keys"] == "***"
