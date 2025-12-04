#!/usr/bin/env python3
"""
Seedream MCP工具 - 主入口文件

启动Seedream MCP服务器。
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from seedream_mcp.server import main


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n服务器已停止")
        sys.exit(0)
    except Exception as e:
        print(f"服务器启动失败: {e}")
        sys.exit(1)