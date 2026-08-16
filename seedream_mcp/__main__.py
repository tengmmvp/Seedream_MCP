"""Seedream MCP 包主入口。

允许通过 python -m seedream_mcp 运行服务器。
"""

from .server import cli_main

if __name__ == "__main__":
    raise SystemExit(cli_main())
