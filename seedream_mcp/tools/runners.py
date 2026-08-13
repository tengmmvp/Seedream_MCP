"""MCP 工具适配器层，作为 composition root 组装 core 流水线与 impl 处理器。

每个 ``run_*`` 函数经 ``workspace_roots_scope`` 注入 MCP Roots 工作区边界，再将经
pydantic 校验的入参 model_dump 后委托给对应 ``handle_*``。本模块位于 tools/ 顶层而非
core/，使依赖方向为 core <- impl <- runners，避免 core 反向依赖 impl 造成包级循环。
四个生成工具共享 ``_run_generation_tool`` 完成工作区边界与 model_dump 委托；图片浏览工具
无 config 入参，单独直接委托。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context
from mcp.types import CallToolResult
from pydantic import BaseModel

from ..config import SeedreamConfig
from ..utils.path_utils import workspace_roots_scope
from .core.schemas import (
    BrowseImagesInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from .impl.browse_images import handle_browse_images
from .impl.image_to_image import handle_image_to_image
from .impl.multi_image_fusion import handle_multi_image_fusion
from .impl.sequential_generation import handle_sequential_generation
from .impl.text_to_image import handle_text_to_image

_GenerationHandler = Callable[
    [dict[str, Any], SeedreamConfig, Context | None],
    Awaitable[CallToolResult],
]


async def _run_generation_tool(
    params: BaseModel,
    config: SeedreamConfig,
    ctx: Context | None,
    handler: _GenerationHandler,
) -> CallToolResult:
    """在工作区边界内将入参 model_dump 后委托给生成 handler。"""
    async with workspace_roots_scope(ctx):
        return await handler(params.model_dump(exclude_none=True), config, ctx)


async def run_text_to_image(
    params: TextToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """文生图工具的 composition root 入口，委托 ``handle_text_to_image``。"""
    return await _run_generation_tool(params, config, ctx, handle_text_to_image)


async def run_image_to_image(
    params: ImageToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """图文生图工具的 composition root 入口，委托 ``handle_image_to_image``。"""
    return await _run_generation_tool(params, config, ctx, handle_image_to_image)


async def run_multi_image_fusion(
    params: MultiImageFusionInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """多图融合工具的 composition root 入口，委托 ``handle_multi_image_fusion``。"""
    return await _run_generation_tool(params, config, ctx, handle_multi_image_fusion)


async def run_sequential_generation(
    params: SequentialGenerationInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """组图输出工具的 composition root 入口，委托 ``handle_sequential_generation``。"""
    return await _run_generation_tool(params, config, ctx, handle_sequential_generation)


async def run_browse_images(
    params: BrowseImagesInput,
    ctx: Context | None = None,
) -> CallToolResult:
    """图片浏览工具的 composition root 入口，委托 ``handle_browse_images``。"""
    async with workspace_roots_scope(ctx):
        return await handle_browse_images(
            params.model_dump(exclude_none=True),
            ctx=ctx,
        )
