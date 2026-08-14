"""
Seedream MCP 工具配置管理模块。

定义 SeedreamConfig 配置数据类与多层配置加载机制，优先级为运行时覆盖 >
系统环境变量 > .env 文件 > 代码默认值。配置构建不向 os.environ 注入 .env 值，
仅写入配置对象，避免全局状态污染。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

from .utils.core.errors import SeedreamConfigError, SeedreamValidationError
from .utils.core.formats import DEFAULT_MAX_FILE_SIZE
from .utils.model.model_capabilities import MODEL_ALIASES, DEPRECATED_MODEL_TOKENS
from .utils.core.validators import (
    FALSE_BOOL_STRINGS,
    TRUE_BOOL_STRINGS,
    validate_size_for_model,
)

# ====================
# 配置常量
# ====================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

# MODEL_ALIASES 与 DEPRECATED_MODEL_TOKENS 属模型知识，统一定义于 model_capabilities，
# 此处经 import 暴露供 normalize_model_selector 与 validate 使用，外部仍可从 config 导入。

# 合法日志级别，供 config 校验与 CLI choices 共用此单一来源
LEGAL_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# to_dict 输出时按字段名关键词脱敏，新增敏感字段无需手动登记
_SENSITIVE_CONFIG_KEYWORDS = ("key", "token", "secret", "password", "auth", "credential")

# dataclass 字段 metadata 中登记环境变量名的键，字段定义据此声明对应环境变量名
_ENV_METADATA_KEY = "env"


def _env_field(default: Any, env_name: str) -> Any:
    """为 dataclass 字段绑定默认值与环境变量名元数据。

    env_name 经字段 metadata 声明，使字段定义成为字段默认值与环境变量名映射的单一数据源，
    _FIELD_ENV_MAP 与 ENV_DEFAULTS 据此反射派生。
    """
    return field(default=default, metadata={_ENV_METADATA_KEY: env_name})


@dataclass(frozen=True)
class SeedreamConfig:
    """
    Seedream MCP 工具配置类

    封装 Seedream 服务的所有配置参数，包括 API 认证、模型设置、日志配置和自动保存功能。
    """

    # 必需配置
    api_key: str

    # 可选配置
    base_url: str = _env_field("https://ark.cn-beijing.volces.com/api/v3", "ARK_BASE_URL")
    model_id: str = _env_field("doubao-seedream-5-0-260128", "SEEDREAM_MODEL_ID")
    default_size: str = _env_field("2K", "SEEDREAM_DEFAULT_SIZE")
    default_watermark: bool = _env_field(False, "SEEDREAM_DEFAULT_WATERMARK")
    timeout: int = _env_field(60, "SEEDREAM_TIMEOUT")
    api_timeout: int = _env_field(600, "SEEDREAM_API_TIMEOUT")
    max_retries: int = _env_field(3, "SEEDREAM_MAX_RETRIES")

    # 日志配置
    log_level: str = _env_field("INFO", "LOG_LEVEL")
    log_file: str | None = _env_field(None, "LOG_FILE")

    # 自动保存配置
    auto_save_enabled: bool = _env_field(True, "SEEDREAM_AUTO_SAVE_ENABLED")
    auto_save_base_dir: str | None = _env_field(None, "SEEDREAM_AUTO_SAVE_BASE_DIR")
    auto_save_download_timeout: int = _env_field(30, "SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT")
    auto_save_max_retries: int = _env_field(3, "SEEDREAM_AUTO_SAVE_MAX_RETRIES")
    auto_save_max_file_size: int = _env_field(
        DEFAULT_MAX_FILE_SIZE, "SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE"
    )
    auto_save_max_concurrent: int = _env_field(5, "SEEDREAM_AUTO_SAVE_MAX_CONCURRENT")
    auto_save_date_folder: bool = _env_field(True, "SEEDREAM_AUTO_SAVE_DATE_FOLDER")
    auto_save_cleanup_days: int = _env_field(30, "SEEDREAM_AUTO_SAVE_CLEANUP_DAYS")
    auto_save_max_total_bytes: int | None = _env_field(
        10 * 1024 * 1024 * 1024, "SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES"
    )

    # 流式处理配置
    stream_buffer_max_size: int = _env_field(10 * 1024 * 1024, "SEEDREAM_STREAM_BUFFER_MAX_SIZE")
    stream_chunk_size: int = _env_field(1024 * 1024, "SEEDREAM_STREAM_CHUNK_SIZE")

    # 客户端图像预处理并发上限
    image_prepare_concurrency: int = _env_field(5, "SEEDREAM_IMAGE_PREPARE_CONCURRENCY")

    # 参考图预处理结果 LRU 缓存的上限条目数
    prepare_cache_max: int = _env_field(32, "SEEDREAM_PREPARE_CACHE_MAX")

    # 参考图预处理结果 LRU 缓存的累计字节上限，防止大图缓存累积撑爆内存
    prepare_cache_max_bytes: int = _env_field(256 * 1024 * 1024, "SEEDREAM_PREPARE_CACHE_MAX_BYTES")

    # 工作区与传输配置
    workspace_root: str | None = _env_field(None, "SEEDREAM_WORKSPACE_ROOT")
    http_auth_token: str | None = _env_field(None, "SEEDREAM_HTTP_AUTH_TOKEN")
    http_max_body_size: int = _env_field(100 * 1024 * 1024, "SEEDREAM_HTTP_MAX_BODY_SIZE")

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """校验配置参数合法性与业务约束，并在通过时做规范化写回。

        规范化包括展开模型别名为 model_id、按模型能力校验并标准化 default_size、
        将 log_level 统一为大写。任一校验失败抛出 SeedreamConfigError。
        """
        if not self.api_key or self.api_key.strip() == "":
            raise SeedreamConfigError("API密钥不能为空")
        if self.api_key == "your_api_key_here":
            raise SeedreamConfigError("请设置有效的API密钥，不能使用默认占位符")

        if not self.base_url or not self.base_url.startswith(("http://", "https://")):
            raise SeedreamConfigError("base_url必须是有效的HTTP/HTTPS URL")
        if self.base_url.startswith("http://"):
            # http 明文会使 API 密钥在网络上裸传，记录告警提示仅限自建可信内网端点使用
            from .utils.core.logs import get_logger

            get_logger(__name__).warning(
                "ARK_BASE_URL 使用 http://，API 密钥将在网络上明文传输，" "仅自建可信内网端点时使用"
            )

        if not self.model_id or self.model_id.strip() == "":
            raise SeedreamConfigError("model_id不能为空")
        object.__setattr__(self, "model_id", normalize_model_selector(self.model_id))
        if any(token in self.model_id for token in DEPRECATED_MODEL_TOKENS):
            raise SeedreamConfigError(
                f"已不支持的模型: {self.model_id}（3.0/seededit-3.0 已下线），"
                "请使用 doubao-seedream-5.0-pro/5.0/5.0-lite/4.5/4.0 或对应 Endpoint ID"
            )

        if not isinstance(self.default_size, str) or not self.default_size.strip():
            raise SeedreamConfigError("default_size不能为空")

        normalized_default_size = self.default_size.strip()
        try:
            object.__setattr__(
                self,
                "default_size",
                validate_size_for_model(normalized_default_size, self.model_id),
            )
        except SeedreamValidationError as exc:
            raise SeedreamConfigError(f"default_size无效: {exc.message}") from exc

        if self.timeout <= 0:
            raise SeedreamConfigError("timeout必须大于0")
        if self.api_timeout <= 0:
            raise SeedreamConfigError("api_timeout必须大于0")
        if self.max_retries < 1:
            raise SeedreamConfigError("max_retries不能小于1")

        if self.log_level.upper() not in LEGAL_LOG_LEVELS:
            raise SeedreamConfigError(f"log_level必须是以下值之一: {list(LEGAL_LOG_LEVELS)}")
        object.__setattr__(self, "log_level", self.log_level.upper())

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
        if self.auto_save_max_total_bytes is not None and self.auto_save_max_total_bytes <= 0:
            raise SeedreamConfigError("auto_save_max_total_bytes必须大于0")

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
        if self.prepare_cache_max_bytes < 1:
            raise SeedreamConfigError("prepare_cache_max_bytes不能小于1")

        if self.auto_save_base_dir:
            self._validate_dir_field(self.auto_save_base_dir, "auto_save_base_dir")

        if self.workspace_root:
            self._validate_dir_field(self.workspace_root, "workspace_root")

        if self.http_max_body_size < 1024 * 1024:
            raise SeedreamConfigError("http_max_body_size 不能低于 1MB（1048576 字节）")

    def _validate_dir_field(self, value: str, field_name: str) -> None:
        """校验给定路径指向有效目录，存在但非目录时抛 SeedreamConfigError。

        仅校验已存在路径的目录性，不要求目录预先存在，
        使未创建的目录也能通过校验以便后续按需创建。
        """
        try:
            dir_path = Path(value).expanduser()
            if dir_path.exists() and not dir_path.is_dir():
                raise SeedreamConfigError(f"{field_name}不是有效目录: {value}")
        except SeedreamConfigError:
            raise
        except Exception as exc:
            raise SeedreamConfigError(f"{field_name}路径无效: {value} -> {exc}") from exc

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "SeedreamConfig":
        """从环境变量与 .env 文件构建配置实例，构建过程线程安全。"""
        return build_config_from_sources(env_file=env_file)

    def to_dict(self) -> dict[str, Any]:
        """导出为字典，名称命中敏感关键词的字段以 "***" 脱敏。"""
        result: dict[str, Any] = {}
        for config_field in fields(self):
            value = getattr(self, config_field.name)
            name_lower = config_field.name.lower()
            if any(keyword in name_lower for keyword in _SENSITIVE_CONFIG_KEYWORDS):
                result[config_field.name] = "***" if value is not None else None
            else:
                result[config_field.name] = value
        return result

    def __repr__(self) -> str:
        return (
            f"SeedreamConfig(api_key='***', base_url='{self.base_url}', model_id='{self.model_id}')"
        )


# dataclass 字段名到环境变量名的映射，从 SeedreamConfig 各字段的 env 元数据反射派生，
# 使字段定义成为单一数据源。新增字段仅需在其 _env_field 声明中登记环境变量名。
_FIELD_ENV_MAP: dict[str, str] = {
    f.name: f.metadata[_ENV_METADATA_KEY]
    for f in fields(SeedreamConfig)
    if _ENV_METADATA_KEY in f.metadata
}


def _field_default_str(field_name: str) -> str:
    """反射 SeedreamConfig 字段默认值并转为环境变量字符串默认值。

    bool 转为 true/false，None 转为空串，其余取 str，与历史手写 ENV_DEFAULTS 语义一致。
    字段无默认值时返回空串，仅 api_key 属此情形且它不进入 ENV_DEFAULTS。
    """
    for f in fields(SeedreamConfig):
        if f.name == field_name:
            default = f.default
            if isinstance(default, bool):
                return "true" if default else "false"
            if default is None:
                return ""
            return str(default)
    return ""


# 配置项的字符串默认值，以环境变量名为键，从 dataclass 字段默认值派生为单一数据源，
# 供 _pick_* 系列辅助回退取值
ENV_DEFAULTS: dict[str, str] = {
    env_key: _field_default_str(field_name) for field_name, env_key in _FIELD_ENV_MAP.items()
}


def normalize_model_selector(value: object) -> str:
    """
    规范化模型选择器。

    支持将友好别名映射为真实 Model ID；未命中的值保持原样。
    """
    normalized = str(value).strip()
    return MODEL_ALIASES.get(normalized, normalized)


def parse_bool(value: object) -> bool:
    """将值解析为布尔。

    接受 true/yes/on/1 为真、false/no/off/0 为假；其余值抛出 SeedreamConfigError，
    避免 enabled 这类拼写错误被静默当作 False，导致功能未生效却无报错。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if normalized in TRUE_BOOL_STRINGS:
        return True
    if normalized in FALSE_BOOL_STRINGS:
        return False
    raise SeedreamConfigError(f"无法解析为布尔值(期望 true/false/yes/no/on/off/1/0): {value!r}")


