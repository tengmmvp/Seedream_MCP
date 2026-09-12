"""Web 操作台常量：路由路径、鉴权豁免表与静态资源目录。

本模块仅依赖标准库，供 transport 在装配 Bearer 豁免路径时懒导入，避免传输层
反向依赖 webapp 的 starlette 消息面。
"""

from __future__ import annotations

from pathlib import Path

# index 页 custom_route 路径，也是浏览器访问 Web 操作台的入口地址。
WEB_INDEX_PATH = "/web"

# 根路径重定向端点：浏览器敲域名根路径时直接落到操作台，仅 Web 开启时注册。
WEB_ROOT_PATH = "/"

# 静态资源挂载路径与对应 URL 前缀，前缀带尾斜杠用于前缀匹配。
WEB_STATIC_MOUNT_PATH = "/web/static"
WEB_STATIC_URL_PREFIX = "/web/static/"

# Web API 统一前缀，前缀下全部端点要求 Bearer 令牌，不做任何豁免。
WEB_API_PREFIX = "/web/api"

# Bearer 中间件的静态页面豁免表：index 页与根路径重定向精确匹配、静态资源前缀
# 匹配；重定向响应本身无数据，豁免后令牌部署下浏览器敲根路径可直接落页。
WEB_EXEMPT_EXACT_PATHS: frozenset[str] = frozenset({WEB_INDEX_PATH, WEB_ROOT_PATH})
WEB_EXEMPT_PATH_PREFIXES: tuple[str, ...] = (WEB_STATIC_URL_PREFIX,)

# 静态资源目录随包分发，消费方（meta 与 routes）统一经模块属性
# constants.STATIC_DIR 取用，目录指向因此可在运行期整体替换。
STATIC_DIR = Path(__file__).resolve().parent / "static"

# 服务端渲染的静态页面（入口页与 404 页）统一携带的安全响应头：CSP 把脚本与
# 数据加载收敛到同源，图片另放开 blob/data/https 供生成结果与预览展示，外链
# 图片经 fetch 转 blob 装载需放开 connect-src 的 https，内联样式为页面内嵌
# <style> 保留，object-src 禁插件内容，base-uri 与 form-action 不回退
# default-src 须单独声明，frame-ancestors 拒绝跨站 iframe 嵌入；nosniff 阻断
# MIME 嗅探；cache-control 要求逐次回源验证，包升级后无陈旧窗口，与静态直出同口径。
PAGE_SECURITY_HEADERS: dict[str, str] = {
    "content-security-policy": (
        "default-src 'self'; "
        "connect-src 'self' https:; "
        "script-src 'self'; "
        "object-src 'none'; "
        "img-src 'self' blob: data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self'"
    ),
    "cache-control": "no-cache",
    "x-content-type-options": "nosniff",
}

# 静态直出响应统一携带的安全响应头：/web/static/ 整体免鉴权且目录可运行期
# 替换，CSP 收敛脚本来源并禁插件与跨站嵌入，阻断 svg 直出构成的同源脚本面；
# nosniff 阻断 MIME 嗅探；cache-control 要求逐次回源验证，包升级后无陈旧窗口。
STATIC_SECURITY_HEADERS: dict[str, str] = {
    "cache-control": "no-cache",
    "content-security-policy": (
        "default-src 'self'; " "script-src 'self'; " "object-src 'none'; " "frame-ancestors 'self'"
    ),
    "x-content-type-options": "nosniff",
}

# 各 API 端点路径常量，routes 注册与测试断言共用单一来源。
WEB_API_CONFIG_INFO = f"{WEB_API_PREFIX}/config-info"
WEB_API_GENERATE_TEXT_TO_IMAGE = f"{WEB_API_PREFIX}/generate/text-to-image"
WEB_API_GENERATE_IMAGE_TO_IMAGE = f"{WEB_API_PREFIX}/generate/image-to-image"
WEB_API_GENERATE_MULTI_IMAGE_FUSION = f"{WEB_API_PREFIX}/generate/multi-image-fusion"
WEB_API_GENERATE_SEQUENTIAL_GENERATION = f"{WEB_API_PREFIX}/generate/sequential-generation"
WEB_API_BROWSE = f"{WEB_API_PREFIX}/browse"
WEB_API_THUMBNAIL = f"{WEB_API_PREFIX}/thumbnail"
WEB_API_IMAGE = f"{WEB_API_PREFIX}/image"
