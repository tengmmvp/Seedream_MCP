"""包主入口，支持 python -m seedream_mcp 启动服务器。"""

from .server import cli_main

if __name__ == "__main__":
    raise SystemExit(cli_main())
