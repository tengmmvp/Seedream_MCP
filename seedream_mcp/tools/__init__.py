"""Seedream MCP 工具包入口，聚合再导出 runners 适配器与输入模型。

impl 的 ``handle_*`` 处理器封装各工具的客户端调用与结果组装，经直接子模块路径
导入消费，不在包门面重导出；runners 的 ``run_*`` 适配器作为 composition root
注入工作区边界后委托 handler；core.schemas 的输入模型是参数校验与 MCP
inputSchema 的单一来源。依赖方向为 core <- impl <- runners。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .core.schemas import (  # noqa: F401
        BrowseImagesInput,
        ImageToImageInput,
        MultiImageFusionInput,
        SequentialGenerationInput,
        TextToImageInput,
    )
    from .runners import (  # noqa: F401
        run_browse_images,
        run_image_to_image,
        run_multi_image_fusion,
        run_sequential_generation,
        run_text_to_image,
    )

# 延迟加载映射：导出名 -> (子模块相对名，子模块内属性名)
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # runners 适配器
    "run_browse_images": (".runners", "run_browse_images"),
    "run_image_to_image": (".runners", "run_image_to_image"),
    "run_multi_image_fusion": (".runners", "run_multi_image_fusion"),
    "run_sequential_generation": (".runners", "run_sequential_generation"),
    "run_text_to_image": (".runners", "run_text_to_image"),
    # core.schemas 输入模型
    "BrowseImagesInput": (".core.schemas", "BrowseImagesInput"),
    "ImageToImageInput": (".core.schemas", "ImageToImageInput"),
    "MultiImageFusionInput": (".core.schemas", "MultiImageFusionInput"),
    "SequentialGenerationInput": (".core.schemas", "SequentialGenerationInput"),
    "TextToImageInput": (".core.schemas", "TextToImageInput"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """按 PEP 562 延迟加载公开导出，首次访问时导入对应子模块。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """补全 dir() 结果，纳入尚未触发导入的延迟导出公开名。"""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
