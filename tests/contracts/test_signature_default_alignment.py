"""签名默认与 config 代码默认的对齐守护。

自动保存族与日志级别默认收口于 definitions.defaults 命名常量，AutoSaveManager、
DownloadManager 与 setup_logging 的对应签名默认、config 默认同引单一来源，is
断言守护不再漂移。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.definitions.defaults import (
    DEFAULT_CLEANUP_DAYS,
    DEFAULT_DATE_FOLDER,
    DEFAULT_DOWNLOAD_MAX_RETRIES,
    DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    DEFAULT_FSYNC,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_CONCURRENT_DOWNLOADS,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MAX_TOTAL_BYTES,
)
from seedream_mcp.utils.core.logs import setup_logging
from seedream_mcp.utils.io.download import DownloadManager
from seedream_mcp.utils.io.save import AutoSaveManager

# (可调用, 参数名, 单一来源常量, config 字段名) 对齐表：config 侧增删改默认项时同步本表。
_SINGLE_SOURCE_DEFAULT_BINDINGS = (
    (
        AutoSaveManager.__init__,
        "download_timeout",
        DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
        "auto_save_download_timeout",
    ),
    (
        AutoSaveManager.__init__,
        "max_retries",
        DEFAULT_DOWNLOAD_MAX_RETRIES,
        "auto_save_max_retries",
    ),
    (
        AutoSaveManager.__init__,
        "max_file_size",
        DEFAULT_MAX_FILE_SIZE,
        "auto_save_max_file_size",
    ),
    (
        AutoSaveManager.__init__,
        "max_concurrent",
        DEFAULT_MAX_CONCURRENT_DOWNLOADS,
        "auto_save_max_concurrent",
    ),
    (AutoSaveManager.__init__, "date_folder", DEFAULT_DATE_FOLDER, "auto_save_date_folder"),
    (AutoSaveManager.__init__, "cleanup_days", DEFAULT_CLEANUP_DAYS, "auto_save_cleanup_days"),
    (
        AutoSaveManager.__init__,
        "max_total_bytes",
        DEFAULT_MAX_TOTAL_BYTES,
        "auto_save_max_total_bytes",
    ),
    (AutoSaveManager.__init__, "fsync", DEFAULT_FSYNC, "auto_save_fsync"),
    (
        DownloadManager.__init__,
        "timeout",
        DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
        "auto_save_download_timeout",
    ),
    (
        DownloadManager.__init__,
        "max_retries",
        DEFAULT_DOWNLOAD_MAX_RETRIES,
        "auto_save_max_retries",
    ),
    (DownloadManager.__init__, "max_file_size", DEFAULT_MAX_FILE_SIZE, "auto_save_max_file_size"),
    (setup_logging, "log_level", DEFAULT_LOG_LEVEL, "log_level"),
)


def _config_default(field_name: str) -> Any:
    """取 SeedreamConfig 字段的代码默认值，环境变量不参与。"""
    return SeedreamConfig.__dataclass_fields__[field_name].default


def _signature_default(func: Callable[..., Any], param_name: str) -> Any:
    """取可调用签名参数的默认值。"""
    return inspect.signature(func).parameters[param_name].default


@pytest.mark.parametrize(
    ("constructor", "param_name", "constant", "config_field"), _SINGLE_SOURCE_DEFAULT_BINDINGS
)
def test_signature_defaults_single_source(
    constructor: Callable[..., Any], param_name: str, constant: Any, config_field: str
) -> None:
    """签名默认与 config 默认同引 definitions.defaults 命名常量，单一来源不会漂移。"""
    assert (
        _signature_default(constructor, param_name) is constant
    ), f"{param_name} 构造默认未同引单一来源常量"
    assert _config_default(config_field) is constant, f"{config_field} 默认未同引单一来源常量"
