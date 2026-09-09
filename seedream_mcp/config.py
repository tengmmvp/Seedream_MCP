"""Seedream MCP 工具配置管理模块。

定义 SeedreamConfig 配置数据类与多层配置加载机制，优先级为运行时覆盖 >
系统环境变量 > .env 文件 > 代码默认值。配置构建不向 os.environ 注入 .env 值，
仅写入配置对象，避免全局状态污染。
"""

from __future__ import annotations

import os
import threading
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from dotenv import dotenv_values

from .utils.core.errors import SeedreamConfigError, SeedreamValidationError, _is_sensitive_key
from .utils.core.formats import DEFAULT_MAX_FILE_SIZE
from .utils.core.logs import (
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_LOG_ROTATION_SIZE_MB,
    get_logger,
)
from .utils.core.validators import INT_TEXT_PATTERN, parse_bool, validate_size_for_model
from .utils.io.io_path import (
    clear_resolved_env_root_cache,
    register_data_root_provider,
    register_env_workspace_root_provider,
    reject_unc_declaration,
)
from .utils.model.model_capabilities import MODEL_ALIASES, DEPRECATED_MODEL_TOKENS

# 构建期告警先收集、经 drain_pending_build_warnings 后置输出：配置构建早于
# setup_logging，即时输出会随日志系统重建被移出文件通道。元素为 (级别, 消息)，
# 级别取 loguru 注册名，部署风险类告警用 ERROR，高日志级别部署下不被过滤。
_pending_build_warnings: list[tuple[str, str]] = []


def drain_pending_build_warnings() -> None:
    """输出并清空构建期收集的告警。

    server 在日志系统就绪后调用；不经 server 入口的嵌入式调用方需自行调用本函数，
    否则收集中的告警不输出。交换持 _config_build_lock，避免与并发构建的
    append 竞态丢失告警。
    """
    global _pending_build_warnings
    with _config_build_lock:
        pending = _pending_build_warnings
        _pending_build_warnings = []
    logger = get_logger()
    for level, message in pending:
        logger.log(level, message)


# 项目根 .env 层仅源码检出部署存在，pip 安装部署该层空转，CWD .env 层仍生效。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
LEGAL_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LIFESPAN_KEY_CONFIG = "config"
LIFESPAN_KEY_CLIENT = "client"
LIFESPAN_KEY_DOWNLOAD_MANAGER = "download_manager"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
# http_max_body_size 的构建期下限；过小的上限连常规 MCP JSON 载荷都无法容纳。
_HTTP_MAX_BODY_SIZE_FLOOR = 1024 * 1024
# http_auth_token 的最短字符数：compare_digest 无法弥补低熵令牌的可猜测性。
# CLI --auth-token 同口径校验（cli.py），公开供其导入。
HTTP_AUTH_TOKEN_MIN_LENGTH = 16
# auto_save_download_timeout 的上界秒数：下载总预算按停滞超时的 120 倍推导，
# 720 秒恰等于 .part 临时文件 24 小时清扫宽限；超过后预算反超宽限，在途慢下载
# 的临时文件会被并发清扫删除。
_AUTO_SAVE_DOWNLOAD_TIMEOUT_MAX_SECONDS = 720
# requestState 密钥环单钥的字节数下限，与 SDK RequestStateSecurity 的密钥强度要求一致。
_REQUEST_STATE_KEY_MIN_BYTES = 32
# SSE 读取块的下限字节数：流首 UTF-8 BOM 为 3 字节且按读取块整体判定剥离，不可跨块。
_STREAM_CHUNK_SIZE_MIN_BYTES = 3
# SSE 单事件阈值的推导常量：base64 最坏膨胀系数与事件信封余量，n 字节图片经
# 编码最长 4*ceil(n/3) 字符，叠加 data 前缀等信封开销。
_B64_WORST_CASE_NUMERATOR = 4
_SSE_EVENT_ENVELOPE_MARGIN = 4 * 1024
# 密钥环错误消息提示的密钥生成命令，生成一个解码后恰为 32 字节的十六进制密钥。
_REQUEST_STATE_KEYGEN_COMMAND = 'python -c "import secrets; print(secrets.token_hex(32))"'
_ENV_METADATA_KEY = "env"


def _env_field(default: Any, env_name: str) -> Any:
    """为 dataclass 字段绑定默认值与环境变量名，字段定义即两者映射的单一数据源。"""
    return field(default=default, metadata={_ENV_METADATA_KEY: env_name})


