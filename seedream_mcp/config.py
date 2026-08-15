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
from urllib.parse import urlparse

from dotenv import dotenv_values

from .utils.core.errors import SeedreamConfigError, SeedreamValidationError, _is_sensitive_key
from .utils.core.formats import DEFAULT_MAX_FILE_SIZE
from .utils.io.io_path import (
    clear_resolved_env_root_cache,
    register_env_workspace_root_provider,
)
from .utils.model.model_capabilities import MODEL_ALIASES, DEPRECATED_MODEL_TOKENS
from .utils.core.validators import parse_bool, validate_size_for_model

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 项目根 .env 语义仅在源码 checkout 下成立：wheel 安装态 PROJECT_ROOT 指向
# site-packages 的上级目录，该处不会有项目 .env，安装部署依赖当前工作目录 .env
# 或显式 env_file 提供配置。
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

# MODEL_ALIASES 与 DEPRECATED_MODEL_TOKENS 属模型知识，统一定义于 model_capabilities，
# 此处经 import 暴露供 normalize_model_selector 与 validate 使用，外部仍可从 config 导入。

# 合法日志级别，供 config 校验与 CLI choices 共用此单一来源。
LEGAL_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# lifespan 上下文字典键，app_lifespan 产出方与 parallel/server 消费方的契约。
# config 为双方共同底层依赖，键集中定义于此，core 层复用键时无需依赖顶层装配模块。
LIFESPAN_KEY_CONFIG = "config"
LIFESPAN_KEY_CLIENT = "client"
LIFESPAN_KEY_DOWNLOAD_MANAGER = "download_manager"

# streamable-http 默认监听配置：argparse 默认值、传输装配与 resources 的 lifespan
# 复位共用此单一来源。与 lifespan 键同理由集中于 config——transport 与 resources 互为
# 延迟导入的近邻层，常量若落在任一侧都会形成对另一侧的顶层依赖回环，config 是双方
# 共同底层。
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000

