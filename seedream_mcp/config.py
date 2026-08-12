"""
Seedream MCP工具配置管理模块
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, fields
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

# 自动保存单文件大小上限默认值
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024

MODEL_ALIASES: dict[str, str] = {
    "doubao-seedream-5.0-pro": "doubao-seedream-5-0-pro-260628",
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
    "SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE": str(DEFAULT_MAX_FILE_SIZE),
    "SEEDREAM_AUTO_SAVE_MAX_CONCURRENT": "5",
    "SEEDREAM_AUTO_SAVE_DATE_FOLDER": "true",
    "SEEDREAM_AUTO_SAVE_CLEANUP_DAYS": "30",
    "SEEDREAM_STREAM_BUFFER_MAX_SIZE": str(10 * 1024 * 1024),
    "SEEDREAM_STREAM_CHUNK_SIZE": str(1024 * 1024),
    "SEEDREAM_IMAGE_PREPARE_CONCURRENCY": "5",
    "SEEDREAM_PREPARE_CACHE_MAX": "32",
    "SEEDREAM_WORKSPACE_ROOT": "",
    "SEEDREAM_HTTP_AUTH_TOKEN": "",
}

# to_dict 输出时按字段名关键词脱敏，新增敏感字段无需手动登记
_SENSITIVE_CONFIG_KEYWORDS = ("key", "token", "secret", "password", "auth", "credential")


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
    auto_save_max_file_size: int = DEFAULT_MAX_FILE_SIZE
    auto_save_max_concurrent: int = 5
    auto_save_date_folder: bool = True
    auto_save_cleanup_days: int = 30

    # 流式处理配置
    stream_buffer_max_size: int = 10 * 1024 * 1024
    stream_chunk_size: int = 1024 * 1024

    # 客户端图像预处理并发上限
    image_prepare_concurrency: int = 5

    # 参考图预处理结果缓存上限（LRU 条目数）
    prepare_cache_max: int = 32

    # 工作区与传输配置
    workspace_root: Optional[str] = None
    http_auth_token: Optional[str] = None

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
                "请使用 doubao-seedream-5.0-pro/5.0/5.0-lite/4.5/4.0 或对应 Endpoint ID"
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

        if self.image_prepare_concurrency <= 0:
            raise SeedreamConfigError("image_prepare_concurrency必须大于0")

        if self.prepare_cache_max < 1:
            raise SeedreamConfigError("prepare_cache_max不能小于1")

        if self.auto_save_base_dir:
            try:
                base_dir = Path(self.auto_save_base_dir).expanduser()
                if base_dir.exists() and not base_dir.is_dir():
                    raise SeedreamConfigError(
                        f"auto_save_base_dir不是有效目录: {self.auto_save_base_dir}"
                    )
            except SeedreamConfigError:
                raise
            except Exception as exc:
                raise SeedreamConfigError(
                    f"auto_save_base_dir路径无效: {self.auto_save_base_dir} -> {exc}"
                )

        if self.workspace_root:
            try:
                root_path = Path(self.workspace_root).expanduser()
                if root_path.exists() and not root_path.is_dir():
                    raise SeedreamConfigError(f"workspace_root不是有效目录: {self.workspace_root}")
            except SeedreamConfigError:
                raise
            except Exception as exc:
                raise SeedreamConfigError(f"workspace_root路径无效: {self.workspace_root} -> {exc}")

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "SeedreamConfig":
        """
        从环境变量创建配置实例
        """
        return build_config_from_sources(env_file=env_file)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            name_lower = field.name.lower()
            if any(keyword in name_lower for keyword in _SENSITIVE_CONFIG_KEYWORDS):
                result[field.name] = "***" if value else None
            else:
                result[field.name] = value
        return result

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
    读取 .env 文件键值为字典，不写入进程环境变量。

    显式 env_file 优先；未提供时按项目根 .env 与当前工作目录 .env 合并读取，
    cwd 覆盖项目根。配置值经 _pick_config_value 按优先级解析，避免污染 os.environ。
    """

    def _load_single_env_file(path: Path) -> dict[str, str]:
        values = dotenv_values(path)
        return {k: str(v) for k, v in values.items() if v is not None}

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


