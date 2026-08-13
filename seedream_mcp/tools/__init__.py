"""Seedream MCP 工具包入口，按三层导出工具对外符号。

第一层为 impl 下的 ``handle_*`` 业务处理器，封装各工具的客户端调用与结果组装；第二层
为 runners 下的 ``run_*`` 适配器，作为 composition root 注入工作区边界后委托对应
handler；第三层为 core.schemas 下的输入模型，作为参数校验与 MCP inputSchema 的单一
来源。依赖方向为 core <- impl <- runners，本包仅做聚合再导出。
"""

from __future__ import annotations

from .impl.browse_images import handle_browse_images
from .impl.image_to_image import handle_image_to_image
from .impl.multi_image_fusion import handle_multi_image_fusion
from .impl.sequential_generation import handle_sequential_generation
from .impl.text_to_image import handle_text_to_image

from .runners import (
    run_browse_images,
    run_image_to_image,
    run_multi_image_fusion,
    run_sequential_generation,
    run_text_to_image,
)

from .core.schemas import (
    BrowseImagesInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)

__all__ = [
    # 业务处理器
    "handle_browse_images",
    "handle_image_to_image",
    "handle_multi_image_fusion",
    "handle_sequential_generation",
    "handle_text_to_image",
    # 核心运行器
    "run_browse_images",
    "run_image_to_image",
    "run_multi_image_fusion",
    "run_sequential_generation",
    "run_text_to_image",
    # 数据模型
    "BrowseImagesInput",
    "ImageToImageInput",
    "MultiImageFusionInput",
    "SequentialGenerationInput",
    "TextToImageInput",
]
