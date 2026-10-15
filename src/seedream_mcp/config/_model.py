"""Seedream MCP 配置模型：SeedreamConfig 数据类与构造期校验。

定义配置字段、环境变量绑定与校验规范化，优先级为运行时覆盖 > 系统环境变量 >
.env 文件 > 代码默认值；构建编排、全局单例与环境变量提供者注册位于 _builder，
.env 读取与类型化取值机械位于 sources。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .host_syntax import (
    _bracket_ipv6_literal,
    _decompose_allowed_host_entry,
    _ipv6_host_literal_forms,
    _is_valid_url_hostname,
)
from .sources import (
    _REQUEST_STATE_KEYGEN_COMMAND,
    _REQUEST_STATE_KEY_MIN_BYTES,
    _ensure_field_utf8_encodable,
    _env_field,
    _env_var_suffix,
    _http_auth_token_invalid_reason,
    normalize_model_selector,
)
from ..utils.core.errors import SeedreamConfigError, SeedreamValidationError
from ..utils.core.sanitizers import is_printable_ascii, is_sensitive_key
from ..utils.core.formats import DEFAULT_MAX_FILE_SIZE
from ..utils.core.logs import (
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_LOG_ROTATION_SIZE_MB,
    LEGAL_LOG_LEVELS,
)
from ..utils.core.validators import validate_size_for_model
from ..utils.io.path import reject_unc_declaration
from ..utils.model.capabilities import MODEL_ALIASES, DEPRECATED_MODEL_TOKENS

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
# SSE 单事件阈值的推导常量：base64 最坏膨胀系数与事件信封余量，n 字节图片经
# 编码最长 4*ceil(n/3) 字符，叠加 data: 前缀等信封开销。
_B64_WORST_CASE_NUMERATOR = 4
_SSE_EVENT_ENVELOPE_MARGIN = 4 * 1024


def _validate_ipv6_host_literal(literal: str, entry: str, label: str, env_key: str) -> None:
    """非法或既非压缩规范形也非 IPv4 映射 dotted 形的 IPv6 字面量抛配置错误。"""
    forms = _ipv6_host_literal_forms(literal)
    if forms is None:
        raise SeedreamConfigError(f"{label}须为合法 IPv6 字面量: {entry}{_env_var_suffix(env_key)}")
    accepted_literals, suggested_literal = forms
    if literal not in accepted_literals:
        raise SeedreamConfigError(
            f"{label}须为压缩规范形或 IPv4 映射 dotted 形，"
            f"应写 [{suggested_literal}] 而非 {entry}{_env_var_suffix(env_key)}"
        )


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
        sse_event_max_size: 单个 SSE 事件的截断阈值字节数；None 时按缓冲区上限与
            单图 base64 最坏展开二者的较大值推导。
        response_body_limit: 上游响应体读取总量上限字节数，三条读取路径共用；None 时
            按 auto_save_max_file_size × 20 推导。
        image_prepare_concurrency: 参考图预处理并发上限。
        prepare_cache_max: 参考图预处理结果 LRU 缓存的条目数上限。
        prepare_cache_max_bytes: 参考图预处理结果缓存的累计字节上限。
        preview_enabled: 是否在生成工具结果中附带已保存图片的缩略图预览，默认关闭；
            客户端能将 ImageContent 送达模型视觉层时开启，长边不超过 768 像素；
            依赖自动保存，关闭或保存失败时仅返回文本与 structuredContent。
        workspace_root: 无 MCP Roots 时本地文件访问边界的回退目录。
        http_auth_token: streamable-http 传输的 Bearer 鉴权令牌；配置时长度不得
            少于 HTTP_AUTH_TOKEN_MIN_LENGTH 字符且须为可打印 ASCII。
        http_max_body_size: streamable-http 请求体大小上限字节数，默认 64MB。
        body_read_timeout: 读体窗口的超时秒数，未申报（chunked 等）与大请求体统一限时，
            超窗回 408 断连并归还并发准入许可，防慢速上传钉死许可与连接。
        large_body_admission_concurrency: 大请求体并发准入的许可数，约束同时在读取
            的大请求体个数，申报超阈值与未申报累计越阈的请求均占许可，
            与生成 API 并发解耦。
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
    sse_event_max_size: int | None = _env_field(None, "SEEDREAM_SSE_EVENT_MAX_SIZE")

    response_body_limit: int | None = _env_field(None, "SEEDREAM_RESPONSE_BODY_LIMIT")

    image_prepare_concurrency: int = _env_field(5, "SEEDREAM_IMAGE_PREPARE_CONCURRENCY")

    prepare_cache_max: int = _env_field(32, "SEEDREAM_PREPARE_CACHE_MAX")

    prepare_cache_max_bytes: int = _env_field(256 * 1024 * 1024, "SEEDREAM_PREPARE_CACHE_MAX_BYTES")

    preview_enabled: bool = _env_field(False, "SEEDREAM_PREVIEW_ENABLED")

    workspace_root: str | None = _env_field(None, "SEEDREAM_WORKSPACE_ROOT")
    http_auth_token: str | None = _env_field(None, "SEEDREAM_HTTP_AUTH_TOKEN")
    http_max_body_size: int = _env_field(64 * 1024 * 1024, "SEEDREAM_HTTP_MAX_BODY_SIZE")
    body_read_timeout: int = _env_field(300, "SEEDREAM_BODY_READ_TIMEOUT")
    large_body_admission_concurrency: int = _env_field(
        3, "SEEDREAM_LARGE_BODY_ADMISSION_CONCURRENCY"
    )
    web_enabled: bool = _env_field(False, "SEEDREAM_WEB_ENABLED")
    http_allowed_hosts: tuple[str, ...] | None = _env_field(None, "SEEDREAM_HTTP_ALLOWED_HOSTS")
    http_allowed_origins: tuple[str, ...] | None = _env_field(None, "SEEDREAM_HTTP_ALLOWED_ORIGINS")
    request_state_secret_keys: tuple[bytes, ...] | None = _env_field(
        None, "SEEDREAM_REQUEST_STATE_KEYS"
    )
    # validate 收集的构建期告警；builder 路径汇入全局经 drain 输出，直接构造留在实例。
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
        """校验 api_key 非空、非默认占位符且为可打印 ASCII。"""
        # 与 model_id 等字段同口径回写 strip，避免空白密钥产出畸形 Bearer 头。
        stripped = (self.api_key or "").strip()
        object.__setattr__(self, "api_key", stripped)
        if not self.api_key:
            raise SeedreamConfigError(f"API密钥不能为空{_env_var_suffix('api_key')}")
        _ensure_field_utf8_encodable(self.api_key, "api_key")
        # 密钥含控制字符、空白或非 ASCII 字符必为无效输入，留到请求期只以头值
        # 非法或编码失败等晦涩错误暴露。
        if not is_printable_ascii(self.api_key):
            raise SeedreamConfigError(f"API密钥须为可打印ASCII字符{_env_var_suffix('api_key')}")
        if self.api_key == "your_api_key_here":
            raise SeedreamConfigError(
                f"请设置有效的API密钥，不能使用默认占位符{_env_var_suffix('api_key')}"
            )

    def _validate_api_endpoint(self) -> None:
        """校验 base_url 的 scheme、主机名、userinfo 与 http 明文豁免。"""
        # RFC 3986 规定 scheme 大小写不敏感，HTTPS:// 等大写形态经 urlparse 取小写后判定。
        invalid_url_message = f"base_url必须是有效的HTTP/HTTPS URL{_env_var_suffix('base_url')}"
        try:
            parsed_base_url = urlparse(self.base_url)
        except ValueError as exc:
            # 括号畸形 IPv6 等形态使 urlparse 抛 ValueError，统一归为配置错误。
            raise SeedreamConfigError(invalid_url_message) from exc
        _ensure_field_utf8_encodable(self.base_url, "base_url")
        base_url_scheme = parsed_base_url.scheme.lower()
        if not self.base_url or base_url_scheme not in ("http", "https"):
            raise SeedreamConfigError(invalid_url_message)
        # 主机名缺失在构造期拒绝，含 https://:8443 一类 netloc 非空形态，避免
        # 运行期才以网络错误档失败；空端口形态主机名非空，不受影响。
        base_url_hostname = parsed_base_url.hostname or ""
        if not base_url_hostname.strip():
            raise SeedreamConfigError(f"base_url缺少主机名{_env_var_suffix('base_url')}")
        # 字符集非法的主机名（空格、控制字符、非 ASCII、乱码百分号等）同样延到
        # 运行期才失败，与缺失同口径在构造期拒绝。
        if not _is_valid_url_hostname(base_url_hostname):
            raise SeedreamConfigError(
                f"base_url主机名含非法字符: {base_url_hostname}{_env_var_suffix('base_url')}"
            )
        # 非数字或越界端口在 .port 求值即抛 ValueError，归拢到配置构建期拒绝。
        try:
            base_url_port = parsed_base_url.port
        except ValueError as exc:
            raise SeedreamConfigError(
                f"base_url端口无效: {exc}{_env_var_suffix('base_url')}"
            ) from exc
        if base_url_port == 0:
            raise SeedreamConfigError(f"base_url端口不得为 0{_env_var_suffix('base_url')}")
        if parsed_base_url.query or parsed_base_url.fragment:
            raise SeedreamConfigError(
                f"base_url不能包含查询参数或片段{_env_var_suffix('base_url')}"
            )
        # userinfo 会被 httpx 转为 URL Basic 认证并静默覆盖 Bearer 密钥头。
        if parsed_base_url.username is not None or parsed_base_url.password is not None:
            raise SeedreamConfigError(
                f"base_url不能包含用户名密码等 userinfo{_env_var_suffix('base_url')}"
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
        """校验 default_size 为非空字符串并按模型能力标准化。"""
        if not isinstance(self.default_size, str):
            raise SeedreamConfigError(f"default_size必须为字符串{_env_var_suffix('default_size')}")
        if not self.default_size.strip():
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
        """校验流式缓冲与响应体读取上限。"""
        if self.stream_buffer_max_size <= 0:
            raise SeedreamConfigError(
                f"stream_buffer_max_size必须大于0{_env_var_suffix('stream_buffer_max_size')}"
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
        """校验 streamable-http 鉴权令牌长度与字符集、请求体下限、读体窗口超时、大请求体并发准入与 Host/Origin 允许列表。"""
        # 与 api_key 同口径回写 strip，直构路径的空白尾符不致误判为非法字符集。
        object.__setattr__(self, "http_auth_token", (self.http_auth_token or "").strip() or None)
        invalid_reason = (
            _http_auth_token_invalid_reason(self.http_auth_token, HTTP_AUTH_TOKEN_MIN_LENGTH)
            if self.http_auth_token
            else None
        )
        if invalid_reason == "length":
            raise SeedreamConfigError(
                f"http_auth_token 长度不得少于 {HTTP_AUTH_TOKEN_MIN_LENGTH} 字符"
                f"{_env_var_suffix('http_auth_token')}"
            )
        if invalid_reason == "charset":
            raise SeedreamConfigError(
                "http_auth_token 须为可打印ASCII字符" f"{_env_var_suffix('http_auth_token')}"
            )
        if self.http_max_body_size < _HTTP_MAX_BODY_SIZE_FLOOR:
            raise SeedreamConfigError(
                f"http_max_body_size 不能低于 1MB（{_HTTP_MAX_BODY_SIZE_FLOOR} 字节）"
                f"{_env_var_suffix('http_max_body_size')}"
            )
        if self.body_read_timeout <= 0:
            raise SeedreamConfigError(
                f"body_read_timeout必须大于0{_env_var_suffix('body_read_timeout')}"
            )
        if self.large_body_admission_concurrency < 1:
            raise SeedreamConfigError(
                "large_body_admission_concurrency必须不小于1"
                f"{_env_var_suffix('large_body_admission_concurrency')}"
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
                _validate_ipv6_host_literal(
                    literal,
                    entry,
                    "http_allowed_hosts 方括号条目",
                    "http_allowed_hosts",
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
                query/fragment/userinfo（含空 userinfo）、主机为空、带尾点、含
                空标签、含百分号编码或 zone-id、非压缩规范形且非 IPv4 映射
                dotted 形的 IPv6 主机、含通配、含大写、含非 ASCII 字符或端口非数字。
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
            # 匹配为字面精确比较，死配置构建期拒绝：空主机、空标签、百分号编码、
            # 通配、query、fragment、userinfo、尾点与非压缩 IPv6 主机都永不匹配
            # 真实 Origin 头。
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
            # 前导点与连续点构成空标签主机，不是任何 Origin 头的合法序列化形态。
            if hostname.startswith(".") or ".." in hostname:
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目主机不得包含前导点或连续点: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # 浏览器把百分号编码主机解码后发送，含 % 的条目永不匹配真实 Origin 头。
            if "%" in hostname:
                raise SeedreamConfigError(
                    f"http_allowed_origins 条目主机不得包含百分号编码或 zone-id: {entry}"
                    f"{_env_var_suffix('http_allowed_origins')}"
                )
            # 方括号 IPv6 主机与 hosts 侧同口径，浏览器 Origin 序列化恒为压缩规范形。
            if ":" in hostname:
                _validate_ipv6_host_literal(
                    hostname,
                    entry,
                    "http_allowed_origins 条目主机",
                    "http_allowed_origins",
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
        message = _first_invalid_request_state_key_message(keys)
        if message is not None:
            raise SeedreamConfigError(message)

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
        # 函数级延迟导入破 _model 与 _builder 的模块环（D3 白名单登记）。
        from ._builder import build_config_from_sources

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


def _first_invalid_request_state_key_message(
    keys: tuple[bytes, ...],
) -> str | None:
    """返回密钥环内首个不合规键的报错消息，全部合规时为 None。

    同键既短又重复时按短键计；导入期预检共用本判定，环规则单源维护。
    """
    seen: set[bytes] = set()
    for index, key in enumerate(keys, start=1):
        if len(key) < _REQUEST_STATE_KEY_MIN_BYTES:
            return (
                f"request_state_secret_keys 第 {index} 个密钥解码后仅 {len(key)} 字节，"
                f"每键须为解码后不少于 {_REQUEST_STATE_KEY_MIN_BYTES} 字节的十六进制串；"
                f"生成命令: {_REQUEST_STATE_KEYGEN_COMMAND}"
                f"{_env_var_suffix('request_state_secret_keys')}"
            )
        if key in seen:
            return (
                f"request_state_secret_keys 第 {index} 个密钥与在先密钥重复，"
                f"轮换环内同一密钥只需登记一次"
                f"{_env_var_suffix('request_state_secret_keys')}"
            )
        seen.add(key)
    return None
