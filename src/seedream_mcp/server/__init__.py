"""Seedream MCP 服务器包门面：再导出服务器公共入口。

注册主体位于 _registration，MCPServer 实例与共享资源生命周期位于 runtime，
skill 读取机制位于 skills，启动编排位于 bootstrap，参数解析位于 cli，传输层
位于 transport。
"""

from ._registration import (  # noqa: F401
    _STYLE_PROMPT_PREFIX as _STYLE_PROMPT_PREFIX,
    _workspace_roots_dependency as _workspace_roots_dependency,
    models_info_resource as models_info_resource,
    server_info_resource as server_info_resource,
    workspace_roots_resource as workspace_roots_resource,
)
from .bootstrap import cli_main as cli_main  # noqa: F401
from .runtime import (  # noqa: F401
    SERVER_NAME as SERVER_NAME,
    SERVER_VERSION as SERVER_VERSION,
    app_lifespan as app_lifespan,
    mcp as mcp,
)

__all__ = [
    "SERVER_NAME",
    "SERVER_VERSION",
    "app_lifespan",
    "cli_main",
    "mcp",
    "models_info_resource",
    "server_info_resource",
    "workspace_roots_resource",
]
