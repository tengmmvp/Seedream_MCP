"""配置取值机械：.env 读取、多层优先级取值、类型化 picker 与字段注册表。"""

from __future__ import annotations

import os
from dataclasses import MISSING, field, fields
from pathlib import Path
from typing import Any, Callable, Mapping

from dotenv import dotenv_values

from .utils.core.errors import SeedreamConfigError
from .utils.core.logs import EarlyMessageBuffer
from .utils.core.validators import INT_TEXT_PATTERN, parse_bool
from .utils.model.model_capabilities import MODEL_ALIASES

# 构建期告警先收集、经 config.drain_pending_build_warnings 后置输出：配置构建早于
# setup_logging，即时输出会随日志系统重建被移出文件通道。元素为 (级别, 消息)，
# 级别取 loguru 注册名，部署风险类告警用 ERROR，高日志级别部署下不被过滤。
_BUILD_WARNINGS = EarlyMessageBuffer()

# 项目根 .env 层仅源码检出部署存在，pip 安装部署该层空转，CWD .env 层仍生效。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

# requestState 密钥环单钥的字节数下限，与 SDK RequestStateSecurity 的密钥强度要求一致。
_REQUEST_STATE_KEY_MIN_BYTES = 32
# 密钥环错误消息提示的密钥生成命令，生成一个解码后恰为 32 字节的十六进制密钥。
_REQUEST_STATE_KEYGEN_COMMAND = 'python -c "import secrets; print(secrets.token_hex(32))"'
_ENV_METADATA_KEY = "env"


def _env_field(default: Any, env_name: str) -> Any:
    """为 dataclass 字段绑定默认值与环境变量名，字段定义即两者映射的单一数据源。"""
    return field(default=default, metadata={_ENV_METADATA_KEY: env_name})


# api_key 必填无默认值，不经 _env_field 登记，环境变量名在此显式列出。
_NON_METADATA_FIELD_ENV: dict[str, str] = {"api_key": "ARK_API_KEY"}

# dataclass 字段名到环境变量名的映射，从各字段的 env 元数据反射派生；新增字段在
# _env_field 声明中登记环境变量名后进入本映射，取值辅助须另行登记 _FIELD_PICKERS。
# SeedreamConfig 定义完成后经 init_env_registry 填充。
_FIELD_ENV_MAP: dict[str, str] = {}


def init_env_registry(config_cls: type) -> None:
    """从配置类反射派生字段环境变量映射与默认值表，config 模块加载尾部调用。

    两个表原地填充，早于本调用建立的模块属性绑定同步生效。
    """
    _FIELD_ENV_MAP.clear()
    _FIELD_ENV_MAP.update(
        {
            f.name: f.metadata[_ENV_METADATA_KEY]
            for f in fields(config_cls)
            if _ENV_METADATA_KEY in f.metadata
        }
    )
    ENV_DEFAULTS.clear()
    ENV_DEFAULTS.update(
        {
            env_key: _field_default_str(config_cls, field_name)
            for field_name, env_key in _FIELD_ENV_MAP.items()
        }
    )


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


def _bracket_ipv6_literal(entry: str) -> str | None:
    """方括号 host 条目返回内部 IPv6 字面量，非方括号或未闭合形态返回 None。

    与 _decompose_allowed_host_entry 的方括号语法同居本模块，形态演化同点维护。
    """
    if not entry.startswith("["):
        return None
    end = entry.find("]")
    return entry[1:end] if end > 1 else None


def _decompose_allowed_host_entry(entry: str) -> tuple[str, str] | None:
    """拆分 Host 允许列表条目为 host 与端口后缀，无法识别的形态返回 None。

    端口后缀为空串、":<1-65535 的 ASCII 数字>" 或 ":*"；host 为方括号 IPv6 字面量
    或不含冒号与通配符、首尾无点号的非空主机名。
    """
    if entry.startswith("["):
        # 畸形方括号形态直接拒绝。
        literal = _bracket_ipv6_literal(entry)
        if literal is None:
            return None
        host, suffix = f"[{literal}]", entry[len(literal) + 2 :]
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


def _field_default_str(config_cls: type, field_name: str) -> str:
    """反射配置类字段默认值并转为环境变量字符串默认值。

    bool 转 true/false，None 与无默认值字段转空串，其余取 str。
    """
    for f in fields(config_cls):
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
# 供 _pick_* 系列辅助回退取值；经 init_env_registry 填充。
ENV_DEFAULTS: dict[str, str] = {}


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
            # utf-8-sig 剥离可能存在的 BOM，无 BOM 文件行为与 utf-8 一致。
            values = dotenv_values(path, encoding="utf-8-sig")
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
            _BUILD_WARNINGS.append(
                "ERROR",
                f"当前工作目录 .env（{runtime_env_path}）覆盖了项目根 .env"
                f"（{default_env_path}）的配置值；"
                "进程工作目录不受控时其中的 .env 可能注入非预期配置，请确认启动目录可信",
            )
        else:
            # 无项目根 .env 时 CWD .env 是唯一来源，属隐式加载。
            _BUILD_WARNINGS.append(
                "WARNING",
                f"已加载当前工作目录 .env（{runtime_env_path}）；"
                "进程工作目录不受控时其中的 .env 可能注入非预期配置，请确认启动目录可信",
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
    "http_allowed_origins": (_pick_optional_str_tuple, None),
    "request_state_secret_keys": (_pick_request_state_key_bytes, None),
}

# 合法的 overrides 键集合：字段名、CLI 简称别名与 api_key，未知键在构建期告警。
_KNOWN_OVERRIDE_KEYS = frozenset(
    {"api_key", *_FIELD_PICKERS} | {alias for _, alias in _FIELD_PICKERS.values() if alias}
)