@dataclass(frozen=True)
class SeedreamConfig:
    """Seedream MCP 工具配置。

    各字段默认值与环境变量名经 _env_field 绑定于字段定义，构造时经 validate
    校验并规范化。

    Attributes:
        api_key: 火山引擎 API 密钥。
        base_url: API 端点 URL。
        allow_http_base_url: http:// 明文 base_url 的显式豁免开关；默认拒绝，仅自建
            可信内网端点开启。
        model_id: 模型标识，构造校验时展开别名为完整 Model ID；也可为 Endpoint ID
            （ep- 开头）。
        default_size: 默认图像尺寸，构造校验时按模型能力标准化。
        default_watermark: 是否默认为生成结果添加水印。
        timeout: 通用超时秒数。
        api_timeout: API 调用超时秒数。
        max_retries: API 调用最大重试次数。
        generate_concurrency: 进程级生成并发准入上限，约束同时在途的生成 API 请求数。
        log_level: 日志级别，构造校验时统一为大写。
        log_rotation_size: 日志文件轮转大小 MB，超过即轮转压缩。
        log_retention_days: 轮转日志的保留天数，超期自动清理。
        auto_save_enabled: 是否启用生成图片的自动保存。
        data_root: 数据根目录，图片、缩略图缓存与日志收在其 .seedream 子目录；
            未设置时取工作根目录。
        auto_save_download_timeout: 自动保存下载超时秒数，上界 720 秒。
        auto_save_max_retries: 自动保存下载最大重试次数。
        auto_save_max_file_size: 自动保存单文件大小上限字节数。
        auto_save_max_concurrent: 自动保存并发下载数上限。
        auto_save_date_folder: 是否按日期子目录保存图片。
        auto_save_cleanup_days: 旧文件自动清理天数。
        auto_save_max_total_bytes: 保存目录总字节上限，超限按最旧文件优先驱逐；
            None 表示不限制，显式设置 0 时归一为 None。
        auto_save_fsync: 自动保存落盘是否在原子替换前执行 fsync，默认关闭；对崩溃
            一致性有要求时开启。
        stream_buffer_max_size: SSE 流式响应缓冲区上限字节数。
        stream_chunk_size: SSE 流式响应读取块大小字节数。
        sse_event_max_size: 单个 SSE 事件的截断阈值字节数；None 时按缓冲区上限与
            单图 base64 最坏展开二者的较大值推导。
        response_body_limit: 上游响应体读取总量上限字节数，三条读取路径共用；None 时
            按 auto_save_max_file_size × 20 推导。
        image_prepare_concurrency: 参考图预处理并发上限。
        prepare_cache_max: 参考图预处理结果 LRU 缓存的条目数上限。
        prepare_cache_max_bytes: 参考图预处理结果缓存的累计字节上限。
        preview_enabled: 是否在生成工具结果中附带已保存图片的缩略图预览，长边不超过
            768 像素；关闭后仅返回文本与 structuredContent。
        workspace_root: 无 MCP Roots 时本地文件访问边界的回退目录。
        http_auth_token: streamable-http 传输的 Bearer 鉴权令牌；配置时长度不得
            少于 HTTP_AUTH_TOKEN_MIN_LENGTH 字符。
        http_max_body_size: streamable-http 请求体大小上限字节数，默认 64MB。
        web_enabled: 是否在 streamable-http 传输上开启 Web 操作台，默认关闭；开启后
            同一进程提供 /web 网页与 /web/api 接口，stdio 传输不受影响。
        http_allowed_hosts: 非回环绑定的 Host 头允许列表，条目支持 host、host:port
            与尾部 :* 端口通配；None 时具体地址绑定按绑定地址派生白名单启用校验，
            通配绑定保持关闭。仅经 SEEDREAM_HTTP_ALLOWED_HOSTS 环境变量解析，
            CLI 不暴露参数。
        request_state_secret_keys: requestState 密钥环，多副本 HTTP 部署共享的
            十六进制密钥列表，首键密封、全键解封支持零停机轮换；None 表示不启用，
            保持 SDK 默认的进程临时密钥。仅经 SEEDREAM_REQUEST_STATE_KEYS
            环境变量解析，CLI 不暴露参数。
    """

    api_key: str

    base_url: str = _env_field("https://ark.cn-beijing.volces.com/api/v3", "ARK_BASE_URL")
    allow_http_base_url: bool = _env_field(False, "SEEDREAM_ALLOW_HTTP_BASE_URL")
    model_id: str = _env_field(MODEL_ALIASES["doubao-seedream-5.0"], "SEEDREAM_MODEL_ID")
    default_size: str = _env_field("2K", "SEEDREAM_DEFAULT_SIZE")
    default_watermark: bool = _env_field(False, "SEEDREAM_DEFAULT_WATERMARK")
    timeout: int = _env_field(60, "SEEDREAM_TIMEOUT")
    api_timeout: int = _env_field(600, "SEEDREAM_API_TIMEOUT")
    max_retries: int = _env_field(3, "SEEDREAM_MAX_RETRIES")
    generate_concurrency: int = _env_field(3, "SEEDREAM_GENERATE_CONCURRENCY")

    log_level: str = _env_field("INFO", "SEEDREAM_LOG_LEVEL")
    log_rotation_size: int = _env_field(DEFAULT_LOG_ROTATION_SIZE_MB, "SEEDREAM_LOG_ROTATION_SIZE")
    log_retention_days: int = _env_field(DEFAULT_LOG_RETENTION_DAYS, "SEEDREAM_LOG_RETENTION_DAYS")

    auto_save_enabled: bool = _env_field(True, "SEEDREAM_AUTO_SAVE_ENABLED")
    data_root: str | None = _env_field(None, "SEEDREAM_DATA_ROOT")
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
    auto_save_fsync: bool = _env_field(False, "SEEDREAM_AUTO_SAVE_FSYNC")

    stream_buffer_max_size: int = _env_field(10 * 1024 * 1024, "SEEDREAM_STREAM_BUFFER_MAX_SIZE")
    stream_chunk_size: int = _env_field(1024 * 1024, "SEEDREAM_STREAM_CHUNK_SIZE")
    sse_event_max_size: int | None = _env_field(None, "SEEDREAM_SSE_EVENT_MAX_SIZE")

    response_body_limit: int | None = _env_field(None, "SEEDREAM_RESPONSE_BODY_LIMIT")

    image_prepare_concurrency: int = _env_field(5, "SEEDREAM_IMAGE_PREPARE_CONCURRENCY")

    prepare_cache_max: int = _env_field(32, "SEEDREAM_PREPARE_CACHE_MAX")

    prepare_cache_max_bytes: int = _env_field(256 * 1024 * 1024, "SEEDREAM_PREPARE_CACHE_MAX_BYTES")

    preview_enabled: bool = _env_field(True, "SEEDREAM_PREVIEW_ENABLED")

    workspace_root: str | None = _env_field(None, "SEEDREAM_WORKSPACE_ROOT")
    http_auth_token: str | None = _env_field(None, "SEEDREAM_HTTP_AUTH_TOKEN")
    http_max_body_size: int = _env_field(64 * 1024 * 1024, "SEEDREAM_HTTP_MAX_BODY_SIZE")
    web_enabled: bool = _env_field(False, "SEEDREAM_WEB_ENABLED")
    http_allowed_hosts: tuple[str, ...] | None = _env_field(None, "SEEDREAM_HTTP_ALLOWED_HOSTS")
    request_state_secret_keys: tuple[bytes, ...] | None = _env_field(
        None, "SEEDREAM_REQUEST_STATE_KEYS"
    )

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """校验配置参数合法性与业务约束，并在通过时做规范化写回。

        各域校验拆分至 _validate_* 私有方法。规范化包括展开模型别名、按模型能力
        标准化 default_size、将 log_level 统一为大写。

        Raises:
            SeedreamConfigError: 任一配置项校验失败。
        """
        self._validate_api_credentials()
        self._validate_api_endpoint()
        self._validate_model_selection()
        self._validate_default_size()
        self._validate_client_timeouts()
        self._validate_log_settings()
        self._validate_auto_save_bounds()
        self._validate_streaming_bounds()
        self._validate_prepare_cache_bounds()
        self._validate_dir_fields()
        self._validate_http_fields()
        self._validate_request_state_keys()

    def _validate_api_credentials(self) -> None:
        """校验 api_key 非空且非默认占位符。"""
        if not self.api_key or self.api_key.strip() == "":
            raise SeedreamConfigError(f"API密钥不能为空{_env_var_suffix('api_key')}")
        _ensure_field_utf8_encodable(self.api_key, "api_key")
        if self.api_key == "your_api_key_here":
            raise SeedreamConfigError(
                f"请设置有效的API密钥，不能使用默认占位符{_env_var_suffix('api_key')}"
            )

    def _validate_api_endpoint(self) -> None:
        """校验 base_url 的 scheme、主机名与 http 明文豁免。"""
        # RFC 3986 规定 scheme 大小写不敏感，HTTPS:// 等大写形态经 urlparse 取小写后判定。
        parsed_base_url = urlparse(self.base_url)
        _ensure_field_utf8_encodable(self.base_url, "base_url")
        base_url_scheme = parsed_base_url.scheme.lower()
        if not self.base_url or base_url_scheme not in ("http", "https"):
            raise SeedreamConfigError(
                f"base_url必须是有效的HTTP/HTTPS URL{_env_var_suffix('base_url')}"
            )
        # netloc 缺失的畸形 URL 在构造期拒绝，避免运行期才以网络错误档失败。
        if not parsed_base_url.netloc.strip():
            raise SeedreamConfigError(f"base_url缺少主机名{_env_var_suffix('base_url')}")
        if parsed_base_url.query or parsed_base_url.fragment:
            raise SeedreamConfigError(
                f"base_url不能包含查询参数或片段{_env_var_suffix('base_url')}"
            )
        if base_url_scheme == "http":
            if not self.allow_http_base_url:
                raise SeedreamConfigError(
                    "base_url 使用 http:// 会使 API 密钥在网络上明文传输，默认拒绝；"
                    "仅自建可信内网端点可设 SEEDREAM_ALLOW_HTTP_BASE_URL=true 豁免"
                )
            _pending_build_warnings.append(
                (
                    "ERROR",
                    "ARK_BASE_URL 使用 http:// 且已豁免，API 密钥将在网络上明文传输，"
                    "仅限自建可信内网端点使用",
                )
            )

    def _validate_model_selection(self) -> None:
        """校验 model_id 并展开别名为完整 Model ID。"""
        if not self.model_id or self.model_id.strip() == "":
            raise SeedreamConfigError(f"model_id不能为空{_env_var_suffix('model_id')}")
        _ensure_field_utf8_encodable(self.model_id, "model_id")
        # 展开后统一为小写，下线 token 子串检查随之为大小写不敏感。
        object.__setattr__(self, "model_id", normalize_model_selector(self.model_id))
        if any(token in self.model_id for token in DEPRECATED_MODEL_TOKENS):
            aliases = "/".join(MODEL_ALIASES)
            deprecated = "/".join(sorted(DEPRECATED_MODEL_TOKENS))
            raise SeedreamConfigError(
                f"已不支持的模型: {self.model_id}（{deprecated} 已下线），"
                f"请使用 {aliases} 或对应 Endpoint ID{_env_var_suffix('model_id')}"
            )

    def _validate_default_size(self) -> None:
        """校验 default_size 非空并按模型能力标准化。"""
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

    def _validate_client_timeouts(self) -> None:
        """校验通用超时、API 超时与 API 重试次数下界及 float 可转换性。"""
        for field_name in ("timeout", "api_timeout"):
            value = getattr(self, field_name)
            if value <= 0:
                raise SeedreamConfigError(f"{field_name}必须大于0{_env_var_suffix(field_name)}")
            try:
                float(value)
            except OverflowError as exc:
                # 原值回显会触发 int 字符串转换位数上限，仅报字段名。
                raise SeedreamConfigError(
                    f"{field_name}超出 float 可表示范围{_env_var_suffix(field_name)}"
                ) from exc
        if self.generate_concurrency < 1:
            raise SeedreamConfigError(
                f"generate_concurrency必须不小于1{_env_var_suffix('generate_concurrency')}"
            )
        if self.max_retries < 0:
            raise SeedreamConfigError(f"max_retries不能为负数{_env_var_suffix('max_retries')}")

    def _validate_log_settings(self) -> None:
        """校验日志三项：级别合法并规范化为大写，轮转与保留为正数。"""
        if self.log_level.upper() not in LEGAL_LOG_LEVELS:
            raise SeedreamConfigError(
                f"log_level必须是以下值之一: {list(LEGAL_LOG_LEVELS)}"
                f"{_env_var_suffix('log_level')}"
            )
        object.__setattr__(self, "log_level", self.log_level.upper())
        if self.log_rotation_size <= 0:
            raise SeedreamConfigError(
                f"log_rotation_size必须大于0{_env_var_suffix('log_rotation_size')}"
            )
        if self.log_retention_days <= 0:
            raise SeedreamConfigError(
                f"log_retention_days必须大于0{_env_var_suffix('log_retention_days')}"
            )

    def _validate_auto_save_bounds(self) -> None:
        """校验自动保存各数值字段的下界与下载停滞超时的上界，总量上限显式 0 归一为 None。"""
        if self.auto_save_download_timeout <= 0:
            raise SeedreamConfigError(
                "auto_save_download_timeout必须大于0"
                f"{_env_var_suffix('auto_save_download_timeout')}"
            )
        if self.auto_save_download_timeout > _AUTO_SAVE_DOWNLOAD_TIMEOUT_MAX_SECONDS:
            raise SeedreamConfigError(
                f"auto_save_download_timeout不能大于{_AUTO_SAVE_DOWNLOAD_TIMEOUT_MAX_SECONDS}"
                "（会使下载总预算超过 .part 临时文件清扫宽限 24 小时，"
                "在途慢下载的临时文件会被并发清扫）"
                f"{_env_var_suffix('auto_save_download_timeout')}"
            )
        if self.auto_save_max_retries < 0:
            raise SeedreamConfigError(
                f"auto_save_max_retries不能小于0{_env_var_suffix('auto_save_max_retries')}"
            )
        if self.auto_save_max_file_size <= 0:
            raise SeedreamConfigError(
                "auto_save_max_file_size必须大于0" f"{_env_var_suffix('auto_save_max_file_size')}"
            )
        if self.auto_save_max_concurrent <= 0:
            raise SeedreamConfigError(
                "auto_save_max_concurrent必须大于0" f"{_env_var_suffix('auto_save_max_concurrent')}"
            )
        if self.auto_save_cleanup_days < 0:
            raise SeedreamConfigError(
                f"auto_save_cleanup_days不能小于0{_env_var_suffix('auto_save_cleanup_days')}"
            )
        if self.auto_save_max_total_bytes == 0 and not isinstance(
            self.auto_save_max_total_bytes, bool
        ):
            object.__setattr__(self, "auto_save_max_total_bytes", None)
        if self.auto_save_max_total_bytes is not None and self.auto_save_max_total_bytes <= 0:
            raise SeedreamConfigError(
                "auto_save_max_total_bytes必须大于0"
                f"{_env_var_suffix('auto_save_max_total_bytes')}"
            )

    def _validate_streaming_bounds(self) -> None:
        """校验流式缓冲、读取块与响应体读取上限。"""
        if self.stream_buffer_max_size <= 0:
            raise SeedreamConfigError(
                f"stream_buffer_max_size必须大于0{_env_var_suffix('stream_buffer_max_size')}"
            )
        if self.stream_chunk_size < _STREAM_CHUNK_SIZE_MIN_BYTES:
            raise SeedreamConfigError(
                f"stream_chunk_size不能低于{_STREAM_CHUNK_SIZE_MIN_BYTES}字节"
                "（流首 UTF-8 BOM 为 3 字节，不可跨读取块剥离）"
                f"{_env_var_suffix('stream_chunk_size')}"
            )
        if self.stream_chunk_size > self.stream_buffer_max_size:
            raise SeedreamConfigError(
                "stream_chunk_size不能大于stream_buffer_max_size"
                f"{_env_var_suffix('stream_chunk_size', 'stream_buffer_max_size')}"
            )
        if (
            self.sse_event_max_size is not None
            and self.sse_event_max_size < self.derived_sse_event_max_size()
        ):
            raise SeedreamConfigError(
                "sse_event_max_size不能低于单图 base64 最坏展开推导值，过小会截断合法图片事件"
                f"{_env_var_suffix('sse_event_max_size')}"
            )
        if self.response_body_limit is not None and self.response_body_limit <= 0:
            raise SeedreamConfigError(
                f"response_body_limit必须大于0{_env_var_suffix('response_body_limit')}"
            )

    def derived_sse_event_max_size(self) -> int:
        """单事件阈值的推导下界：缓冲区上限与单图 base64 最坏展开二者的较大值。

        低于该下界的阈值会整段截断合法图片事件，显式配置仅允许在推导值之上调大。
        """
        return max(
            self.stream_buffer_max_size,
            _B64_WORST_CASE_NUMERATOR * ((self.auto_save_max_file_size + 2) // 3)
            + _SSE_EVENT_ENVELOPE_MARGIN,
        )

    def _validate_prepare_cache_bounds(self) -> None:
        """校验图像预处理并发与预处理缓存容量下界。"""
        if self.image_prepare_concurrency <= 0:
            raise SeedreamConfigError(
                "image_prepare_concurrency必须大于0"
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

    def _validate_dir_fields(self) -> None:
        """校验各目录型字段指向有效目录，目录声明拒绝 UNC 形态。"""
        if self.data_root:
            # UNC 数据根目录的 resolve 会触发 SMB 认证，构建期响亮失败而非运行期逐次降级。
            reject_unc_declaration(
                self.data_root, label="数据根目录", env_hint=_env_var_suffix("data_root")
            )
            self._validate_dir_field(self.data_root, "data_root")

        if self.workspace_root:
            reject_unc_declaration(
                self.workspace_root,
                label="工作区根目录",
                env_hint=_env_var_suffix("workspace_root"),
            )
            self._validate_dir_field(self.workspace_root, "workspace_root")

    def _validate_http_fields(self) -> None:
        """校验 streamable-http 鉴权令牌强度、请求体下限与 Host 允许列表。"""
        if self.http_auth_token and len(self.http_auth_token) < HTTP_AUTH_TOKEN_MIN_LENGTH:
            raise SeedreamConfigError(
                f"http_auth_token 长度不得少于 {HTTP_AUTH_TOKEN_MIN_LENGTH} 字符"
                f"{_env_var_suffix('http_auth_token')}"
            )
        if self.http_max_body_size < _HTTP_MAX_BODY_SIZE_FLOOR:
            raise SeedreamConfigError(
                f"http_max_body_size 不能低于 1MB（{_HTTP_MAX_BODY_SIZE_FLOOR} 字节）"
                f"{_env_var_suffix('http_max_body_size')}"
            )
        self._validate_http_allowed_hosts()

    def _validate_http_allowed_hosts(self) -> None:
        """校验 http_allowed_hosts 条目形态，端口通配未配套裸 host 时告警。

        Raises:
            SeedreamConfigError: 条目含 scheme/斜杠、非尾部通配或端口非数字。
        """
        hosts = self.http_allowed_hosts
        if hosts is None:
            return
        bare_hosts: set[str] = set()
        wildcard_hosts: set[str] = set()
        for entry in hosts:
            # Host 头值不含 scheme 与路径，条目写出这两者即配置错误。
            if "://" in entry or "/" in entry:
                raise SeedreamConfigError(
                    f"http_allowed_hosts 条目不得包含 scheme 或斜杠: {entry}"
                    f"{_env_var_suffix('http_allowed_hosts')}"
                )
            decomposed = _decompose_allowed_host_entry(entry)
            if decomposed is None:
                raise SeedreamConfigError(
                    f"http_allowed_hosts 条目仅支持 host、host:port、host:* 形态: {entry}"
                    f"{_env_var_suffix('http_allowed_hosts')}"
                )
            host_part, port_part = decomposed
            if port_part == "":
                bare_hosts.add(host_part)
            elif port_part == ":*":
                wildcard_hosts.add(host_part)

        uncovered = wildcard_hosts - bare_hosts
        if uncovered:
            _pending_build_warnings.append(
                (
                    "ERROR",
                    f"http_allowed_hosts 中 {', '.join(sorted(uncovered))} "
                    "仅列出端口通配形态而未列裸 host，"
                    "无端口 Host 头的请求不匹配通配条目会被 SDK 以 421 拒绝，"
                    "建议同时列出裸 host 形态",
                )
            )

    def _validate_request_state_keys(self) -> None:
        """校验 requestState 密钥环的单钥字节数下限与重复键。

        Raises:
            SeedreamConfigError: 任一密钥解码后不足 32 字节，或与在先密钥重复。
        """
        keys = self.request_state_secret_keys
        if keys is None:
            return
        seen: set[bytes] = set()
        for index, key in enumerate(keys):
            if len(key) < _REQUEST_STATE_KEY_MIN_BYTES:
                raise SeedreamConfigError(
                    f"request_state_secret_keys 第 {index} 个密钥解码后仅 {len(key)} 字节，"
                    f"每键须为解码后不少于 {_REQUEST_STATE_KEY_MIN_BYTES} 字节的十六进制串；"
                    f"生成命令: {_REQUEST_STATE_KEYGEN_COMMAND}"
                    f"{_env_var_suffix('request_state_secret_keys')}"
                )
            if key in seen:
                raise SeedreamConfigError(
                    f"request_state_secret_keys 第 {index} 个密钥与在先密钥重复，"
                    f"轮换环内同一密钥只需登记一次"
                    f"{_env_var_suffix('request_state_secret_keys')}"
                )
            seen.add(key)

    def _validate_dir_field(self, value: str, field_name: str) -> None:
        """校验给定路径指向有效目录，存在但非目录时抛 SeedreamConfigError。

        不要求目录预先存在，未创建的目录可通过校验以便按需创建。
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
        """从环境变量与 .env 文件构建配置实例，构建过程线程安全。

        Raises:
            SeedreamConfigError: 配置文件不可读或配置项校验失败。
        """
        return build_config_from_sources(env_file=env_file)

    def to_dict(self) -> dict[str, Any]:
        """导出为字典，名称命中敏感关键词的字段以 "***" 脱敏。"""
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


# dataclass 字段名到环境变量名的映射，从各字段的 env 元数据反射派生；新增字段在
# _env_field 声明中登记环境变量名后进入本映射，取值辅助须另行登记 _FIELD_PICKERS。
_FIELD_ENV_MAP: dict[str, str] = {
    f.name: f.metadata[_ENV_METADATA_KEY]
    for f in fields(SeedreamConfig)
    if _ENV_METADATA_KEY in f.metadata
}

# api_key 必填无默认值，不经 _env_field 登记，环境变量名在此显式列出。
_NON_METADATA_FIELD_ENV: dict[str, str] = {"api_key": "ARK_API_KEY"}


def _env_var_suffix(*field_names: str) -> str:
    """反查字段对应的环境变量名，生成校验错误消息的变量名提示后缀。

    跨字段约束可传入多个字段名，斜杠连接各自的变量名；无法反查的字段名跳过，
    全部不可反查时返回空串。
    """
    env_names: list[str] = []
    for name in field_names:
        env_name = _FIELD_ENV_MAP.get(name) or _NON_METADATA_FIELD_ENV.get(name)
        if env_name:
            env_names.append(env_name)
    if not env_names:
        return ""
    return f"（环境变量 {'/'.join(env_names)}）"


def _ensure_field_utf8_encodable(value: str, field_name: str) -> None:
    """校验配置字符串可编码为 UTF-8，孤立代理字符在构造期即拒绝。"""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SeedreamConfigError(
            f"{field_name}包含无法编码的字符{_env_var_suffix(field_name)}"
        ) from exc


def _decompose_allowed_host_entry(entry: str) -> tuple[str, str] | None:
    """拆分 Host 允许列表条目为 host 与端口后缀，无法识别的形态返回 None。

    端口后缀为空串、":<1-65535 的 ASCII 数字>" 或 ":*"；host 为方括号 IPv6 字面量
    或不含冒号与通配符、首尾无点号的非空主机名。
    """
    if entry.startswith("["):
        end = entry.find("]")
        # end <= 1 覆盖未闭合与空内容两种畸形方括号形态。
        if end <= 1:
            return None
        host, suffix = entry[: end + 1], entry[end + 1 :]
    else:
        idx = entry.rfind(":")
        host, suffix = (entry, "") if idx == -1 else (entry[:idx], entry[idx:])
        if (
            not host
            or host.startswith(".")
            or host.endswith(".")
            or any(ch in host for ch in (":", "*", "[", "]"))
        ):
            return None
    if suffix in ("", ":*"):
        return host, suffix
    if suffix.startswith(":"):
        port_text = suffix[1:]
        # SDK 的 Host 校验为精确串比较，前导零端口虽数值合法但与客户端规范化
        # 形态不匹配，按无效条目拒绝。
        if port_text.isascii() and port_text.isdigit() and not port_text.startswith("0"):
            port = int(port_text)
            if 1 <= port <= 65535:
                return host, suffix
    return None


def _field_default_str(field_name: str) -> str:
    """反射 SeedreamConfig 字段默认值并转为环境变量字符串默认值。

    bool 转 true/false，None 与无默认值字段转空串，其余取 str。
    """
    for f in fields(SeedreamConfig):
        if f.name == field_name:
            default = f.default
            if default is MISSING:
                return ""
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
    """规范化模型选择器：忽略大小写将友好别名映射为完整 Model ID，未命中返回小写原值。"""
    normalized = str(value).strip().lower()
    return MODEL_ALIASES.get(normalized, normalized)


def parse_int(value: object) -> int:
    """将值解析为整数。

    Raises:
        SeedreamConfigError: 值为空、布尔或无法解析为整数。
    """
    if isinstance(value, bool):
        raise SeedreamConfigError(f"无法解析整数值: {value!r}")
    if isinstance(value, int):
        return value
    if value is None:
        raise SeedreamConfigError(f"无法解析整数值: {value}")

    normalized = str(value).strip()
    if not normalized:
        raise SeedreamConfigError(f"无法解析整数值: {value}")

    if not INT_TEXT_PATTERN.fullmatch(normalized):
        raise SeedreamConfigError(f"无法解析整数值: {value}")
    try:
        return int(normalized)
    except ValueError as exc:
        # 超长数字串超出解释器转换上限时同样归一为配置错误
        raise SeedreamConfigError(f"无法解析整数值: {value}") from exc


def _read_env_values(env_file: str | None) -> dict[str, str]:
    """读取 .env 文件键值为字典，不写入进程环境变量。

    显式传入 env_file 时只读取该文件；未提供时按项目根 .env 与当前工作目录
    .env 合并读取，当前工作目录覆盖项目根。
    """

    def _load_single_env_file(path: Path) -> dict[str, str]:
        try:
            values = dotenv_values(path)
        except OSError as exc:
            # 读取失败统一包装为含路径与原因的配置错误，经 cli_main 优雅错误路径输出。
            raise SeedreamConfigError(f"配置文件不可读: {path} -> {exc}") from exc
        except UnicodeDecodeError as exc:
            raise SeedreamConfigError(f"配置文件编码错误: {path} 需为 UTF-8 编码 -> {exc}") from exc
        return {k: str(v) for k, v in values.items() if v is not None}

    def _prepare_env_path(raw: str | Path) -> Path:
        # 路径准备与读取同口径包装：被删 CWD 抛 OSError，HOME 剥离容器对 ~ 抛 RuntimeError。
        try:
            return Path(raw).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise SeedreamConfigError(f"配置文件不可读: {raw} -> {exc}") from exc

    if env_file:
        env_path = _prepare_env_path(env_file)
        if not env_path.is_file():
            raise SeedreamConfigError(f"配置文件不存在: {env_path}")
        return _load_single_env_file(env_path)

    merged_values: dict[str, str] = {}
    default_env_path = _prepare_env_path(DEFAULT_ENV_FILE)
    runtime_env_path = _prepare_env_path(".env")

    if default_env_path.is_file():
        merged_values.update(_load_single_env_file(default_env_path))

    if runtime_env_path.is_file() and runtime_env_path != default_env_path:
        merged_values.update(_load_single_env_file(runtime_env_path))
        if default_env_path.is_file():
            _pending_build_warnings.append(
                (
                    "ERROR",
                    f"当前工作目录 .env（{runtime_env_path}）覆盖了项目根 .env"
                    f"（{default_env_path}）的配置值；"
                    "进程工作目录不受控时其中的 .env 可能注入非预期配置，请确认启动目录可信",
                )
            )

    return merged_values


def _value_is_set(value: object) -> bool:
    """判定取值是否视为已设置：字符串 strip 后非空，其余类型仅排除 None。"""
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
    """按优先级选取配置值：overrides > 系统环境变量 > env 文件 > 默认值。

    各层统一以 _value_is_set 判空，空白字符串视为未设置而穿透到下一层。
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


# 类型化取值辅助：经 _pick_config_value 按优先级取值后再做类型转换。
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


def _pick_optional_str_tuple(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> tuple[str, ...] | None:
    """按优先级取值后按逗号拆分为去空白条目元组，空值归 None 表示未配置。

    逐项 strip 并丢弃空条目，全部条目为空时同样归 None。序列形态的 override
    按元素取值，不经 str() 把整个容器拼成畸形条目。
    """
    raw = _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key])
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        entries = [str(entry).strip() for entry in raw]
    else:
        normalized = str(raw).strip()
        if not normalized:
            return None
        entries = [entry.strip() for entry in normalized.split(",")]
    valid_entries = [entry for entry in entries if entry]
    return tuple(valid_entries) or None


def _pick_request_state_key_bytes(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> tuple[bytes, ...] | None:
    """按优先级取值后按逗号拆分并逐条 hex 解码为密钥字节，空值归 None。

    条目归一复用 _pick_optional_str_tuple，序号按有效条目计数；解码失败的
    错误消息给出格式要求与生成命令提示，不回显密钥内容；解码后单键字节数
    下限与重复键由 validate 校验。

    Raises:
        SeedreamConfigError: 任一条目无法以十六进制解码。
    """
    entries = _pick_optional_str_tuple(overrides, field_name, env_key, env_values)
    if entries is None:
        return None
    material: list[bytes] = []
    for effective_index, entry in enumerate(entries, start=1):
        try:
            material.append(bytes.fromhex(entry))
        except ValueError as exc:
            raise SeedreamConfigError(
                f"request_state_secret_keys 第 {effective_index} 个条目不是合法的十六进制密钥，"
                f"每键须为解码后不少于 {_REQUEST_STATE_KEY_MIN_BYTES} 字节的十六进制串；"
                f"生成命令: {_REQUEST_STATE_KEYGEN_COMMAND}"
                f"{_env_var_suffix(field_name)}"
            ) from exc
    return tuple(material) or None


def _parse_int_with_env_hint(value: object, field_name: str) -> int:
    """parse_int 的字段级包装，解析失败的消息附带该字段环境变量名提示。

    parse_int 仅接收取值、不知字段来源，环境变量名提示统一在取值层补充。
    """
    try:
        return parse_int(value)
    except SeedreamConfigError as exc:
        raise SeedreamConfigError(f"{exc.message}{_env_var_suffix(field_name)}") from exc


def _pick_int(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> int:
    return _parse_int_with_env_hint(
        _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key]),
        field_name,
    )


def _pick_optional_int(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> int | None:
    raw = _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key])
    if raw is None or not str(raw).strip():
        return None
    return _parse_int_with_env_hint(raw, field_name)


