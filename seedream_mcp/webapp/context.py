"""Web 请求的 MCP 上下文替身：借用 lifespan 共享的 client 与下载管理器。

core 流水线经鸭子类型探测取共享资源，两处探测点为本替身的形态契约：

- ``core/parallel.py`` 的 get_lifespan_resource：经 ``ctx.request_context.
  lifespan_context`` 字典取 LIFESPAN_KEY_CLIENT / LIFESPAN_KEY_DOWNLOAD_MANAGER，
  取值路径上的属性缺失等异常捕获后回退 None；
- ``core/_helpers.py`` 的 safe_report_progress：调用 ``ctx.report_progress``，
  失败静默，故替身提供 no-op 实现即可。

Web 请求没有 MCP 会话上下文，共享资源单例在堂时本模块构造等价替身，使生成
请求复用同一 HTTP 连接池与下载并发上限。替身仅做借用，不改变引用计数：uvicorn
serve 期间 lifespan 在堂保证单例存活，serve 之外的窗口返回 None 由流水线回退
每请求新建 client。SDK 升级若调整上述探测路径，本替身随之失效并自动回退，不
产生错误行为，仅损失连接池复用。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ..config import LIFESPAN_KEY_CLIENT, LIFESPAN_KEY_DOWNLOAD_MANAGER


class _WebRequestContextStub:
    """满足 core 流水线鸭子类型探测的最小上下文替身。

    Attributes:
        request_context: 携带 lifespan_context 字典的命名空间，
            get_lifespan_resource 经属性访问取共享 client 与 download_manager。
    """

    def __init__(self, lifespan_context: dict[str, Any]) -> None:
        self.request_context = SimpleNamespace(lifespan_context=lifespan_context)

    async def report_progress(self, *_args: Any, **_kwargs: Any) -> None:
        """进度上报空实现：Web 等待式交互不消费进度，safe_report_progress 静默通过。"""


def build_web_request_context() -> _WebRequestContextStub | None:
    """共享资源单例在堂时返回借用其 client 与下载管理器的上下文替身。

    返回 None 表示资源不在堂，即 uvicorn serve 之外的调用窗口，调用方以
    ctx=None 走流水线回退路径，按请求新建 client。读取的是模块属性而非
    import 期绑定，跟随 resources 单例的重建与退役。
    """
    from .. import resources

    resource = resources._active_resource
    if resource is None:
        return None
    return _WebRequestContextStub(
        {
            LIFESPAN_KEY_CLIENT: resource.client,
            LIFESPAN_KEY_DOWNLOAD_MANAGER: resource.download_manager,
        }
    )
