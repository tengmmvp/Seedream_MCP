"""Seedream MCP 配置构建器：统一来源构建、全局单例与环境变量提供者注册。

配置构建经构建锁串行化、全局实例经独立双检锁惰性初始化，两锁分离避免嵌套
死锁；构建不向 os.environ 注入 .env 值，仅写入配置对象。配置模型位于 _model，
.env 读取与类型化取值机械位于 sources。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Mapping

from ..utils.core.errors import SeedreamConfigError
from ..utils.core.executors import register_cpu_offload_depth_provider
from ..utils.core.logs import get_logger
from ..utils.io.path import (
    clear_resolved_env_root_cache,
    register_data_root_provider,
    register_env_workspace_root_provider,
)
from ._model import SeedreamConfig, _first_invalid_request_state_key_message
from .sources import (
    _BUILD_WARNINGS,
    _FIELD_ENV_MAP,
    _FIELD_PICKERS,
    _KNOWN_OVERRIDE_KEYS,
    _decode_request_state_key_entries,
    _normalize_field_name_overrides,
    _pick_config_value,
    _read_env_values,
    _split_optional_str_tuple,
    env_family_prefixes,
    init_env_registry,
)


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


def build_config_from_sources(
    overrides: Mapping[str, object] | None = None,
    env_file: str | None = None,
) -> SeedreamConfig:
    """从统一来源构建配置对象，经 ``_config_build_lock`` 串行化，线程安全。

    Args:
        overrides: 调用方显式覆盖值，CLI 参数为典型来源。
        env_file: 可选 .env 文件路径，空串或纯空白视同未提供；未提供时先读项目根
            .env 再读当前工作目录 .env，后者覆盖前者。

    Raises:
        SeedreamConfigError: 配置文件不可读、缺少 API 密钥或配置项校验失败。
    """
    with _config_build_lock:
        _build_in_progress.active = True
        try:
            return _build_config_from_sources_unlocked(overrides, env_file)
        finally:
            _build_in_progress.active = False


def _build_config_from_sources_unlocked(
    overrides: Mapping[str, object] | None = None,
    env_file: str | None = None,
) -> SeedreamConfig:
    """构建配置对象但自身不加锁，由 :func:`build_config_from_sources` 持锁调用。"""
    pending_mark = _BUILD_WARNINGS.mark()
    try:
        return _build_config_unlocked_body(overrides, env_file)
    except BaseException:
        # 构建失败的告警不外泄给下一次成功构建的 drain。
        _BUILD_WARNINGS.rollback(pending_mark)
        raise


def _build_config_unlocked_body(
    overrides: Mapping[str, object] | None,
    env_file: str | None,
) -> SeedreamConfig:
    """构建配置对象的主体，告警回滚由调用方负责。"""
    override_values = _normalize_field_name_overrides(overrides or {})
    unknown_keys = sorted(map(str, override_values.keys() - _KNOWN_OVERRIDE_KEYS))
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
# 全局配置惰性初始化锁，与 _config_build_lock 分离，避免嵌套获取造成死锁。
_global_config_lock = threading.Lock()
# 构建进行中的线程本地标记，get_global_config 据此把构建期惰性构建转为配置错误，
# 防重入构建锁的单线程死锁。
_build_in_progress = threading.local()

_global_config: SeedreamConfig | None = None
# CLI 注入的活动配置，优先于 _global_config。
_active_config: SeedreamConfig | None = None


def get_global_config() -> SeedreamConfig:
    """获取全局配置实例，首次调用时惰性构建并经双检锁缓存。

    Raises:
        SeedreamConfigError: 配置构建进行中时触发惰性构建被拒绝，防重入死锁。
    """
    global _global_config
    if _global_config is not None:
        return _global_config
    if getattr(_build_in_progress, "active", False):
        # 构建期惰性构建会重入构建锁死锁，转配置错误由调用方回退。
        raise SeedreamConfigError("配置构建进行中，不允许触发全局配置惰性构建")
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

    变更同时使 path 的回退根缓存失效。
    """
    global _active_config
    with _global_config_lock:
        _active_config = config
        clear_resolved_env_root_cache()


def active_request_state_keys() -> tuple[bytes, ...] | None:
    """向 runtime 提供 requestState 密钥环的活动取值。

    活动配置已就绪时返回其密钥环；导入期配置未构建，仅按系统环境变量先行
    取密钥环，条目拆分与解码均复用配置构建的单点。.env 中的值由启动路径
    构建后经 rebind 校正；不构建完整配置，构建告警缓冲不为其写入，drain
    输出与启动期声明的配置来源一致。先行取值解码失败或未过强度与重复
    校验时按未配置处理，真正的配置错误由启动路径报告。

    Returns:
        解码后的密钥字节元组，未配置时为 None。
    """
    config = _active_config if _active_config is not None else _global_config
    if config is not None:
        return config.request_state_secret_keys
    try:
        keys = _decode_request_state_key_entries(
            _split_optional_str_tuple(os.getenv("SEEDREAM_REQUEST_STATE_KEYS"))
        )
    except SeedreamConfigError:
        return None
    # 环判定与配置构建共用同一谓词，不合法值按未配置处理，避免喂给 SDK 在导入期崩溃。
    if keys is not None and _first_invalid_request_state_key_message(keys) is not None:
        return None
    return keys


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


# 模块加载即注册，path 的位置求值经提供者取活动配置值。
register_env_workspace_root_provider(
    _make_env_location_provider("workspace_root", "SEEDREAM_WORKSPACE_ROOT")
)
register_data_root_provider(_make_env_location_provider("data_root", "SEEDREAM_DATA_ROOT"))


def _active_pool_concurrency() -> tuple[int, int] | None:
    """CPU 卸载池深提供者：返回活动配置的生成并发与图像预处理并发，配置不可构建时返回 None。"""
    try:
        config = get_active_config()
    except (SeedreamConfigError, OSError):
        # 配置构建失败不阻断池创建，回退交由 executors 侧兜底。
        return None
    return config.generate_concurrency, config.image_prepare_concurrency


# CPU 卸载池深经提供者取活动配置的生成并发与图像预处理并发，utils/core 不反向依赖本模块。
register_cpu_offload_depth_provider(_active_pool_concurrency)
# 从配置类反射派生字段环境变量映射与默认值表，取值机械经此读取。
init_env_registry(SeedreamConfig)

# 环境族前缀从已初始化的注册表派生导出，配置新增环境族时消费方自动跟随。
ENV_FAMILY_PREFIXES: tuple[str, ...] = env_family_prefixes()
