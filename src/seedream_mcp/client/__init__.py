"""Seedream MCP 客户端包门面：再导出 SeedreamClient 公共入口。

API 客户端实现位于 _api 模块，HTTP 传输机械位于 _http，批内共享请求计划位于
request_plan。
"""

from ._api import SeedreamClient as SeedreamClient

__all__ = ["SeedreamClient"]
