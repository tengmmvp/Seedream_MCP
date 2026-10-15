"""Seedream MCP 配置包门面：再导出配置公共入口。

配置模型位于 _model，构建编排与全局单例位于 _builder，.env 读取与类型化取值
机械位于 sources，Host 条目语法内核位于 host_syntax。
"""

from ._builder import (
    ENV_FAMILY_PREFIXES as ENV_FAMILY_PREFIXES,
    active_request_state_keys as active_request_state_keys,
    build_config_from_sources as build_config_from_sources,
    drain_pending_build_warnings as drain_pending_build_warnings,
    get_active_config as get_active_config,
    get_global_config as get_global_config,
    set_active_config as set_active_config,
)
from ._model import (
    DEFAULT_HTTP_HOST as DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PORT as DEFAULT_HTTP_PORT,
    HTTP_AUTH_TOKEN_MIN_LENGTH as HTTP_AUTH_TOKEN_MIN_LENGTH,
    LIFESPAN_KEY_CLIENT as LIFESPAN_KEY_CLIENT,
    LIFESPAN_KEY_CONFIG as LIFESPAN_KEY_CONFIG,
    LIFESPAN_KEY_DOWNLOAD_MANAGER as LIFESPAN_KEY_DOWNLOAD_MANAGER,
    SeedreamConfig as SeedreamConfig,
)

# as 同名形态为显式再导出，测试侧经 config 单源取仓库根。
from .sources import PROJECT_ROOT as PROJECT_ROOT  # noqa: F401

__all__ = [
    "DEFAULT_HTTP_HOST",
    "DEFAULT_HTTP_PORT",
    "ENV_FAMILY_PREFIXES",
    "HTTP_AUTH_TOKEN_MIN_LENGTH",
    "LIFESPAN_KEY_CLIENT",
    "LIFESPAN_KEY_CONFIG",
    "LIFESPAN_KEY_DOWNLOAD_MANAGER",
    "PROJECT_ROOT",
    "SeedreamConfig",
    "active_request_state_keys",
    "build_config_from_sources",
    "drain_pending_build_warnings",
    "get_active_config",
    "get_global_config",
    "set_active_config",
]
