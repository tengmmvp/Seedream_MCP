"""
MCP工具适配器辅助模块
"""

from __future__ import annotations

from mcp.server.fastmcp import Context
from mcp.types import CallToolResult

from ...config import SeedreamConfig
from ...utils.path_utils import workspace_roots_scope
from .schemas import (
    BrowseImagesInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from ..impl.browse_images import handle_browse_images
from ..impl.image_to_image import handle_image_to_image
from ..impl.multi_image_fusion import handle_multi_image_fusion
from ..impl.sequential_generation import handle_sequential_generation
from ..impl.text_to_image import handle_text_to_image


async def run_text_to_image(
    params: TextToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """
    执行文生图生成工具

    Args:
        params: 文生图生成的已验证参数对象。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_text_to_image(
            params.model_dump(exclude_none=True),
            config=config,
            ctx=ctx,
        )


async def run_image_to_image(
    params: ImageToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """
    执行图文生图工具

    Args:
        params: 图文生图的已验证参数对象。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_image_to_image(
            params.model_dump(exclude_none=True),
            config=config,
            ctx=ctx,
        )


async def run_multi_image_fusion(
    params: MultiImageFusionInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """
    执行多图像融合工具

    Args:
        params: 多图像融合的已验证参数对象。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_multi_image_fusion(
            params.model_dump(exclude_none=True),
            config=config,
            ctx=ctx,
        )


async def run_sequential_generation(
    params: SequentialGenerationInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """
    执行组图输出工具

    Args:
        params: 组图输出的已验证参数对象。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_sequential_generation(
            params.model_dump(exclude_none=True),
            config=config,
            ctx=ctx,
        )


async def run_browse_images(
    params: BrowseImagesInput,
    ctx: Context | None = None,
) -> CallToolResult:
    """
    执行图像浏览工具

    Args:
        params: 图像浏览的已验证参数对象。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_browse_images(
            params.model_dump(exclude_none=True),
            ctx=ctx,
        )
