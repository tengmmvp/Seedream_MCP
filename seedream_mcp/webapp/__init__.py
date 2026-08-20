"""Web 操作台模块：custom_route 注册、API 处理器与随包分发的静态资源。

transport 在 web_enabled 开启时懒导入本包完成路由注册与静态挂载；stdio 传输
与未开启 Web 的部署不加载本模块，行为零变化。
"""

from __future__ import annotations

from .routes import mount_web_static, register_web_routes

__all__ = ["mount_web_static", "register_web_routes"]
