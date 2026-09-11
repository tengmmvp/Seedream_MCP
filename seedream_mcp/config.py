"""Seedream MCP 工具配置管理模块。

定义 SeedreamConfig 配置数据类与校验、全局单例与环境变量提供者注册，优先级为
运行时覆盖 > 系统环境变量 > .env 文件 > 代码默认值。配置构建不向 os.environ
注入 .env 值，仅写入配置对象，避免全局状态污染；.env 读取与类型化取值机械位于
_config_sources 模块。
"""

from __future__ import annotations

import ipaddress
import os
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from ._config_sources import (
    # as 同名形态为显式再导出，resources 的复位协议经 config 模块属性访问。
    _BUILD_WARNINGS as _BUILD_WARNINGS,
    _FIELD_ENV_MAP,
    _bracket_ipv6_literal,
    _FIELD_PICKERS,
    _KNOWN_OVERRIDE_KEYS,
    _REQUEST_STATE_KEYGEN_COMMAND,
    _REQUEST_STATE_KEY_MIN_BYTES,
    _decompose_allowed_host_entry,
    _ensure_field_utf8_encodable,
    _env_field,
    _env_var_suffix,
    _pick_config_value,
    _read_env_values,
    init_env_registry,
    normalize_model_selector,
)
from .utils.core.errors import SeedreamConfigError, SeedreamValidationError
from .utils.core.sanitizers import is_sensitive_key
from .utils.core.formats import DEFAULT_MAX_FILE_SIZE
from .utils.core.logs import (
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_LOG_ROTATION_SIZE_MB,
    get_logger,
)
from .utils.core.validators import validate_size_for_model
from .utils.io.io_path import (
    clear_resolved_env_root_cache,
    register_data_root_provider,
    register_env_workspace_root_provider,
    reject_unc_declaration,
)
from .utils.model.model_capabilities import MODEL_ALIASES, DEPRECATED_MODEL_TOKENS