def _pick_config_value(
    overrides: Mapping[str, object],
    key: str,
    env_key: str,
    env_values: Mapping[str, str],
    default_value: object,
) -> object:
    """
    按优先级选取配置值：overrides > 系统环境变量 > env 文件 > 默认值。

    系统环境变量直接读取 os.environ，因配置构建不再向其注入 .env 值，
    故 os.environ 仅含真实的系统环境变量。
    """
    if key in overrides and overrides[key] is not None:
        return overrides[key]

    env_value = os.getenv(env_key)
    if env_value is not None and env_value.strip():
        return env_value

    file_value = env_values.get(env_key)
    if file_value is not None and str(file_value).strip():
        return file_value

    return default_value


def _pick_str(
    overrides: Mapping[str, object], field: str, env_key: str, env_values: Mapping[str, str]
) -> str:
    return str(_pick_config_value(overrides, field, env_key, env_values, ENV_DEFAULTS[env_key]))


def _pick_optional_str(
    overrides: Mapping[str, object], field: str, env_key: str, env_values: Mapping[str, str]
) -> Optional[str]:
    raw = _pick_config_value(overrides, field, env_key, env_values, ENV_DEFAULTS[env_key])
    return str(raw) or None


def _pick_int(
    overrides: Mapping[str, object], field: str, env_key: str, env_values: Mapping[str, str]
) -> int:
    return parse_int(
        _pick_config_value(overrides, field, env_key, env_values, ENV_DEFAULTS[env_key])
    )


def _pick_bool(
    overrides: Mapping[str, object], field: str, env_key: str, env_values: Mapping[str, str]
) -> bool:
    return parse_bool(
        _pick_config_value(overrides, field, env_key, env_values, ENV_DEFAULTS[env_key])
    )


def build_config_from_sources(
    overrides: Optional[Mapping[str, object]] = None,
    env_file: Optional[str] = None,
) -> SeedreamConfig:
    """
    从统一来源构建配置对象，线程安全。

    通过 ``_config_build_lock`` 串行化构建，避免并发调用时 .env 注入与 os.environ
    读写之间产生竞态，该问题在 streamable-http 多请求场景下尤为关键。构建语义与
    单线程完全一致。

    Args:
        overrides: 调用方显式覆盖值（例如 CLI 参数）。
        env_file: 可选 .env 文件路径，未提供时按“项目根 `.env` -> 当前工作目录 `.env`”
            合并读取（cwd 覆盖）。
    """
    with _config_build_lock:
        return _build_config_from_sources_unlocked(overrides, env_file)


