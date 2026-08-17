"""MCP 工具适配器层，作为 composition root 组装 core 流水线与 impl 处理器。

每个 ``run_*`` 函数经 ``workspace_roots_scope`` 注入 MCP Roots 工作区边界，再将
pydantic 校验后的入参模型本身委托给对应 ``handle_*``，保持类型化流水线直至
core 层。本模块位于 tools/ 顶层而非 core/，使依赖方向为 core <- impl <- runners，
避免 core 反向依赖 impl 造成包级循环。四个生成工具共享 ``_run_generation_tool``
完成工作区边界与委托；图片浏览工具无 config 入参，单独直接委托。
"""

from __future__ import annotations

from typing import Awaitable, Callable, TypeVar

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult

from ..config import SeedreamConfig
from ..utils.io.io_path import workspace_roots_scope
from .core.schemas import (
    BrowseImagesInput,
    GenerationInputParams,
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

# 泛型参数绑定输入协议：handler 接受各自的具体输入模型，与传入 params 的具体类型一致，
# 避免 Callable 逆变要求 handler 接受任意协议实现。
_GenerationInputT = TypeVar("_GenerationInputT", bound=GenerationInputParams)

_GenerationHandler = Callable[
    [_GenerationInputT, SeedreamConfig, Context | None],
    Awaitable[CallToolResult],
]


async def _run_generation_tool(
    params: _GenerationInputT,
    config: SeedreamConfig,
    ctx: Context | None,
    handler: _GenerationHandler[_GenerationInputT],
) -> CallToolResult:
    """在工作区边界内将类型化入参模型委托给生成 handler。"""
    async with workspace_roots_scope(ctx):
        return await handler(params, config, ctx)


async def run_text_to_image(
    params: TextToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_text_to_image`` 处理文生图请求。"""
    return await _run_generation_tool(params, config, ctx, handle_text_to_image)


async def run_image_to_image(
    params: ImageToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_image_to_image`` 处理图文生图请求。"""
    return await _run_generation_tool(params, config, ctx, handle_image_to_image)


async def run_multi_image_fusion(
    params: MultiImageFusionInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_multi_image_fusion`` 处理多图融合请求。"""
    return await _run_generation_tool(params, config, ctx, handle_multi_image_fusion)


async def run_sequential_generation(
    params: SequentialGenerationInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_sequential_generation`` 处理组图输出请求。"""
    return await _run_generation_tool(params, config, ctx, handle_sequential_generation)


async def run_browse_images(
    params: BrowseImagesInput,
    ctx: Context | None = None,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_browse_images`` 处理图片浏览请求。"""
    async with workspace_roots_scope(ctx):
        return await handle_browse_images(params, ctx=ctx)