def _parse_bool_with_env_hint(value: object, field_name: str) -> bool:
    """parse_bool 的字段级包装，解析失败的消息附带该字段环境变量名提示。"""
    try:
        return parse_bool(value)
    except SeedreamConfigError as exc:
        raise SeedreamConfigError(f"{exc.message}{_env_var_suffix(field_name)}") from exc


def _pick_bool(
    overrides: Mapping[str, object], field_name: str, env_key: str, env_values: Mapping[str, str]
) -> bool:
    return _parse_bool_with_env_hint(
        _pick_config_value(overrides, field_name, env_key, env_values, ENV_DEFAULTS[env_key]),
        field_name,
    )


# 取值辅助的统一签名：接收 overrides 映射、override 键名、环境变量键名与 env 文件值，
# 返回具体字段类型的取值。
_ConfigValuePicker = Callable[[Mapping[str, object], str, str, Mapping[str, str]], object]

# 配置字段的声明式取值表：字段到取值辅助与 override 键名覆盖的映射，覆盖为 None 时
# override 键名即字段名；model_id 与 default_watermark 的 override 键名是 CLI 简称的
# 有意命名间接映射，别名展开统一由 validate 完成，构建侧不重复规范化。构建循环遍历
# _FIELD_ENV_MAP 并索引本表，新增 env metadata 字段漏登记时以 KeyError 在构建期暴露。
_FIELD_PICKERS: dict[str, tuple[_ConfigValuePicker, str | None]] = {
    "base_url": (_pick_str, None),
    "allow_http_base_url": (_pick_bool, None),
    "model_id": (_pick_str, "model"),
    "default_size": (_pick_str, None),
    "default_watermark": (_pick_bool, "watermark"),
    "timeout": (_pick_int, None),
    "api_timeout": (_pick_int, None),
    "max_retries": (_pick_int, None),
    "generate_concurrency": (_pick_int, None),
    "log_level": (_pick_str, None),
    "log_rotation_size": (_pick_int, None),
    "log_retention_days": (_pick_int, None),
    "auto_save_enabled": (_pick_bool, None),
    "data_root": (_pick_optional_str, None),
    "auto_save_download_timeout": (_pick_int, None),
    "auto_save_max_retries": (_pick_int, None),
    "auto_save_max_file_size": (_pick_int, None),
    "auto_save_max_concurrent": (_pick_int, None),
    "auto_save_date_folder": (_pick_bool, None),
    "auto_save_cleanup_days": (_pick_int, None),
    "auto_save_max_total_bytes": (_pick_optional_int, None),
    "auto_save_fsync": (_pick_bool, None),
    "stream_buffer_max_size": (_pick_int, None),
    "stream_chunk_size": (_pick_int, None),
    "sse_event_max_size": (_pick_optional_int, None),
    "response_body_limit": (_pick_optional_int, None),
    "image_prepare_concurrency": (_pick_int, None),
    "prepare_cache_max": (_pick_int, None),
    "prepare_cache_max_bytes": (_pick_int, None),
    "preview_enabled": (_pick_bool, None),
    "workspace_root": (_pick_optional_str, None),
    "http_auth_token": (_pick_optional_str, None),
    "http_max_body_size": (_pick_int, None),
    "web_enabled": (_pick_bool, "web"),
    "http_allowed_hosts": (_pick_optional_str_tuple, None),
    "request_state_secret_keys": (_pick_request_state_key_bytes, None),
}

