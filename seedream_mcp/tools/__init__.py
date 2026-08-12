"""
Seedream MCP 工具模块入口。

本模块提供 Seedream MCP 工具的统一入口，整合图像生成、浏览、融合等核心功能，
包含业务处理器和运行器的导出，以及相应的数据模型定义。
"""

from __future__ import annotations

# 业务处理器导入
from .impl.browse_images import handle_browse_images
from .impl.image_to_image import handle_image_to_image
from .impl.multi_image_fusion import handle_multi_image_fusion
from .impl.sequential_generation import handle_sequential_generation
from .impl.text_to_image import handle_text_to_image

# 核心运行器导入
from .runners import (
    run_browse_images,
    run_image_to_image,
    run_multi_image_fusion,
    run_sequential_generation,
    run_text_to_image,
)

# 数据模型导入
from .core.schemas import (
    BrowseImagesInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)

# 公开接口定义
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
