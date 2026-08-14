"""Seedream MCP 工具包入口，按三层导出工具对外符号。

第一层为 impl 下的 ``handle_*`` 业务处理器，封装各工具的客户端调用与结果组装；第二层
为 runners 下的 ``run_*`` 适配器，作为 composition root 注入工作区边界后委托对应
handler；第三层为 core.schemas 下的输入模型，作为参数校验与 MCP inputSchema 的单一
来源。依赖方向为 core <- impl <- runners，本包仅做聚合再导出。

采用 PEP 562 的 ``__getattr__`` 延迟加载：首次访问导出名时才导入对应子模块，不连带
加载其余 impl、runners 与 schemas 子模块。``__all__`` 程序化派生
自 ``_LAZY_EXPORTS`` 的键，二者天然一致，无需手动维护。
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
    from .impl.browse_images import handle_browse_images  # noqa: F401
    from .impl.image_to_image import handle_image_to_image  # noqa: F401
    from .impl.multi_image_fusion import handle_multi_image_fusion  # noqa: F401
    from .impl.sequential_generation import (  # noqa: F401
        handle_sequential_generation,
    )
    from .impl.text_to_image import handle_text_to_image  # noqa: F401
    from .runners import (  # noqa: F401
        run_browse_images,
        run_image_to_image,
        run_multi_image_fusion,
        run_sequential_generation,
        run_text_to_image,
    )

# 延迟加载映射：导出名 -> (子模块相对名，子模块内属性名)
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # impl 业务处理器
    "handle_browse_images": (".impl.browse_images", "handle_browse_images"),
    "handle_image_to_image": (".impl.image_to_image", "handle_image_to_image"),
    "handle_multi_image_fusion": (".impl.multi_image_fusion", "handle_multi_image_fusion"),
    "handle_sequential_generation": (
        ".impl.sequential_generation",
        "handle_sequential_generation",
    ),
    "handle_text_to_image": (".impl.text_to_image", "handle_text_to_image"),
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

# 公开接口声明，程序化派生自 _LAZY_EXPORTS 的键以消除手动同步。
__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """按 PEP 562 延迟加载公开导出，首次访问时才导入对应子模块并缓存到模块字典。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