# 合法的 overrides 键集合：字段名、CLI 简称别名与 api_key，未知键在构建期告警。
_KNOWN_OVERRIDE_KEYS = frozenset(
    {"api_key", *_FIELD_PICKERS} | {alias for _, alias in _FIELD_PICKERS.values() if alias}
)


def build_config_from_sources(
    overrides: Mapping[str, object] | None = None,
    env_file: str | None = None,
) -> SeedreamConfig:
    """从统一来源构建配置对象，经 ``_config_build_lock`` 串行化，线程安全。

    Args:
        overrides: 调用方显式覆盖值，CLI 参数为典型来源。
        env_file: 可选 .env 文件路径，未提供时先读项目根 .env 再读当前工作目录
            .env，后者覆盖前者。

    Raises:
        SeedreamConfigError: 配置文件不可读、缺少 API 密钥或配置项校验失败。
    """
    with _config_build_lock:
        return _build_config_from_sources_unlocked(overrides, env_file)


def _build_config_from_sources_unlocked(
    overrides: Mapping[str, object] | None = None,
    env_file: str | None = None,
) -> SeedreamConfig:
    """构建配置对象但自身不加锁，由 :func:`build_config_from_sources` 持锁调用。"""
    pending_mark = len(_pending_build_warnings)
    try:
        return _build_config_unlocked_body(overrides, env_file)
    except BaseException:
        # 构建失败的告警不外泄给下一次成功构建的 drain
        del _pending_build_warnings[pending_mark:]
        raise