def drain_pending_build_warnings() -> None:
    """输出并清空构建期收集的告警。

    server 在日志系统就绪后调用；不经 server 入口的嵌入式调用方需自行调用本函数，
    否则收集中的告警不输出。锁内原子取走与并发构建互斥，半次构建的告警不被提前
    冲刷，日志输出在锁外执行。
    """
    with _config_build_lock:
        pending = _BUILD_WARNINGS.take_all()
    logger = get_logger()
    for level, message in pending:
        logger.log(level, message)


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
# SSE 读取块的下限字节数：流首 UTF-8 BOM 为 3 字节且按读取块整体判定剥离，不可跨块。
_STREAM_CHUNK_SIZE_MIN_BYTES = 3
# SSE 单事件阈值的推导常量：base64 最坏膨胀系数与事件信封余量，n 字节图片经
# 编码最长 4*ceil(n/3) 字符，叠加 data 前缀等信封开销。
_B64_WORST_CASE_NUMERATOR = 4
_SSE_EVENT_ENVELOPE_MARGIN = 4 * 1024


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
        http_allowed_origins: 跨源浏览器客户端的 Origin 允许列表，条目为精确
            origin（scheme://host[:port]，不支持端口通配）；配置后 /mcp 挂 CORS
            层应答预检并向 SDK 内层校验放行列表内 Origin。仅经
            SEEDREAM_HTTP_ALLOWED_ORIGINS 环境变量解析，CLI 不暴露参数。
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
    http_allowed_origins: tuple[str, ...] | None = _env_field(None, "SEEDREAM_HTTP_ALLOWED_ORIGINS")
    request_state_secret_keys: tuple[bytes, ...] | None = _env_field(
        None, "SEEDREAM_REQUEST_STATE_KEYS"
    )
    # validate 收集的构建期告警；builder 路径汇入全局经 drain 输出，直接构造留在实例
    _build_warnings: list[tuple[str, str]] = field(
        init=False, repr=False, compare=False, default_factory=list
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
        # 与 model_id 等字段同口径回写 strip，避免空白密钥产出畸形 Bearer 头。
        stripped = (self.api_key or "").strip()
        object.__setattr__(self, "api_key", stripped)
        if not self.api_key:
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
            self._build_warnings.append(
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
        self._validate_http_allowed_origins()

    def _validate_http_allowed_hosts(self) -> None:
        """校验 http_allowed_hosts 条目形态，端口通配未配套裸 host 时告警。

        Raises:
            SeedreamConfigError: 条目含 scheme/斜杠、含大写、非尾部通配、端口
                非数字，或方括号内容为非法、带 zone-id，或既非压缩规范形也非
                IPv4 映射 dotted 形的 IPv6 字面量。
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
            # Host 头按 RFC 为 ASCII，IDN 主机以 punycode 传输，非 ASCII 条目永不
            # 匹配真实 Host 头。
            if not entry.isascii():
                raise SeedreamConfigError(
                    f"http_allowed_hosts 条目须为 ASCII 字符: {entry}"
                    f"{_env_var_suffix('http_allowed_hosts')}"
                )
            # Host 头传输恒为小写，SDK 校验为字节级精确比较，大写条目永不匹配。
            if entry != entry.lower():
                raise SeedreamConfigError(
                    f"http_allowed_hosts 条目须全小写: {entry}"
                    f"{_env_var_suffix('http_allowed_hosts')}"
                )
            decomposed = _decompose_allowed_host_entry(entry)
            if decomposed is None:
                raise SeedreamConfigError(
                    f"http_allowed_hosts 条目仅支持 host、host:port、host:* 形态: {entry}"
                    f"{_env_var_suffix('http_allowed_hosts')}"
                )
            host_part, port_part = decomposed
            # 方括号条目按 IPv6 字面量校验，畸形内容运行期永不匹配真实 Host 头。
            literal = _bracket_ipv6_literal(host_part)
            if literal is not None:
                # Host 头按 RFC 6874 须以 %25 编码 zone-id，裸 % 条目永不匹配。
                if "%" in literal:
                    raise SeedreamConfigError(
                        f"http_allowed_hosts 方括号条目不得携带 zone-id: {entry}"
                        f"{_env_var_suffix('http_allowed_hosts')}"
                    )
                try:
                    address = ipaddress.IPv6Address(literal)
                except ValueError as exc:
                    raise SeedreamConfigError(
                        f"http_allowed_hosts 方括号条目须为合法 IPv6 字面量: {entry}"
                        f"{_env_var_suffix('http_allowed_hosts')}"
                    ) from exc
                # 真实 Host 头只携带压缩规范形；IPv4 映射地址客户端发送 dotted 形
                # （Host: [::ffff:192.0.2.1]:port），两种形态均放行。
                ipv4_mapped = address.ipv4_mapped
                accepted = [address.compressed]
                if ipv4_mapped is not None:
                    accepted.append(f"::ffff:{ipv4_mapped}")
                if literal not in accepted:
                    suggested = (
                        f"[::ffff:{ipv4_mapped}]"
                        if ipv4_mapped is not None
                        else f"[{address.compressed}]"
                    )
                    raise SeedreamConfigError(
                        f"http_allowed_hosts 方括号条目须为压缩规范形或 IPv4 映射 dotted 形，"
                        f"应写 {suggested} 而非 {entry}"
                        f"{_env_var_suffix('http_allowed_hosts')}"
                    )
            if port_part == "":
                bare_hosts.add(host_part)
            elif port_part == ":*":
                wildcard_hosts.add(host_part)

        uncovered = wildcard_hosts - bare_hosts
        if uncovered:
            self._build_warnings.append(
                (
                    "ERROR",
                    f"http_allowed_hosts 中 {', '.join(sorted(uncovered))} "
                    "仅列出端口通配形态而未列裸 host，"
                    "无端口 Host 头的请求不匹配通配条目会被 SDK 以 421 拒绝，"
                    "建议同时列出裸 host 形态",
                )
            )

    def _validate_http_allowed_origins(self) -> None:
        """校验 http_allowed_origins 条目形态。

        Raises:
            SeedreamConfigError: 条目缺 scheme、含路径、URL 形态无效、含
                query/fragment/userinfo（含空 userinfo）、主机为空或带尾点、含通配、
                含大写、含非 ASCII 字符或端口非数字。
        """
        origins = self.http_allowed_origins
        if origins is None:
            return
        for entry in origins:
            if not entry.startswith(("http://", "https://")):
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目须以 http:// 或 https:// 开头: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # Origin 头无路径成分，写出路径即配置错误。
            if "/" in entry.split("://", 1)[1]:
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目不得包含路径: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # 匹配为字面精确比较，死配置构建期拒绝：空主机、通配、query、
            # fragment、userinfo 与尾点主机都永不匹配真实 Origin 头。
            try:
                parsed = urlparse(entry)
            except ValueError as exc:
                # 括号畸形 IPv6 等形态使 urlparse 抛 ValueError，统一归为配置错误。
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目 URL 形态无效: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                ) from exc
            # 空 userinfo（user@ 清理残留）的 username 为空串，按 is not None 判定。
            if parsed.query or parsed.fragment or parsed.username is not None:
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目不得包含 query、fragment 或 userinfo: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            hostname = parsed.hostname or ""
            if not hostname or hostname != hostname.rstrip("."):
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目主机为空或带尾点: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # 反斜杠不是任何 Origin 头的合法字符。
            if "\\" in entry:
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目不得包含反斜杠: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # 空端口（尾随冒号被 urlparse 归一为 None）永不匹配真实 Origin 头。
            if parsed.netloc.endswith(":"):
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目端口为空: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            if "*" in entry:
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目不支持通配，请写出精确 origin: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # CORS 与 SDK 内层为大小写敏感比较，浏览器 Origin 恒为小写，大写条目
            # 在部分层永不命中，构建期拒绝。
            if entry != entry.lower():
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目须全小写: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # 浏览器把非 ASCII 主机序列化为 ASCII 形态发送，非 ASCII 条目永不匹配
            # 真实 Origin 头。
            if not entry.isascii():
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目须为 ASCII 字符: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            try:
                port = parsed.port
            except ValueError:
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目端口须为数字: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            if port == 0:
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目端口不得为 0: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # 浏览器 Origin 序列化省略默认端口，默认端口与前导零端口条目永不
            # 匹配真实头。
            host_part = parsed.netloc.rsplit("]", 1)[-1]
            port_text = host_part.rsplit(":", 1)[-1] if ":" in host_part else ""
            if port_text and port_text != str(port):
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目端口不得含前导零: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            scheme = parsed.scheme.lower()
            if (scheme, port) in (("https", 443), ("http", 80)):
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目不得写出默认端口，请省略: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
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
        """导出为字典，名称命中敏感关键词的字段以 "***" 脱敏。

        init=False 的内部字段不属于配置面，不导出。
        """
        result: dict[str, Any] = {}
        for config_field in fields(self):
            if not config_field.init:
                continue
            value = getattr(self, config_field.name)
            if is_sensitive_key(config_field.name):
                result[config_field.name] = "***" if value is not None else None
            else:
                result[config_field.name] = value
        return result

    def __repr__(self) -> str:
        return (
            f"SeedreamConfig(api_key='***', base_url='{self.base_url}', model_id='{self.model_id}')"
        )


# 从配置类反射派生字段环境变量映射与默认值表，取值机械经此读取。
init_env_registry(SeedreamConfig)


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
    pending_mark = _BUILD_WARNINGS.mark()
    try:
        return _build_config_unlocked_body(overrides, env_file)
    except BaseException:
        # 构建失败的告警不外泄给下一次成功构建的 drain
        _BUILD_WARNINGS.rollback(pending_mark)
        raise


def _build_config_unlocked_body(
    overrides: Mapping[str, object] | None,
    env_file: str | None,
) -> SeedreamConfig:
    """构建配置对象的主体，告警回滚由调用方负责。"""
    override_values = dict(overrides or {})
    unknown_keys = sorted(set(override_values) - _KNOWN_OVERRIDE_KEYS)
    if unknown_keys:
        _BUILD_WARNINGS.append(
            "WARNING",
            f"配置覆盖包含未知键，已忽略: {', '.join(unknown_keys)}",
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
    config = SeedreamConfig(**config_kwargs)
    _BUILD_WARNINGS.extend(config._build_warnings)
    return config


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
    """向 resources 提供 requestState 密钥环的活动取值。

    活动配置已就绪时返回其密钥环；导入期配置未构建，仅按系统环境变量先行
    取密钥环，.env 中的值由启动路径构建后经 rebind 校正；不构建完整配置，
    构建告警缓冲不为其写入，drain 输出与启动期声明的配置来源一致。先行取值
    解码失败或未过强度与重复校验时按未配置处理，真正的配置错误由启动路径
    报告。

    Returns:
        解码后的密钥字节元组，未配置时为 None。
    """
    config = _active_config if _active_config is not None else _global_config
    if config is not None:
        return config.request_state_secret_keys
    raw = os.getenv("SEEDREAM_REQUEST_STATE_KEYS")
    if not raw or not raw.strip():
        return None
    entries = tuple(entry.strip() for entry in raw.split(",") if entry.strip())
    try:
        keys = tuple(bytes.fromhex(entry) for entry in entries)
    except ValueError:
        return None
    # 与配置构建同口径的强度与重复校验，不合法值按未配置处理，避免喂给 SDK
    # 在导入期崩溃。
    if any(len(key) < _REQUEST_STATE_KEY_MIN_BYTES for key in keys) or len(set(keys)) != len(keys):
        return None
    return keys or None


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
