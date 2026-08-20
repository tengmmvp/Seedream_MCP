"""Web 操作台装配入口：custom_route 注册与静态资源挂载。

注册须在 streamable_http_app 调用之前完成，SDK 构造 app 时一次性拷贝自定义
路由引用；挂载则在 app 构造之后向 Starlette 活体路由表追加 Mount。两步的调用
点由 transport 的 _build_streamable_app 依序触发，时序约束在该调用处本地可见。

handler 按域拆分：meta（入口页/重定向/config-info）、generate（四个生成端点）、
gallery（图库浏览）、files（缩略图与原图）；新增端点时在对应域模块实现后于
下方注册表登记一行。
"""

from __future__ import annotations

from typing import Any

from ..utils.core.logs import get_logger
from . import constants
from .constants import (
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

# 模块级注册守卫：测试反复构建 app 会多次调用注册，SDK 侧无去重，重复注册使
# 同一路由匹配两次；守卫状态与路由表不同步的残留由注册时的路径交集复查兜底。
_routes_registered = False


def register_web_routes() -> None:
    """向 MCPServer 单例注册全部 Web 路由，重复调用经守卫与路径复查保持幂等。"""
    global _routes_registered
    if _routes_registered:
        return

    from ..resources import mcp
    from . import files, gallery, generate, meta

    existing_paths = {getattr(route, "path", None) for route in mcp._custom_starlette_routes}
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
    for path, methods, handler in routes:
        if path in existing_paths:
            logger.warning("Web 路由 {} 已注册，跳过重复注册", path)
            continue
        mcp.custom_route(path, methods=methods, include_in_schema=False)(handler)
    _routes_registered = True
    logger.info("Web 操作台路由已注册，共 {} 条", len(routes))


def mount_web_static(app: Any) -> None:
    """向 Starlette app 追加静态资源挂载与兜底 404 路由，幂等且目录缺失时跳过。

    兜底路由必须排在静态挂载之后：Starlette 按注册顺序匹配路径，先注册的
    ``/{path:path}`` 会吞掉全部静态资源请求。custom_route 注册表无法保证该
    顺序（其路由固定先于挂载进入路由表），故兜底在此处与挂载成对追加。
    """
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    from .meta import web_not_found

    for route in getattr(app, "routes", []):
        if isinstance(route, Mount) and route.path == WEB_STATIC_MOUNT_PATH:
            return
    # STATIC_DIR 经模块属性访问而非导入期绑定，目录指向可在运行期整体替换。
    if not constants.STATIC_DIR.is_dir():
        logger.error("Web 静态资源目录不存在，跳过挂载: {}", constants.STATIC_DIR)
        return
    app.routes.append(
        Mount(WEB_STATIC_MOUNT_PATH, app=StaticFiles(directory=str(constants.STATIC_DIR)))
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