def _build_config_unlocked_body(
    overrides: Mapping[str, object] | None,
    env_file: str | None,
) -> SeedreamConfig:
    """构建配置对象的主体，告警回滚由调用方负责。"""
    override_values = dict(overrides or {})
    unknown_keys = sorted(set(override_values) - _KNOWN_OVERRIDE_KEYS)
    if unknown_keys:
        _pending_build_warnings.append(
            (
                "WARNING",
                f"配置覆盖包含未知键，已忽略: {', '.join(unknown_keys)}",
            )
        )
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
        raise SeedreamConfigError(
            "未找到ARK_API_KEY环境变量或配置文件值，也可通过 --api-key 参数提供。"
        )

    config_kwargs: dict[str, Any] = {"api_key": api_key}
    for field_name, env_key in _FIELD_ENV_MAP.items():
        picker, override_key = _FIELD_PICKERS[field_name]
        config_kwargs[field_name] = picker(
            override_values, override_key or field_name, env_key, env_values
        )
    return SeedreamConfig(**config_kwargs)


# 配置构建串行化锁：保护 .env 读取与配置构建，避免并发构建竞态。
_config_build_lock = threading.Lock()
# 全局配置惰性初始化锁；与 _config_build_lock 分离，避免嵌套获取造成死锁。
_global_config_lock = threading.Lock()

