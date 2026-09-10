"""config 错误契约测试：.env 读取失败包装、弃用模型提示派生、工作区根提供者回退
与下载停滞超时上界。

共同契约是配置侧的失败不向调用方裸抛 OSError、不残留硬编码清单、不失守派生
数值边界：.env 读取失败包装为含路径与原因的 SeedreamConfigError，经 cli_main 的
优雅错误路径输出；弃用模型错误提示从 DEPRECATED_MODEL_TOKENS 派生，新增下线模型
自动同步；工作区根提供者在配置构建抛 OSError 时回退环境变量，不让异常沿
provider 上抛；下载停滞超时超过 720 秒会使下载总预算反超 .part 清扫宽限，在
构造期拒绝。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedream_mcp import _config_sources as config_sources
from seedream_mcp import config as config_module
from seedream_mcp._config_sources import _read_env_values
from seedream_mcp.config import (
    DEPRECATED_MODEL_TOKENS,
    SeedreamConfig,
)
from seedream_mcp.utils.core.errors import SeedreamConfigError
from seedream_mcp.utils.io import io_path as io_path_module


def test_read_env_values_wraps_os_error_as_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.env 存在但读取失败抛 OSError 时包装为含路径的 SeedreamConfigError。

    未包装时 OSError 会沿配置构建上抛，越过 cli_main 只捕 SeedreamConfigError 的
    优雅错误路径，以裸 traceback 崩溃。
    """
    env_file = tmp_path / "locked.env"
    env_file.write_text("ARK_API_KEY=test_key\n", encoding="utf-8")

    def _raise_permission(path: object) -> dict[str, str]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(config_sources, "dotenv_values", _raise_permission)

    with pytest.raises(SeedreamConfigError) as excinfo:
        _read_env_values(str(env_file))

    assert str(env_file) in excinfo.value.message


def test_read_env_values_wraps_non_utf8_as_config_error(tmp_path: Path) -> None:
    """.env 含非 UTF-8 字节时包装为含编码提示的 SeedreamConfigError，不裸抛。

    中文 Windows 记事本默认 ANSI/GBK 保存含中文注释的 .env 是现实触发场景；
    UnicodeDecodeError 不在 cli_main 捕获范围内，未包装时以裸 traceback 崩溃。
    """
    env_file = tmp_path / "gbk.env"
    env_file.write_bytes(b"ARK_API_KEY=test_key\n# \xd6\xd0\xce\xc4 note\n")

    with pytest.raises(SeedreamConfigError, match="UTF-8") as excinfo:
        _read_env_values(str(env_file))

    assert str(env_file) in excinfo.value.message


def test_read_env_values_wraps_path_preparation_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """~ 开头 env_file 的 expanduser 抛 RuntimeError（HOME 剥离容器）时包装为配置错误。

    RuntimeError 不在 cli_main 捕获范围内，路径准备未包装时以裸 traceback 崩溃。
    """

    def _no_home(path: Path) -> Path:
        raise RuntimeError("Could not resolve home directory")

    monkeypatch.setattr(Path, "expanduser", _no_home)

    with pytest.raises(SeedreamConfigError, match="配置文件不可读") as excinfo:
        _read_env_values("~/config.env")

    assert "~" in excinfo.value.message


def test_read_env_values_wraps_deleted_cwd_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """工作目录被删导致定位 CWD .env 抛 FileNotFoundError 时包装为配置错误。

    未包装时 FileNotFoundError 沿配置构建上抛，越过 cli_main 的优雅错误路径。
    """

    def _deleted_cwd() -> str:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(config_module.os, "getcwd", _deleted_cwd)

    with pytest.raises(SeedreamConfigError, match="配置文件不可读"):
        _read_env_values(None)


def test_deprecated_model_error_message_derives_from_token_set() -> None:
    """弃用模型错误提示的下线清单从 DEPRECATED_MODEL_TOKENS 派生。

    硬编码清单在新增下线模型时提示不随之更新，用户会按过期提示继续选用已下线
    模型；派生断言保证集合中每个 token 都出现在提示文本中。
    """
    with pytest.raises(SeedreamConfigError) as excinfo:
        SeedreamConfig(api_key="test_key", model_id="doubao-seedream-3.0")

    message = excinfo.value.message
    for token in DEPRECATED_MODEL_TOKENS:
        assert token in message


def test_workspace_root_provider_falls_back_to_env_on_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """活动配置懒构建抛 OSError 时提供者回退环境变量，不让异常沿 provider 上抛。

    provider 被 io_path 的每次边界解析调用，异常上抛会把文件访问整体变为崩溃；
    与配置错误同口径回退 SEEDREAM_WORKSPACE_ROOT。
    """
    monkeypatch.setenv("SEEDREAM_WORKSPACE_ROOT", str(tmp_path))

    def _raise_os_error() -> SeedreamConfig:
        raise OSError("disk unavailable")

    monkeypatch.setattr(config_module, "get_active_config", _raise_os_error)

    provider = io_path_module._env_value_providers["SEEDREAM_WORKSPACE_ROOT"]
    assert provider() == str(tmp_path)


def test_auto_save_download_timeout_bound_aligns_with_part_sweep_grace() -> None:
    """下载停滞超时上界 720 秒：预算恰等于 .part 清扫宽限时通过，721 秒拒绝。

    下载总预算按停滞超时的 120 倍推导，超过 720 秒会反超 24 小时清扫宽限，
    在途慢下载的临时文件被并发清扫删除。
    """
    SeedreamConfig(api_key="test_key", auto_save_download_timeout=720)

    with pytest.raises(SeedreamConfigError, match="清扫宽限") as excinfo:
        SeedreamConfig(api_key="test_key", auto_save_download_timeout=721)

    assert "720" in excinfo.value.message
    assert "SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT" in excinfo.value.message