def _build_config_from_sources_unlocked(
    overrides: Optional[Mapping[str, object]] = None,
    env_file: Optional[str] = None,
) -> SeedreamConfig:
    """配置构建内部实现（无锁，由 :func:`build_config_from_sources` 持锁调用）。"""
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

    # override 键名 "model" 对应 SeedreamConfig.model_id 字段：CLI 暴露更简短的
    # "model"，此处取值后再经 normalize_model_selector 写入 model_id，与其余键名
    # （键名与字段同名）不同，属有意的命名间接映射。
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

    return SeedreamConfig(
        api_key=api_key,
        base_url=_pick_str(override_values, "base_url", "ARK_BASE_URL", env_values),
        model_id=model_id,
        default_size=_pick_str(
            override_values, "default_size", "SEEDREAM_DEFAULT_SIZE", env_values
        ),
        # override 键名 "watermark" 对应 SeedreamConfig.default_watermark 字段，
        # 与 model 同属 CLI 友好命名，其余键名均与字段同名。
        default_watermark=_pick_bool(
            override_values, "watermark", "SEEDREAM_DEFAULT_WATERMARK", env_values
        ),
        timeout=_pick_int(override_values, "timeout", "SEEDREAM_TIMEOUT", env_values),
        api_timeout=_pick_int(override_values, "api_timeout", "SEEDREAM_API_TIMEOUT", env_values),
        max_retries=_pick_int(override_values, "max_retries", "SEEDREAM_MAX_RETRIES", env_values),
        log_level=_pick_str(override_values, "log_level", "LOG_LEVEL", env_values),
        log_file=_pick_optional_str(override_values, "log_file", "LOG_FILE", env_values),
        auto_save_enabled=_pick_bool(
            override_values, "auto_save_enabled", "SEEDREAM_AUTO_SAVE_ENABLED", env_values
        ),
        auto_save_base_dir=_pick_optional_str(
            override_values, "auto_save_base_dir", "SEEDREAM_AUTO_SAVE_BASE_DIR", env_values
        ),
        auto_save_download_timeout=_pick_int(
            override_values,
            "auto_save_download_timeout",
            "SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT",
            env_values,
        ),
        auto_save_max_retries=_pick_int(
            override_values, "auto_save_max_retries", "SEEDREAM_AUTO_SAVE_MAX_RETRIES", env_values
        ),
        auto_save_max_file_size=_pick_int(
            override_values,
            "auto_save_max_file_size",
            "SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE",
            env_values,
        ),
        auto_save_max_concurrent=_pick_int(
            override_values,
            "auto_save_max_concurrent",
            "SEEDREAM_AUTO_SAVE_MAX_CONCURRENT",
            env_values,
        ),
        auto_save_date_folder=_pick_bool(
            override_values, "auto_save_date_folder", "SEEDREAM_AUTO_SAVE_DATE_FOLDER", env_values
        ),
        auto_save_cleanup_days=_pick_int(
            override_values, "auto_save_cleanup_days", "SEEDREAM_AUTO_SAVE_CLEANUP_DAYS", env_values
        ),
        stream_buffer_max_size=_pick_int(
            override_values, "stream_buffer_max_size", "SEEDREAM_STREAM_BUFFER_MAX_SIZE", env_values
        ),
        stream_chunk_size=_pick_int(
            override_values, "stream_chunk_size", "SEEDREAM_STREAM_CHUNK_SIZE", env_values
        ),
        image_prepare_concurrency=_pick_int(
            override_values,
            "image_prepare_concurrency",
            "SEEDREAM_IMAGE_PREPARE_CONCURRENCY",
            env_values,
        ),
        prepare_cache_max=_pick_int(
            override_values, "prepare_cache_max", "SEEDREAM_PREPARE_CACHE_MAX", env_values
        ),
        workspace_root=_pick_optional_str(
            override_values, "workspace_root", "SEEDREAM_WORKSPACE_ROOT", env_values
        ),
        http_auth_token=_pick_optional_str(
            override_values, "http_auth_token", "SEEDREAM_HTTP_AUTH_TOKEN", env_values
        ),
    )


# 配置构建串行化锁：保护 .env 读取与配置构建，避免并发构建竞态
_config_build_lock = threading.Lock()
# 全局配置实例的惰性初始化锁；与 _config_build_lock 分离，避免 get_global_config 持锁
# 调 from_env（内部复用 _config_build_lock）造成不可重入死锁
_global_config_lock = threading.Lock()

# 全局配置实例
_global_config: Optional[SeedreamConfig] = None


def get_global_config() -> SeedreamConfig:
    """
    获取全局配置实例。
    """
    global _global_config
    if _global_config is not None:
        return _global_config
    with _global_config_lock:
        if _global_config is None:
            _global_config = SeedreamConfig.from_env()
        return _global_config


def set_config(config: SeedreamConfig) -> None:
    """
    设置全局配置实例。
    """
    global _global_config
    with _global_config_lock:
        _global_config = config


def reload_config(env_file: Optional[str] = None) -> None:
    """
    重新加载全局配置。
    """
    global _global_config
    with _global_config_lock:
        _global_config = SeedreamConfig.from_env(env_file)