def parse_int(value: object) -> int:
    """将值解析为整数，空值或无法解析时抛出 SeedreamConfigError。"""
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


def _read_env_values(env_file: str | None) -> dict[str, str]:
    """
    读取 .env 文件键值为字典，不写入进程环境变量。

    显式传入 env_file 时只读取该文件，不再合并项目根或当前工作目录的 .env；
    未提供时按项目根 .env 与当前工作目录 .env 合并读取，cwd 覆盖项目根。
    配置值经 _pick_config_value 按优先级解析，避免污染 os.environ。
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


def _value_is_set(value: object) -> bool:
    """判定一个取值是否应视为已设置。

    字符串需 strip 后非空，与系统环境变量、.env 文件值的空值判定保持一致；
    其余类型仅排除 None，使布尔、整数等显式覆盖仍被采纳。
    """
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _pick_config_value(
    overrides: Mapping[str, object],
    key: str,
    env_key: str,
    env_values: Mapping[str, str],
    default_value: object,
) -> object:
    """
    按优先级选取配置值：overrides > 系统环境变量 > env 文件 > 默认值。

    三层来源统一采用 _value_is_set 做空值判定，空白字符串在任一层都视为未设置而穿透到
    下一层，避免空白 override 被原样采用而空白 env/file 被当作未设置的语义分裂。系统
    环境变量直接读取 os.environ，因配置构建不再向其注入 .env 值，故 os.environ 仅含
    真实的系统环境变量。
    """
    if key in overrides and _value_is_set(overrides[key]):
        return overrides[key]

    env_value = os.getenv(env_key)
    if _value_is_set(env_value):
        return env_value

    file_value = env_values.get(env_key)
    if _value_is_set(file_value):
        return file_value

    return default_value


# 类型化配置取值辅助：统一经 _pick_config_value 按优先级取值后再做类型转换，
# 每个辅助对应一种目标类型，供 _build_config_from_sources_unlocked 调用。
def _pick_str(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> str:
    return str(
        _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key])
    ).strip()


def _pick_optional_str(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> str | None:
    raw = _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key])
    return str(raw).strip() or None


def _pick_int(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> int:
    return parse_int(
        _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key])
    )


def _pick_optional_int(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> int | None:
    raw = _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key])
    if raw is None or not str(raw).strip():
        return None
    return parse_int(raw)


def _pick_bool(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> bool:
    return parse_bool(
        _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key])
    )


def build_config_from_sources(
    overrides: Mapping[str, object] | None = None,
    env_file: str | None = None,
) -> SeedreamConfig:
    """
    从统一来源构建配置对象，线程安全。

    通过 ``_config_build_lock`` 串行化构建，避免并发调用时 .env 注入与 os.environ
    读写之间产生竞态，该问题在 streamable-http 多请求场景下尤为关键。构建语义与
    单线程完全一致。

    Args:
        overrides: 调用方显式覆盖值，CLI 参数为典型来源。
        env_file: 可选 .env 文件路径，未提供时按“项目根 `.env` -> 当前工作目录 `.env`”
            合并读取，当前工作目录的值覆盖项目根。
    """
    with _config_build_lock:
        return _build_config_from_sources_unlocked(overrides, env_file)


def _build_config_from_sources_unlocked(
    overrides: Mapping[str, object] | None = None,
    env_file: str | None = None,
) -> SeedreamConfig:
    """配置构建内部实现，自身不加锁；由 :func:`build_config_from_sources` 持锁调用。"""
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

    # override 键名 "model" 对应 SeedreamConfig.model_id 字段，属有意的命名间接映射：
    # CLI 暴露更简短的 "model"，取值后再经 normalize_model_selector 写入 model_id。
    # 多数 override 键名与目标字段同名，仅 "model" 与下方 "watermark" 为 CLI 简称的例外。
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

    config_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "base_url": _pick_str(override_values, "base_url", "ARK_BASE_URL", env_values),
        "model_id": model_id,
        "default_size": _pick_str(
            override_values, "default_size", "SEEDREAM_DEFAULT_SIZE", env_values
        ),
        # override 键名 "watermark" 对应 SeedreamConfig.default_watermark 字段，
        # 与 "model" 同属 CLI 简称，未与字段同名。
        "default_watermark": _pick_bool(
            override_values, "watermark", "SEEDREAM_DEFAULT_WATERMARK", env_values
        ),
        "timeout": _pick_int(override_values, "timeout", "SEEDREAM_TIMEOUT", env_values),
        "api_timeout": _pick_int(
            override_values, "api_timeout", "SEEDREAM_API_TIMEOUT", env_values
        ),
        "max_retries": _pick_int(
            override_values, "max_retries", "SEEDREAM_MAX_RETRIES", env_values
        ),
        "log_level": _pick_str(override_values, "log_level", "LOG_LEVEL", env_values),
        "log_file": _pick_optional_str(override_values, "log_file", "LOG_FILE", env_values),
        "auto_save_enabled": _pick_bool(
            override_values, "auto_save_enabled", "SEEDREAM_AUTO_SAVE_ENABLED", env_values
        ),
        "auto_save_base_dir": _pick_optional_str(
            override_values, "auto_save_base_dir", "SEEDREAM_AUTO_SAVE_BASE_DIR", env_values
        ),
        "auto_save_download_timeout": _pick_int(
            override_values,
            "auto_save_download_timeout",
            "SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT",
            env_values,
        ),
        "auto_save_max_retries": _pick_int(
            override_values,
            "auto_save_max_retries",
            "SEEDREAM_AUTO_SAVE_MAX_RETRIES",
            env_values,
        ),
        "auto_save_max_file_size": _pick_int(
            override_values,
            "auto_save_max_file_size",
            "SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE",
            env_values,
        ),
        "auto_save_max_concurrent": _pick_int(
            override_values,
            "auto_save_max_concurrent",
            "SEEDREAM_AUTO_SAVE_MAX_CONCURRENT",
            env_values,
        ),
        "auto_save_date_folder": _pick_bool(
            override_values,
            "auto_save_date_folder",
            "SEEDREAM_AUTO_SAVE_DATE_FOLDER",
            env_values,
        ),
        "auto_save_cleanup_days": _pick_int(
            override_values,
            "auto_save_cleanup_days",
            "SEEDREAM_AUTO_SAVE_CLEANUP_DAYS",
            env_values,
        ),
        "auto_save_max_total_bytes": _pick_optional_int(
            override_values,
            "auto_save_max_total_bytes",
            "SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES",
            env_values,
        ),
        "stream_buffer_max_size": _pick_int(
            override_values,
            "stream_buffer_max_size",
            "SEEDREAM_STREAM_BUFFER_MAX_SIZE",
            env_values,
        ),
        "stream_chunk_size": _pick_int(
            override_values, "stream_chunk_size", "SEEDREAM_STREAM_CHUNK_SIZE", env_values
        ),
        "image_prepare_concurrency": _pick_int(
            override_values,
            "image_prepare_concurrency",
            "SEEDREAM_IMAGE_PREPARE_CONCURRENCY",
            env_values,
        ),
        "prepare_cache_max": _pick_int(
            override_values, "prepare_cache_max", "SEEDREAM_PREPARE_CACHE_MAX", env_values
        ),
        "prepare_cache_max_bytes": _pick_int(
            override_values,
            "prepare_cache_max_bytes",
            "SEEDREAM_PREPARE_CACHE_MAX_BYTES",
            env_values,
        ),
        "workspace_root": _pick_optional_str(
            override_values, "workspace_root", "SEEDREAM_WORKSPACE_ROOT", env_values
        ),
        "http_auth_token": _pick_optional_str(
            override_values, "http_auth_token", "SEEDREAM_HTTP_AUTH_TOKEN", env_values
        ),
        "http_max_body_size": _pick_int(
            override_values, "http_max_body_size", "SEEDREAM_HTTP_MAX_BODY_SIZE", env_values
        ),
    }
    # 断言所有带 env metadata 的字段都在构造调用中显式传值，防止新增 _env_field 字段
    # 被静默忽略而仅回落到默认值。开发期同步遗漏会立即暴露。
    missing_env_fields = set(_FIELD_ENV_MAP.keys()) - set(config_kwargs.keys())
    if missing_env_fields:
        raise AssertionError(
            f"以下字段已声明 env metadata 但未在配置构建中传值: {sorted(missing_env_fields)}"
        )
    return SeedreamConfig(**config_kwargs)


# 配置构建串行化锁：保护 .env 读取与配置构建，避免并发构建竞态
_config_build_lock = threading.Lock()
# 全局配置实例的惰性初始化锁。与 _config_build_lock 分离，因为 get_global_config
# 持该锁时会调用 from_env，而 from_env 内部又复用 _config_build_lock，共用同一把
# 锁会造成不可重入死锁
_global_config_lock = threading.Lock()

_global_config: SeedreamConfig | None = None
# CLI 注入的活动配置，优先于 _global_config。server 与 path_utils 经 get_active_config
# 共用此源；reload_config 重置其为 None 以回退重建后的全局配置，消除活动配置与全局
# 配置的双单例分叉。
_active_config: SeedreamConfig | None = None


def get_global_config() -> SeedreamConfig:
    """获取全局配置实例，首次调用时惰性构建并经双检锁缓存。"""
    global _global_config
    if _global_config is not None:
        return _global_config
    with _global_config_lock:
        if _global_config is None:
            _global_config = SeedreamConfig.from_env()
        return _global_config


def set_config(config: SeedreamConfig) -> None:
    """替换生效配置：写入全局实例，若已注入活动配置则同步更新，使本调用始终替换生效配置。

    CLI 启动后 get_active_config 优先返回 _active_config，仅写 _global_config 会被遮蔽；
    故当 _active_config 已设置时一并更新，保证 set_config 在任何阶段都让后续读取拿到新实例。
    """
    global _global_config, _active_config
    with _global_config_lock:
        _global_config = config
        if _active_config is not None:
            _active_config = config


def get_active_config() -> SeedreamConfig:
    """获取活动配置：CLI 注入的活动配置优先，回退全局默认实例。"""
    if _active_config is not None:
        return _active_config
    return get_global_config()


def set_active_config(config: SeedreamConfig | None) -> None:
    """设置或清除 CLI 注入的活动配置。

    None 表示清除活动配置，后续 get_active_config 回退到全局默认。CLI 启动时注入，
    使 server 与 path_utils 共用同一活动配置源。
    """
    global _active_config
    with _global_config_lock:
        _active_config = config


def reload_config(env_file: str | None = None) -> None:
    """重新加载全局配置并重置活动配置。

    重建全局配置实例并清除活动配置，使后续 get_active_config 回退到新的全局实例，
    确保 server 的 client/tools 与 path_utils 读到一致的新配置，消除双单例分叉。
    """
    global _global_config, _active_config
    with _global_config_lock:
        _global_config = SeedreamConfig.from_env(env_file)
        _active_config = None
