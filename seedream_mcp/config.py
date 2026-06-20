"""
Seedream MCP工具配置管理模块
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from dotenv import dotenv_values

from .utils.errors import SeedreamConfigError, SeedreamValidationError
from .utils.validation import validate_size_for_model

# ====================
# 配置常量
# ====================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

MODEL_ALIASES: dict[str, str] = {
    "doubao-seedream-5.0": "doubao-seedream-5-0-260128",
    "doubao-seedream-5.0-lite": "doubao-seedream-5-0-260128",
    "doubao-seedream-4.5": "doubao-seedream-4-5-251128",
    "doubao-seedream-4.0": "doubao-seedream-4-0-250828",
}

DEPRECATED_MODEL_TOKENS: set[str] = {
    "doubao-seedream-3-0",
    "doubao-seedream-3.0",
    "doubao-seededit-3-0",
    "doubao-seededit-3.0",
}

# 记录进程启动后模块加载时的环境键，用于区分“系统环境变量”与“.env 注入变量”
_BASE_ENV_KEYS: set[str] = set(os.environ.keys())
_INJECTED_ENV_VALUES: dict[str, str] = {}

ENV_DEFAULTS: dict[str, Any] = {
    "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
    "SEEDREAM_MODEL_ID": "doubao-seedream-5-0-260128",
    "SEEDREAM_DEFAULT_SIZE": "2K",
    "SEEDREAM_DEFAULT_WATERMARK": "false",
    "SEEDREAM_TIMEOUT": "60",
    "SEEDREAM_API_TIMEOUT": "600",
    "SEEDREAM_MAX_RETRIES": "3",
    "LOG_LEVEL": "INFO",
    "LOG_FILE": "",
    "SEEDREAM_AUTO_SAVE_ENABLED": "true",
    "SEEDREAM_AUTO_SAVE_BASE_DIR": "",
    "SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT": "30",
    "SEEDREAM_AUTO_SAVE_MAX_RETRIES": "3",
    "SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE": str(50 * 1024 * 1024),
    "SEEDREAM_AUTO_SAVE_MAX_CONCURRENT": "5",
    "SEEDREAM_AUTO_SAVE_DATE_FOLDER": "true",
    "SEEDREAM_AUTO_SAVE_CLEANUP_DAYS": "30",
    "SEEDREAM_STREAM_BUFFER_MAX_SIZE": str(10 * 1024 * 1024),
    "SEEDREAM_STREAM_CHUNK_SIZE": str(1024 * 1024),
}


@dataclass
class SeedreamConfig:
    """
    Seedream MCP工具配置类

    封装 Seedream 服务的所有配置参数，包括 API 认证、模型设置、日志配置和自动保存功能。
    """

    # 必需配置
    api_key: str

    # 可选配置
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model_id: str = "doubao-seedream-5-0-260128"
    default_size: str = "2K"
    default_watermark: bool = False
    timeout: int = 60
    api_timeout: int = 600
    max_retries: int = 3

    # 日志配置
    log_level: str = "INFO"
    log_file: Optional[str] = None

    # 自动保存配置
    auto_save_enabled: bool = True
    auto_save_base_dir: Optional[str] = None
    auto_save_download_timeout: int = 30
    auto_save_max_retries: int = 3
    auto_save_max_file_size: int = 50 * 1024 * 1024
    auto_save_max_concurrent: int = 5
    auto_save_date_folder: bool = True
    auto_save_cleanup_days: int = 30

    # 流式处理配置
    stream_buffer_max_size: int = 10 * 1024 * 1024
    stream_chunk_size: int = 1024 * 1024

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """
        验证配置参数
        """
        if not self.api_key or self.api_key.strip() == "":
            raise SeedreamConfigError("API密钥不能为空")
        if self.api_key == "your_api_key_here":
            raise SeedreamConfigError("请设置有效的API密钥，不能使用默认占位符")

        if not self.base_url or not self.base_url.startswith(("http://", "https://")):
            raise SeedreamConfigError("base_url必须是有效的HTTP/HTTPS URL")

        if not self.model_id or self.model_id.strip() == "":
            raise SeedreamConfigError("model_id不能为空")
        self.model_id = normalize_model_selector(self.model_id)
        if any(token in self.model_id for token in DEPRECATED_MODEL_TOKENS):
            raise SeedreamConfigError(
                f"已不支持的模型: {self.model_id}（3.0/seededit-3.0 已下线），"
                "请使用 doubao-seedream-5.0/5.0-lite/4.5/4.0 或对应 Endpoint ID"
            )

        if not isinstance(self.default_size, str) or not self.default_size.strip():
            raise SeedreamConfigError("default_size不能为空")

        normalized_default_size = self.default_size.strip()
        try:
            self.default_size = validate_size_for_model(normalized_default_size, self.model_id)
        except SeedreamValidationError as exc:
            raise SeedreamConfigError(f"default_size无效: {exc.message}") from exc

        if self.timeout <= 0:
            raise SeedreamConfigError("timeout必须大于0")
        if self.api_timeout <= 0:
            raise SeedreamConfigError("api_timeout必须大于0")
        if self.max_retries < 1:
            raise SeedreamConfigError("max_retries不能小于1")

        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_log_levels:
            raise SeedreamConfigError(f"log_level必须是以下值之一: {valid_log_levels}")
        self.log_level = self.log_level.upper()

        if self.auto_save_download_timeout <= 0:
            raise SeedreamConfigError("auto_save_download_timeout必须大于0")
        if self.auto_save_max_retries < 0:
            raise SeedreamConfigError("auto_save_max_retries不能小于0")
        if self.auto_save_max_file_size <= 0:
            raise SeedreamConfigError("auto_save_max_file_size必须大于0")
        if self.auto_save_max_concurrent <= 0:
            raise SeedreamConfigError("auto_save_max_concurrent必须大于0")
        if self.auto_save_cleanup_days < 0:
            raise SeedreamConfigError("auto_save_cleanup_days不能小于0")

        if self.stream_buffer_max_size <= 0:
            raise SeedreamConfigError("stream_buffer_max_size必须大于0")
        if self.stream_chunk_size <= 0:
            raise SeedreamConfigError("stream_chunk_size必须大于0")
        if self.stream_chunk_size > self.stream_buffer_max_size:
            raise SeedreamConfigError("stream_chunk_size不能大于stream_buffer_max_size")

        if self.auto_save_base_dir:
            try:
                base_dir = Path(self.auto_save_base_dir)
                if base_dir.exists() and not base_dir.is_dir():
                    raise SeedreamConfigError(
                        f"auto_save_base_dir不是有效目录: {self.auto_save_base_dir}"
                    )
            except Exception as exc:
                raise SeedreamConfigError(
                    f"auto_save_base_dir路径无效: {self.auto_save_base_dir} -> {exc}"
                )

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "SeedreamConfig":
        """
        从环境变量创建配置实例
        """
        return build_config_from_sources(env_file=env_file)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_key": "***" if self.api_key else None,
            "base_url": self.base_url,
            "model_id": self.model_id,
            "default_size": self.default_size,
            "default_watermark": self.default_watermark,
            "timeout": self.timeout,
            "api_timeout": self.api_timeout,
            "max_retries": self.max_retries,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "auto_save_enabled": self.auto_save_enabled,
            "auto_save_base_dir": self.auto_save_base_dir,
            "auto_save_download_timeout": self.auto_save_download_timeout,
            "auto_save_max_retries": self.auto_save_max_retries,
            "auto_save_max_file_size": self.auto_save_max_file_size,
            "auto_save_max_concurrent": self.auto_save_max_concurrent,
            "auto_save_date_folder": self.auto_save_date_folder,
            "auto_save_cleanup_days": self.auto_save_cleanup_days,
            "stream_buffer_max_size": self.stream_buffer_max_size,
            "stream_chunk_size": self.stream_chunk_size,
        }

    def __repr__(self) -> str:
        return (
            f"SeedreamConfig(api_key='***', base_url='{self.base_url}', model_id='{self.model_id}')"
        )


def normalize_model_selector(value: object) -> str:
    """
    规范化模型选择器。

    支持将友好别名映射为真实 Model ID；未命中的值保持原样。
    """
    normalized = str(value).strip()
    return MODEL_ALIASES.get(normalized, normalized)


def parse_bool(value: object) -> bool:
    """
    解析布尔值字符串
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def parse_int(value: object) -> int:
    """
    解析整数字符串
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if value is None:
        raise SeedreamConfigError(f"无法解析整数值: {value}")

    normalized = str(value).strip()
    if not normalized:
        raise SeedreamConfigError(f"无法解析整数值: {value}")

    try:
        return int(normalized)
    except ValueError as exc:
        raise SeedreamConfigError(f"无法解析整数值: {value}") from exc


def _read_env_values(env_file: Optional[str]) -> dict[str, str]:
    """
    读取 .env 文件键值并注入进程环境变量
    """

    def _load_single_env_file(path: Path) -> dict[str, str]:
        values = dotenv_values(path)
        normalized = {k: str(v) for k, v in values.items() if v is not None}
        _inject_env_values(normalized)
        return normalized

    if env_file:
        env_path = Path(env_file).expanduser().resolve()
        if not env_path.is_file():
            raise SeedreamConfigError(f"配置文件不存在: {env_path}")
        return _load_single_env_file(env_path)

    merged_values: dict[str, str] = {}
    default_env_path = DEFAULT_ENV_FILE.expanduser().resolve()
    runtime_env_path = (Path.cwd() / ".env").expanduser().resolve()

    if default_env_path.is_file():
        merged_values.update(_load_single_env_file(default_env_path))

    if runtime_env_path.is_file():
        merged_values.update(_load_single_env_file(runtime_env_path))

    return merged_values


def _inject_env_values(values: Mapping[str, str]) -> None:
    """
    将 .env 键值注入进程环境变量

    规则：
    - 不覆盖系统环境变量（包括运行时动态设置）；
    - 允许覆盖此前由 .env 注入的值（支持重复构建切换 env_file）。
    """
    for key, value in values.items():
        current = os.environ.get(key)
        injected_value = _INJECTED_ENV_VALUES.get(key)

        # 若该键当前已存在且并非 .env 注入值，视为系统环境变量
        if current is not None and key not in _INJECTED_ENV_VALUES:
            _BASE_ENV_KEYS.add(key)
            continue

        # 若该键此前为注入值，但运行时被外部修改，则升级为“系统环境变量”
        if injected_value is not None and current is not None and current != injected_value:
            _BASE_ENV_KEYS.add(key)
            _INJECTED_ENV_VALUES.pop(key, None)
            continue

        os.environ[key] = value
        _INJECTED_ENV_VALUES[key] = value


def _get_system_env_value(env_key: str) -> Optional[str]:
    """
    获取系统环境变量值
    """
    env_value = os.getenv(env_key)
    if env_value is None:
        return None

    injected_value = _INJECTED_ENV_VALUES.get(env_key)
    if injected_value is not None and env_value == injected_value and env_key not in _BASE_ENV_KEYS:
        return None

    return env_value


def _pick_config_value(
    overrides: Mapping[str, object],
    key: str,
    env_key: str,
    env_values: Mapping[str, str],
    default_value: object,
) -> object:
    """
    按优先级选取配置值：overrides > 系统环境变量 > env 文件 > 默认值。
    """
    if key in overrides and overrides[key] is not None:
        return overrides[key]

    env_value = _get_system_env_value(env_key)
    if env_value is not None and env_value.strip():
        return env_value

    file_value = env_values.get(env_key)
    if file_value is not None and str(file_value).strip():
        return file_value

    return default_value


def build_config_from_sources(
    overrides: Optional[Mapping[str, object]] = None,
    env_file: Optional[str] = None,
) -> SeedreamConfig:
    """
    从统一来源构建配置对象

    Args:
        overrides: 调用方显式覆盖值（例如 CLI 参数）。
        env_file: 可选 .env 文件路径，未提供时按“项目根 `.env` -> 当前工作目录 `.env`”
            合并读取（cwd 覆盖）。
    """
    override_values = dict(overrides or {})
    env_values = _read_env_values(env_file)

    api_key = str(
        _pick_config_value(
            override_values,
            "api_key",
            "ARK_API_KEY",
            env_values,
            "",
        )
    ).strip()
    if not api_key:
        raise SeedreamConfigError("未找到ARK_API_KEY环境变量或配置文件值。")

    raw_model = str(
        _pick_config_value(
            override_values,
            "model",
            "SEEDREAM_MODEL_ID",
            env_values,
            ENV_DEFAULTS["SEEDREAM_MODEL_ID"],
        )
    )
    model_id = normalize_model_selector(raw_model)

    log_file_raw = _pick_config_value(
        override_values, "log_file", "LOG_FILE", env_values, ENV_DEFAULTS["LOG_FILE"]
    )
    auto_save_base_dir_raw = _pick_config_value(
        override_values,
        "auto_save_base_dir",
        "SEEDREAM_AUTO_SAVE_BASE_DIR",
        env_values,
        ENV_DEFAULTS["SEEDREAM_AUTO_SAVE_BASE_DIR"],
    )

    return SeedreamConfig(
        api_key=api_key,
        base_url=str(
            _pick_config_value(
                override_values,
                "base_url",
                "ARK_BASE_URL",
                env_values,
                ENV_DEFAULTS["ARK_BASE_URL"],
            )
        ),
        model_id=model_id,
        default_size=str(
            _pick_config_value(
                override_values,
                "default_size",
                "SEEDREAM_DEFAULT_SIZE",
                env_values,
                ENV_DEFAULTS["SEEDREAM_DEFAULT_SIZE"],
            )
        ),
        default_watermark=parse_bool(
            _pick_config_value(
                override_values,
                "watermark",
                "SEEDREAM_DEFAULT_WATERMARK",
                env_values,
                ENV_DEFAULTS["SEEDREAM_DEFAULT_WATERMARK"],
            )
        ),
        timeout=parse_int(
            _pick_config_value(
                override_values,
                "timeout",
                "SEEDREAM_TIMEOUT",
                env_values,
                ENV_DEFAULTS["SEEDREAM_TIMEOUT"],
            )
        ),
        api_timeout=parse_int(
            _pick_config_value(
                override_values,
                "api_timeout",
                "SEEDREAM_API_TIMEOUT",
                env_values,
                ENV_DEFAULTS["SEEDREAM_API_TIMEOUT"],
            )
        ),
        max_retries=parse_int(
            _pick_config_value(
                override_values,
                "max_retries",
                "SEEDREAM_MAX_RETRIES",
                env_values,
                ENV_DEFAULTS["SEEDREAM_MAX_RETRIES"],
            )
        ),
        log_level=str(
            _pick_config_value(
                override_values,
                "log_level",
                "LOG_LEVEL",
                env_values,
                ENV_DEFAULTS["LOG_LEVEL"],
            )
        ),
        log_file=(str(log_file_raw) or None),
        auto_save_enabled=parse_bool(
            _pick_config_value(
                override_values,
                "auto_save_enabled",
                "SEEDREAM_AUTO_SAVE_ENABLED",
                env_values,
                ENV_DEFAULTS["SEEDREAM_AUTO_SAVE_ENABLED"],
            )
        ),
        auto_save_base_dir=(str(auto_save_base_dir_raw) or None),
        auto_save_download_timeout=parse_int(
            _pick_config_value(
                override_values,
                "auto_save_download_timeout",
                "SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT",
                env_values,
                ENV_DEFAULTS["SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT"],
            )
        ),
        auto_save_max_retries=parse_int(
            _pick_config_value(
                override_values,
                "auto_save_max_retries",
                "SEEDREAM_AUTO_SAVE_MAX_RETRIES",
                env_values,
                ENV_DEFAULTS["SEEDREAM_AUTO_SAVE_MAX_RETRIES"],
            )
        ),
        auto_save_max_file_size=parse_int(
            _pick_config_value(
                override_values,
                "auto_save_max_file_size",
                "SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE",
                env_values,
                ENV_DEFAULTS["SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE"],
            )
        ),
        auto_save_max_concurrent=parse_int(
            _pick_config_value(
                override_values,
                "auto_save_max_concurrent",
                "SEEDREAM_AUTO_SAVE_MAX_CONCURRENT",
                env_values,
                ENV_DEFAULTS["SEEDREAM_AUTO_SAVE_MAX_CONCURRENT"],
            )
        ),
        auto_save_date_folder=parse_bool(
            _pick_config_value(
                override_values,
                "auto_save_date_folder",
                "SEEDREAM_AUTO_SAVE_DATE_FOLDER",
                env_values,
                ENV_DEFAULTS["SEEDREAM_AUTO_SAVE_DATE_FOLDER"],
            )
        ),
        auto_save_cleanup_days=parse_int(
            _pick_config_value(
                override_values,
                "auto_save_cleanup_days",
                "SEEDREAM_AUTO_SAVE_CLEANUP_DAYS",
                env_values,
                ENV_DEFAULTS["SEEDREAM_AUTO_SAVE_CLEANUP_DAYS"],
            )
        ),
        stream_buffer_max_size=parse_int(
            _pick_config_value(
                override_values,
                "stream_buffer_max_size",
                "SEEDREAM_STREAM_BUFFER_MAX_SIZE",
                env_values,
                ENV_DEFAULTS["SEEDREAM_STREAM_BUFFER_MAX_SIZE"],
            )
        ),
        stream_chunk_size=parse_int(
            _pick_config_value(
                override_values,
                "stream_chunk_size",
                "SEEDREAM_STREAM_CHUNK_SIZE",
                env_values,
                ENV_DEFAULTS["SEEDREAM_STREAM_CHUNK_SIZE"],
            )
        ),
    )


# 全局配置实例
_global_config: Optional[SeedreamConfig] = None


def get_global_config() -> SeedreamConfig:
    """
    获取全局配置实例。
    """
    global _global_config
    if _global_config is None:
        _global_config = SeedreamConfig.from_env()
    return _global_config


def set_config(config: SeedreamConfig) -> None:
    """
    设置全局配置实例。
    """
    global _global_config
    _global_config = config


def reload_config(env_file: Optional[str] = None) -> None:
    """
    重新加载全局配置。
    """
    global _global_config
    _global_config = SeedreamConfig.from_env(env_file)
