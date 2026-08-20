"""Web 操作台元信息端点：入口页、根路径重定向与 config-info。

config-info 是前端的启动面：模型能力与尺寸档位同源 model_capabilities，兼作
鉴权探测端点——配置了令牌时未携带 Authorization 的请求在此得到 401，前端据此
弹出令牌输入。
"""

from __future__ import annotations

from dataclasses import asdict

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from ..config import get_active_config
from ..tools.core._helpers import _resolve_default_base_dir
from ..utils.core.errors import SeedreamValidationError
from ..utils.model.model_capabilities import MODEL_ALIASES, get_model_capabilities
from ..version import __version__
from . import _shared
from .constants import STATIC_DIR, WEB_API_PREFIX, WEB_INDEX_PATH


async def web_index(_request: Request) -> Response:
    """返回 Web 操作台入口页。"""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


async def web_not_found(request: Request) -> Response:
    """兜底 404：API 前缀回统一 JSON 错误，其余路径回风格化 404 页。"""
    if request.url.path.startswith(WEB_API_PREFIX + "/"):
        return _shared.error_json("not_found", "接口不存在", 404)
    return FileResponse(STATIC_DIR / "404.html", status_code=404, media_type="text/html")


async def web_root_redirect(_request: Request) -> Response:
    """把根路径重定向到操作台入口。"""
    return RedirectResponse(WEB_INDEX_PATH, status_code=307)


async def web_config_info(_request: Request) -> Response:
    """返回前端所需的模型能力、默认值与保存根信息。

    保存根不可解析时回 None，前端隐藏图库入口。
    """
    config = get_active_config()
    models = []
    for alias, model_id in MODEL_ALIASES.items():
        capabilities = asdict(get_model_capabilities(model_id))
        # frozenset 与 set 不可 JSON 序列化，档位转有序列表。
        capabilities["allowed_presets"] = sorted(capabilities["allowed_presets"])
        models.append({"alias": alias, "model_id": model_id, **capabilities})
    try:
        save_root = str(_resolve_default_base_dir(config))
    except SeedreamValidationError:
        save_root = None
    return JSONResponse(
        {
            "server_version": __version__,
            "model_id": config.model_id,
            "default_size": config.default_size,
            "models": models,
            "save_root": save_root,
            "auto_save_enabled": config.auto_save_enabled,
            "preview_enabled": config.preview_enabled,
        }
    )