# dataclass 字段 metadata 中登记环境变量名的键，字段定义据此声明对应环境变量名。
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
    Seedream MCP 工具配置。

    封装 Seedream 服务的所有配置参数，包括 API 认证、模型设置、日志配置和自动保存功能。
    各字段默认值与环境变量名经 _env_field 绑定于字段定义。

    Attributes:
        api_key: 火山引擎 API 密钥。
        base_url: API 端点 URL。
        allow_http_base_url: http:// 明文 base_url 的显式豁免开关；http 明文会使 API
            密钥在网络上裸传，默认拒绝。
        model_id: 模型标识，构造校验时展开别名为完整 Model ID。
        default_size: 默认图像尺寸，构造校验时按模型能力标准化。
        default_watermark: 默认是否在生成图片上添加水印。
        timeout: 通用超时秒数。
        api_timeout: API 调用超时秒数。
        max_retries: API 调用最大重试次数。
        log_level: 日志级别。
        log_file: 日志文件路径，未设置时使用日志系统默认路径。
        auto_save_enabled: 是否启用自动保存。
        auto_save_base_dir: 自动保存根目录，未设置时回退工作区 images 目录。
        auto_save_download_timeout: 自动保存下载超时秒数。
        auto_save_max_retries: 自动保存下载最大重试次数。
        auto_save_max_file_size: 自动保存单文件大小上限字节数。
        auto_save_max_concurrent: 自动保存并发下载数上限。
        auto_save_date_folder: 是否按日期子目录保存图片。
        auto_save_cleanup_days: 旧文件自动清理天数。
        auto_save_max_total_bytes: 保存目录总字节上限，超限按最旧文件优先驱逐。
        stream_buffer_max_size: SSE 流式响应缓冲区上限字节数。
        stream_chunk_size: SSE 流式响应读取块大小字节数。
        response_body_limit: 上游响应体读取总量上限字节数，None 时按
            auto_save_max_file_size × 20 推导；非流式 JSON、流式 JSON 与 SSE
            三条读取路径共用。
        image_prepare_concurrency: 参考图预处理并发上限。
        prepare_cache_max: 参考图预处理结果 LRU 缓存的条目数上限。
        prepare_cache_max_bytes: 参考图预处理结果缓存的累计字节上限，防止大图缓存
            累积撑爆内存。
        workspace_root: 无 MCP Roots 时本地文件访问边界的回退目录。
        http_auth_token: streamable-http 传输的 Bearer 鉴权令牌。
        http_max_body_size: streamable-http 请求体大小上限字节数；默认 64MB，MCP 正常
            载荷远小于 100MB，单图 data URI 上限约 40MB，兼顾多图融合。
    """

    api_key: str

    base_url: str = _env_field("https://ark.cn-beijing.volces.com/api/v3", "ARK_BASE_URL")
    allow_http_base_url: bool = _env_field(False, "SEEDREAM_ALLOW_HTTP_BASE_URL")
    model_id: str = _env_field("doubao-seedream-5-0-260128", "SEEDREAM_MODEL_ID")
    default_size: str = _env_field("2K", "SEEDREAM_DEFAULT_SIZE")
    default_watermark: bool = _env_field(False, "SEEDREAM_DEFAULT_WATERMARK")
    timeout: int = _env_field(60, "SEEDREAM_TIMEOUT")
    api_timeout: int = _env_field(600, "SEEDREAM_API_TIMEOUT")
    max_retries: int = _env_field(3, "SEEDREAM_MAX_RETRIES")

    log_level: str = _env_field("INFO", "LOG_LEVEL")
    log_file: str | None = _env_field(None, "LOG_FILE")

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

    stream_buffer_max_size: int = _env_field(10 * 1024 * 1024, "SEEDREAM_STREAM_BUFFER_MAX_SIZE")
    stream_chunk_size: int = _env_field(1024 * 1024, "SEEDREAM_STREAM_CHUNK_SIZE")

    response_body_limit: int | None = _env_field(None, "SEEDREAM_RESPONSE_BODY_LIMIT")

    image_prepare_concurrency: int = _env_field(5, "SEEDREAM_IMAGE_PREPARE_CONCURRENCY")

    prepare_cache_max: int = _env_field(32, "SEEDREAM_PREPARE_CACHE_MAX")

    prepare_cache_max_bytes: int = _env_field(256 * 1024 * 1024, "SEEDREAM_PREPARE_CACHE_MAX_BYTES")

    workspace_root: str | None = _env_field(None, "SEEDREAM_WORKSPACE_ROOT")
    http_auth_token: str | None = _env_field(None, "SEEDREAM_HTTP_AUTH_TOKEN")
    http_max_body_size: int = _env_field(64 * 1024 * 1024, "SEEDREAM_HTTP_MAX_BODY_SIZE")

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """校验配置参数合法性与业务约束，并在通过时做规范化写回。

        规范化包括展开模型别名为 model_id、按模型能力校验并标准化 default_size、
        将 log_level 统一为大写。任一校验失败抛出 SeedreamConfigError。
        """
        if not self.api_key or self.api_key.strip() == "":
            raise SeedreamConfigError(f"API密钥不能为空{_env_var_suffix('api_key')}")
        if self.api_key == "your_api_key_here":
            raise SeedreamConfigError(
                f"请设置有效的API密钥，不能使用默认占位符{_env_var_suffix('api_key')}"
            )

        # RFC 3986 规定 scheme 大小写不敏感，HTTPS:// 等大写形态经 urlparse 取小写后判定。
        base_url_scheme = urlparse(self.base_url).scheme.lower() if self.base_url else ""
        if not self.base_url or base_url_scheme not in ("http", "https"):
            raise SeedreamConfigError(
                f"base_url必须是有效的HTTP/HTTPS URL{_env_var_suffix('base_url')}"
            )
        if base_url_scheme == "http":
            if not self.allow_http_base_url:
                raise SeedreamConfigError(
                    "base_url 使用 http:// 会使 API 密钥在网络上明文传输，默认拒绝；"
                    "仅自建可信内网端点可设 SEEDREAM_ALLOW_HTTP_BASE_URL=true 豁免"
                )
            from .utils.core.logs import get_logger

            get_logger(__name__).warning(
                "ARK_BASE_URL 使用 http:// 且已豁免，API 密钥将在网络上明文传输，"
                "仅限自建可信内网端点使用"
            )

        if not self.model_id or self.model_id.strip() == "":
            raise SeedreamConfigError(f"model_id不能为空{_env_var_suffix('model_id')}")
        object.__setattr__(self, "model_id", normalize_model_selector(self.model_id))
        if any(token in self.model_id for token in DEPRECATED_MODEL_TOKENS):
            # 可用别名清单运行时从 MODEL_ALIASES 派生，新增模型时提示自动同步，
            # 消除与 CLI choices 派生机制并存的最后一个硬编码模型清单同步点。
            aliases = "/".join(MODEL_ALIASES)
            raise SeedreamConfigError(
                f"已不支持的模型: {self.model_id}（3.0/seededit-3.0 已下线），"
                f"请使用 {aliases} 或对应 Endpoint ID{_env_var_suffix('model_id')}"
            )

        if not isinstance(self.default_size, str) or not self.default_size.strip():
            raise SeedreamConfigError(f"default_size不能为空{_env_var_suffix('default_size')}")

        normalized_default_size = self.default_size.strip()
        try:
            object.__setattr__(
                self,
                "default_size",
                validate_size_for_model(normalized_default_size, self.model_id),
            )
        except SeedreamValidationError as exc:
            raise SeedreamConfigError(
                f"default_size无效: {exc.message}{_env_var_suffix('default_size')}"
            ) from exc

        if self.timeout <= 0:
            raise SeedreamConfigError(f"timeout必须大于0{_env_var_suffix('timeout')}")
        if self.api_timeout <= 0:
            raise SeedreamConfigError(f"api_timeout必须大于0{_env_var_suffix('api_timeout')}")
        if self.max_retries < 1:
            raise SeedreamConfigError(f"max_retries不能小于1{_env_var_suffix('max_retries')}")

        if self.log_level.upper() not in LEGAL_LOG_LEVELS:
            raise SeedreamConfigError(
                f"log_level必须是以下值之一: {list(LEGAL_LOG_LEVELS)}"
                f"{_env_var_suffix('log_level')}"
            )
        object.__setattr__(self, "log_level", self.log_level.upper())

        if self.auto_save_download_timeout <= 0:
            raise SeedreamConfigError(
                f"auto_save_download_timeout必须大于0"
                f"{_env_var_suffix('auto_save_download_timeout')}"
            )
        if self.auto_save_max_retries < 0:
            raise SeedreamConfigError(
                f"auto_save_max_retries不能小于0{_env_var_suffix('auto_save_max_retries')}"
            )
        if self.auto_save_max_file_size <= 0:
            raise SeedreamConfigError(
                f"auto_save_max_file_size必须大于0" f"{_env_var_suffix('auto_save_max_file_size')}"
            )
        if self.auto_save_max_concurrent <= 0:
            raise SeedreamConfigError(
                f"auto_save_max_concurrent必须大于0"
                f"{_env_var_suffix('auto_save_max_concurrent')}"
            )
        if self.auto_save_cleanup_days < 0:
            raise SeedreamConfigError(
                f"auto_save_cleanup_days不能小于0{_env_var_suffix('auto_save_cleanup_days')}"
            )
        if self.auto_save_max_total_bytes is not None and self.auto_save_max_total_bytes <= 0:
            raise SeedreamConfigError(
                f"auto_save_max_total_bytes必须大于0"
                f"{_env_var_suffix('auto_save_max_total_bytes')}"
            )

        if self.stream_buffer_max_size <= 0:
            raise SeedreamConfigError(
                f"stream_buffer_max_size必须大于0{_env_var_suffix('stream_buffer_max_size')}"
            )
        if self.stream_chunk_size <= 0:
            raise SeedreamConfigError(
                f"stream_chunk_size必须大于0{_env_var_suffix('stream_chunk_size')}"
            )
        if self.stream_chunk_size > self.stream_buffer_max_size:
            raise SeedreamConfigError(
                f"stream_chunk_size不能大于stream_buffer_max_size"
                f"{_env_var_suffix('stream_chunk_size', 'stream_buffer_max_size')}"
            )
        if self.response_body_limit is not None and self.response_body_limit <= 0:
            raise SeedreamConfigError(
                f"response_body_limit必须大于0{_env_var_suffix('response_body_limit')}"
            )

        if self.image_prepare_concurrency <= 0:
            raise SeedreamConfigError(
                f"image_prepare_concurrency必须大于0"
                f"{_env_var_suffix('image_prepare_concurrency')}"
            )

        if self.prepare_cache_max < 1:
            raise SeedreamConfigError(
                f"prepare_cache_max不能小于1{_env_var_suffix('prepare_cache_max')}"
            )
        if self.prepare_cache_max_bytes < 1:
            raise SeedreamConfigError(
                f"prepare_cache_max_bytes不能小于1{_env_var_suffix('prepare_cache_max_bytes')}"
            )

        if self.auto_save_base_dir:
            self._validate_dir_field(self.auto_save_base_dir, "auto_save_base_dir")

        if self.workspace_root:
            self._validate_dir_field(self.workspace_root, "workspace_root")

        if self.http_max_body_size < 1024 * 1024:
            raise SeedreamConfigError(
                f"http_max_body_size 不能低于 1MB（1048576 字节）"
                f"{_env_var_suffix('http_max_body_size')}"
            )

    def _validate_dir_field(self, value: str, field_name: str) -> None:
        """校验给定路径指向有效目录，存在但非目录时抛 SeedreamConfigError。

        仅校验已存在路径的目录性，不要求目录预先存在，
        使未创建的目录也能通过校验以便后续按需创建。
        """
        try:
            dir_path = Path(value).expanduser()
            if dir_path.exists() and not dir_path.is_dir():
                raise SeedreamConfigError(
                    f"{field_name}不是有效目录: {value}{_env_var_suffix(field_name)}"
                )
        except SeedreamConfigError:
            raise
        except Exception as exc:
            raise SeedreamConfigError(
                f"{field_name}路径无效: {value} -> {exc}{_env_var_suffix(field_name)}"
            ) from exc

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "SeedreamConfig":
        """从环境变量与 .env 文件构建配置实例，构建过程线程安全。"""
        return build_config_from_sources(env_file=env_file)

    def to_dict(self) -> dict[str, Any]:
        """导出为字典，名称命中敏感关键词的字段以 "***" 脱敏。

        敏感判定复用 errors 的 _is_sensitive_key 边界匹配，与结构化错误输出的脱敏
        标准保持同一来源。
        """
        result: dict[str, Any] = {}
        for config_field in fields(self):
            value = getattr(self, config_field.name)
            if _is_sensitive_key(config_field.name):
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

# api_key 为必填字段、无默认值，不适用 _env_field 登记，其环境变量名在此显式列出，
# 与配置构建显式读取 ARK_API_KEY 的路径保持一致。
_NON_METADATA_FIELD_ENV: dict[str, str] = {"api_key": "ARK_API_KEY"}


def _env_var_suffix(*field_names: str) -> str:
    """反查字段对应的环境变量名，生成校验错误消息的变量名提示后缀。

    以 _FIELD_ENV_MAP 为单一数据源，api_key 经 _NON_METADATA_FIELD_ENV 补齐。跨字段
    约束可传入多个字段名，斜杠连接各自的变量名。无法反查的字段名跳过，全部不可反查
    时返回空串，消息保持原样。validate 在实例构造期执行，晚于本模块加载完成，直接
    读取模块级映射无可见性问题。
    """
    env_names: list[str] = []
    for name in field_names:
        env_name = _FIELD_ENV_MAP.get(name) or _NON_METADATA_FIELD_ENV.get(name)
        if env_name:
            env_names.append(env_name)
    if not env_names:
        return ""
    return f"（环境变量 {'/'.join(env_names)}）"


def _field_default_str(field_name: str) -> str:
    """反射 SeedreamConfig 字段默认值并转为环境变量字符串默认值。

    bool 转为 true/false，None 转为空串，其余取 str。
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
# 供 _pick_* 系列辅助回退取值。
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
    未提供时按项目根 .env 与当前工作目录 .env 合并读取，当前工作目录覆盖项目根。
    项目根 .env 仅源码 checkout 下存在，wheel 安装态实际只有当前工作目录 .env
    参与合并。
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
        if default_env_path.is_file() and runtime_env_path != default_env_path:
            from .utils.core.logs import get_logger

            get_logger(__name__).warning(
                "当前工作目录 .env（{}）覆盖了项目根 .env（{}）的配置值；"
                "进程工作目录不受控时其中的 .env 可能注入非预期配置，请确认启动目录可信",
                runtime_env_path,
                default_env_path,
            )

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

    通过 ``_config_build_lock`` 串行化构建；streamable-http 多请求场景下可能并发
    触发配置构建，串行化保证构建语义与单线程完全一致。

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
    """构建配置对象但自身不加锁，由 :func:`build_config_from_sources` 持锁调用。"""
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
        "allow_http_base_url": _pick_bool(
            override_values, "allow_http_base_url", "SEEDREAM_ALLOW_HTTP_BASE_URL", env_values
        ),
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
        "response_body_limit": _pick_optional_int(
            override_values,
            "response_body_limit",
            "SEEDREAM_RESPONSE_BODY_LIMIT",
            env_values,
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


# 配置构建串行化锁：保护 .env 读取与配置构建，避免并发构建竞态。
_config_build_lock = threading.Lock()
# 全局配置实例的惰性初始化锁。与 _config_build_lock 分离，因为 get_global_config
# 持该锁时会调用 from_env，而 from_env 内部又复用 _config_build_lock，共用同一把
# 锁会造成不可重入死锁。
_global_config_lock = threading.Lock()

_global_config: SeedreamConfig | None = None
# CLI 注入的活动配置，优先于 _global_config。server 经 get_active_config 共用此源，
# io_path 经模块加载期注册的工作区根提供者间接读取同一活动配置；reload_config 重置
# 其为 None 以回退重建后的全局配置，消除活动配置与全局配置的双单例分叉。
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
    生效配置变更同时使 io_path 的回退根 resolve 缓存失效。
    """
    global _global_config, _active_config
    with _global_config_lock:
        _global_config = config
        if _active_config is not None:
            _active_config = config
    clear_resolved_env_root_cache()


def get_active_config() -> SeedreamConfig:
    """获取活动配置：CLI 注入的活动配置优先，回退全局默认实例。"""
    if _active_config is not None:
        return _active_config
    return get_global_config()


def set_active_config(config: SeedreamConfig | None) -> None:
    """设置或清除 CLI 注入的活动配置。

    None 表示清除活动配置，后续 get_active_config 回退到全局默认。CLI 启动时注入，
    使 server 与 io_path 经注册的提供者共用同一活动配置源；测试复位协议经
    set_active_config(None) 同步使 io_path 的回退根缓存失效。
    """
    global _active_config
    with _global_config_lock:
        _active_config = config
    clear_resolved_env_root_cache()


def reload_config(env_file: str | None = None) -> None:
    """重新加载全局配置并重置活动配置。

    重建全局配置实例并清除活动配置，使后续 get_active_config 回退到新的全局实例，
    确保 server 的 client/tools 与 io_path 读到一致的新配置，消除双单例分叉；
    io_path 的回退根 resolve 缓存随之失效。
    """
    global _global_config, _active_config
    with _global_config_lock:
        _global_config = SeedreamConfig.from_env(env_file)
        _active_config = None
    clear_resolved_env_root_cache()


def _registered_workspace_root_provider() -> str | None:
    """向 io_path 提供当前生效的工作区根目录原始值。

    活动配置就绪时返回其 workspace_root，配置值在构建时已按优先级合并环境变量与
    .env；配置构建失败即活动配置不可得时回退读取 SEEDREAM_WORKSPACE_ROOT 环境变量，
    与 io_path 未注册提供者时的回退一致。
    """
    try:
        config = get_active_config()
    except SeedreamConfigError:
        config = None
    if config is not None:
        root = config.workspace_root
        return root.strip() if root else None
    env_root = os.getenv("SEEDREAM_WORKSPACE_ROOT")
    return env_root.strip() if env_root else None


# 模块加载即完成注入：任何加载了 config 的进程，io_path 的回退读取都经本提供者取得
# 活动配置值，消除 io_path 对顶层 config 的延迟 import 向上依赖。
register_env_workspace_root_provider(_registered_workspace_root_provider)