_global_config: SeedreamConfig | None = None
# CLI 注入的活动配置，优先于 _global_config。
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


def get_active_config() -> SeedreamConfig:
    """获取活动配置：CLI 注入的活动配置优先，回退全局默认实例。"""
    if _active_config is not None:
        return _active_config
    return get_global_config()


def set_active_config(config: SeedreamConfig | None) -> None:
    """设置或清除 CLI 注入的活动配置，None 表示清除后回退全局默认。

    变更同时使 io_path 的回退根缓存失效。
    """
    global _active_config
    with _global_config_lock:
        _active_config = config
        clear_resolved_env_root_cache()


def active_request_state_keys() -> tuple[bytes, ...] | None:
    """向 resources 提供 requestState 密钥环的活动配置取值。

    活动配置就绪时返回其 request_state_secret_keys；配置构建失败或读取抛
    OSError 时返回 None，保持 SDK 默认的进程临时密钥，模块导入不因缺配置而
    中断，真正的配置错误由启动路径报告。

    Returns:
        解码后的密钥字节元组，未配置时为 None。
    """
    try:
        config = get_active_config()
    except (SeedreamConfigError, OSError):
        return None
    return config.request_state_secret_keys


def _make_env_location_provider(attr: str, env_name: str) -> Callable[[], str | None]:
    """构造位置声明的配置提供者：活动配置属性优先，配置缺失回退同名环境变量。"""

    def provider() -> str | None:
        try:
            config = get_active_config()
        except (SeedreamConfigError, OSError):
            config = None
        if config is not None:
            value: str | None = getattr(config, attr)
            return value.strip() if value else None
        env_value = os.getenv(env_name)
        return env_value.strip() if env_value else None

    return provider


# 模块加载即注册，io_path 的位置求值经提供者取活动配置值。
register_env_workspace_root_provider(
    _make_env_location_provider("workspace_root", "SEEDREAM_WORKSPACE_ROOT")
)
register_data_root_provider(_make_env_location_provider("data_root", "SEEDREAM_DATA_ROOT"))
