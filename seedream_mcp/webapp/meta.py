"""Web 操作台元信息端点：入口页、重定向与 config-info。

config-info 是前端的启动面：模型能力与尺寸档位同源 model_capabilities，兼作
鉴权探测端点——配置了令牌时未携带 Authorization 的请求在此得到 401，前端据此
弹出令牌输入。
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from ..config import get_active_config
from ..tools.core._helpers import _resolve_default_base_dir
from ..utils.core.errors import SeedreamValidationError
from ..utils.model.model_capabilities import MODEL_ALIASES, get_model_capabilities
from ..version import __version__
from . import _shared, constants
from .constants import PAGE_SECURITY_HEADERS, WEB_API_PREFIX, WEB_INDEX_PATH

# 模型能力清单缓存：MODEL_ALIASES 为进程级静态数据，首次构建后跨请求复用。
_MODELS_PAYLOAD: list[dict[str, object]] | None = None


def _models_payload() -> list[dict[str, object]]:
    """构建模型能力清单，进程级缓存首次结果供 config-info 复用。"""
    global _MODELS_PAYLOAD
    if _MODELS_PAYLOAD is None:
        models: list[dict[str, object]] = []
        for alias, model_id in MODEL_ALIASES.items():
            capabilities = asdict(get_model_capabilities(model_id))
            # frozenset 与 set 不可 JSON 序列化，档位转有序列表。
            capabilities["allowed_presets"] = sorted(capabilities["allowed_presets"])
            models.append({"alias": alias, "model_id": model_id, **capabilities})
        _MODELS_PAYLOAD = models
    return _MODELS_PAYLOAD


async def web_index(_request: Request) -> Response:
    """返回 Web 操作台入口页，附 CSP 与 nosniff 安全头。"""
    # STATIC_DIR 经模块属性访问而非导入期绑定，目录指向可在运行期整体替换。
    return FileResponse(
        constants.STATIC_DIR / "index.html",
        media_type="text/html",
        headers=PAGE_SECURITY_HEADERS,
    )


async def web_not_found(request: Request) -> Response:
    """兜底 404：尾斜杠路径先 307 到去尾斜杠形态，其余按前缀分流错误页。

    兜底路由会吞掉 Starlette 的 redirect_slashes 语义，使 /mcp/、/web/ 落到
    404；此处按同语义重定向，路径每跳至少短一个字符故无循环。去尾斜杠后仍以
    // 开头的协议相对形态是开放重定向目标，不重定向，落入后续 404 分支。API
    前缀回统一 JSON 错误，其余路径回附安全头的风格化 404 页。
    """
    path = request.url.path
    if path != "/" and path.endswith("/"):
        trimmed = path.rstrip("/")
        if trimmed and not trimmed.startswith("//"):
            return RedirectResponse(trimmed, status_code=307)
    if path.startswith(WEB_API_PREFIX + "/"):
        return _shared.error_json("not_found", "接口不存在", 404)
    return FileResponse(
        constants.STATIC_DIR / "404.html",
        status_code=404,
        media_type="text/html",
        headers=PAGE_SECURITY_HEADERS,
    )


async def web_root_redirect(_request: Request) -> Response:
    """把根路径重定向到操作台入口。"""
    return RedirectResponse(WEB_INDEX_PATH, status_code=307)


async def web_config_info(_request: Request) -> Response:
    """返回前端所需的模型能力、默认值与保存根可用性。

    模型清单取进程级缓存；保存根含 Path.resolve 文件系统调用，经 to_thread
    下沉与 files 域同口径。保存根仅回传可用性布尔，不向浏览器泄露服务器绝对
    路径；不可用时前端隐藏图库入口。
    """
    config = get_active_config()
    try:
        await asyncio.to_thread(_resolve_default_base_dir, config)
    except SeedreamValidationError:
        save_root_available = False
    else:
        save_root_available = True
    return JSONResponse(
        {
            "server_version": __version__,
            "model_id": config.model_id,
            "default_size": config.default_size,
            "models": _models_payload(),
            "save_root_available": save_root_available,
            "auto_save_enabled": config.auto_save_enabled,
            "preview_enabled": config.preview_enabled,
        }
    )
