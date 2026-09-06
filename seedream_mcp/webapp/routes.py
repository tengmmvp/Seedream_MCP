"""Web 操作台装配入口：custom_route 注册与静态资源挂载。

注册须在 streamable_http_app 调用之前完成，SDK 构造 app 时一次性拷贝自定义
路由引用；挂载则在 app 构造之后向 Starlette 活体路由表追加 Mount。两步的调用
点由 transport 的 _build_streamable_app 依序触发，时序约束在该调用处本地可见。

handler 按域拆分：meta（入口页/重定向/config-info）、generate（四个生成端点）、
gallery（图库浏览）、files（缩略图与原图）；新增端点时在对应域模块实现后于
下方注册表登记一行。
"""

from __future__ import annotations

import mimetypes
import os
import weakref
from typing import Any

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from ..utils.core.logs import get_logger
from . import constants
from .constants import (
    PAGE_SECURITY_HEADERS,
    WEB_API_BROWSE,
    WEB_API_CONFIG_INFO,
    WEB_API_GENERATE_IMAGE_TO_IMAGE,
    WEB_API_GENERATE_MULTI_IMAGE_FUSION,
    WEB_API_GENERATE_SEQUENTIAL_GENERATION,
    WEB_API_GENERATE_TEXT_TO_IMAGE,
    WEB_API_IMAGE,
    WEB_API_THUMBNAIL,
    WEB_INDEX_PATH,
    WEB_ROOT_PATH,
    WEB_STATIC_MOUNT_PATH,
)

logger = get_logger()

# Windows 注册表把 .svg 的 MIME 污染成 image/svg，img 标签只认 image/svg+xml。
mimetypes.add_type("image/svg+xml", ".svg")

# 注册守卫登记表：按 MCPServer 实例身份弱引用登记已完成注册的对象，宿主或测试
# 重造实例时守卫不误跳过新实例的注册；守卫命中路径同样复查路径交集，仅跳过
# 真正重复的注册。
_registered_servers: "weakref.WeakSet[Any]" = weakref.WeakSet()


class _GuardedStaticFiles(StaticFiles):
    """封禁页面文件直达并为直出补 nosniff 的静态资源应用。

    index 与 404 页须经 meta 域端点直出以携带 CSP 与 nosniff 安全头，StaticFiles
    直出会绕过该头，故页面文件在 GET/HEAD 服务路径上 404；其余方法到不了
    file_response，由 Starlette 以 405 拒绝，同样不出页面内容。封禁判定在
    file_response 按解析后的
    物理路径后缀执行，.html、.htm 与 .xhtml 均封且大小写不敏感：Starlette lookup_path 以
    realpath 定位文件，8.3 短名、大小写变体与尾随字符等别名族都解析到真实页面
    文件，请求路径后缀判定覆盖不了这些形态。其余静态资源按原行为直出，并统一补
    x-content-type-options: nosniff 阻断对 css/js/svg 的 MIME 嗅探。
    """

    def file_response(
        self,
        full_path: "str | os.PathLike[str]",
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        """物理路径以页面扩展名结尾时回 404，其余直出附 nosniff，304 分支同样覆盖。"""
        if os.fspath(full_path).lower().endswith((".html", ".htm", ".xhtml")):
            return Response(status_code=404)
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["x-content-type-options"] = PAGE_SECURITY_HEADERS["x-content-type-options"]
        return response


def register_web_routes() -> None:
    """向调用方 MCPServer 注册全部 Web 路由，按实例身份守卫并复查路径交集保持幂等。"""
    from ..resources import mcp
    from . import files, gallery, generate, meta

    # 路径交集复查依赖 SDK 私有路由表，getattr 探测缺失时告警并回退纯身份守卫；
    # 注册本身经 custom_route 公开 API 完成，不受探测结果影响。
    custom_routes = getattr(mcp, "_custom_starlette_routes", None)
    if custom_routes is None:
        logger.error(
            "SDK 私有路由表 mcp._custom_starlette_routes 缺失，跳过路径交集复查，"
            "请适配新版 MCP SDK"
        )
        existing_paths: set[str | None] = set()
    else:
        existing_paths = {getattr(route, "path", None) for route in custom_routes}
    routes: list[tuple[str, list[str], Any]] = [
        (WEB_ROOT_PATH, ["GET"], meta.web_root_redirect),
        (WEB_INDEX_PATH, ["GET"], meta.web_index),
        (WEB_API_CONFIG_INFO, ["GET"], meta.web_config_info),
        (WEB_API_GENERATE_TEXT_TO_IMAGE, ["POST"], generate.web_generate_text_to_image),
        (WEB_API_GENERATE_IMAGE_TO_IMAGE, ["POST"], generate.web_generate_image_to_image),
        (WEB_API_GENERATE_MULTI_IMAGE_FUSION, ["POST"], generate.web_generate_multi_image_fusion),
        (
            WEB_API_GENERATE_SEQUENTIAL_GENERATION,
            ["POST"],
            generate.web_generate_sequential_generation,
        ),
        (WEB_API_BROWSE, ["POST"], gallery.web_browse),
        (WEB_API_THUMBNAIL, ["GET"], files.web_thumbnail),
        (WEB_API_IMAGE, ["GET"], files.web_image),
    ]
    # 私有路由表不可得时无从复查路径，退回纯身份守卫；表可得时以循环内的
    # 逐路径跳过为唯一幂等机制，路由表被清空或截断自动补注册缺失项。
    if custom_routes is None:
        if mcp in _registered_servers:
            return
    for path, methods, handler in routes:
        if path in existing_paths:
            logger.warning("Web 路由 {} 已注册，跳过重复注册", path)
            continue
        mcp.custom_route(path, methods=methods, include_in_schema=False)(handler)
    _registered_servers.add(mcp)
    logger.info("Web 操作台路由已注册，共 {} 条", len(routes))


def mount_web_static(app: Any) -> None:
    """向 Starlette app 追加静态资源挂载与兜底 404 路由，幂等且目录缺失时跳过。

    兜底路由必须排在静态挂载之后：Starlette 按注册顺序匹配路径，先注册的
    ``/{path:path}`` 会吞掉全部静态资源请求。custom_route 注册表无法保证该
    顺序，其路由固定先于挂载进入路由表，故兜底在此处与挂载成对追加。
    """
    from starlette.routing import Mount, Route

    from .meta import web_not_found

    for route in getattr(app, "routes", []):
        if isinstance(route, Mount) and route.path == WEB_STATIC_MOUNT_PATH:
            return
    # STATIC_DIR 经模块属性访问而非导入期绑定，目录指向可在运行期整体替换。
    if not constants.STATIC_DIR.is_dir():
        logger.error("Web 静态资源目录不存在，跳过挂载: {}", constants.STATIC_DIR)
        return
    app.routes.append(
        Mount(
            WEB_STATIC_MOUNT_PATH,
            app=_GuardedStaticFiles(directory=str(constants.STATIC_DIR)),
        )
    )
    app.routes.append(
        Route(
            "/{path:path}",
            web_not_found,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            include_in_schema=False,
        )
    )
    logger.info("Web 静态资源已挂载于 {}，兜底 404 已就位", WEB_STATIC_MOUNT_PATH)
